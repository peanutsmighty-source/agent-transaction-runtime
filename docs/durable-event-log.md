# Durable Event Log 与 Reducer

## 它解决什么问题

现有 `TraceWriter` 记录模型请求、Context、工具和错误，适合回答“Agent 为什么这样运行”。但 `AgentLoop.run()` 每次都会新建内存中的 `AgentState`；进程退出后，Runtime 没有可重放协议来回答“任务现在进行到哪里”。

本轮增加最小 Durable Event Log 基线：领域事件保存会改变任务状态的事实，Reducer 按顺序把事件归约成 `DurableTaskState`。相同事件流必须产生相同状态：

```text
DomainEvent[] -> replay_domain_events() -> DurableTaskState
```

这和 Trace 是两个合同。Trace 面向调试，可以记录 token、延迟和 Context 视图；Domain Event 面向恢复，必须有 run ID、连续 sequence、schema version、稳定类型和完整数据。两者以后可以由同一个 Event Bus 分发，但不能因为当前 trace 文件名叫 `events.jsonl` 就认为它已经可恢复。

## 当前数据模型

`DomainEvent` 信封包含：

```text
event_id / run_id / sequence / schema_version / type / data / timestamp
```

当前领域事件支持创建任务、接受计划、开始和完成计划节点、增加和解决 blocker、设置下一动作。`PLAN_ACCEPTED` 只接受版本化 `TaskPlan`：plan 有稳定 ID，`PlanNodeSpec` 有节点 ID、依赖和节点级 acceptance criteria。接受后才转换成带运行状态的 `TaskNode` 并进入 `DurableTaskState.plan`。因此 checkpoint 也会包含 plan ID、节点状态、依赖、验收条件和完成证据；它不是从原始对话自由摘要出来的文本。

Reducer 是纯状态转换：它返回新的不可变 State，不修改旧 State。它拒绝 sequence 间隙、run ID 混用、重复创建任务、循环依赖、依赖未完成就启动节点，以及未启动就宣布节点完成。

## JSONL Store

`JsonlDomainEventStore` 是最小的单 run、单 writer、append-only 教学实现：

- 每个文件只接受一个 run；
- sequence 必须从 1 连续递增；
- 完全相同的重复 append 按幂等成功处理；
- 相同 ID 或 sequence 但内容不同会拒绝；
- 每次 append 后 flush + fsync；
- 读取时拒绝损坏 JSON、跨 run、sequence 间隙和重复 event ID。

## Checkpoint 与增量重放

`TaskCheckpoint` 不是对话摘要，而是完整 `DurableTaskState` 的版本化快照，因此保存 goal、acceptance criteria、plan 节点及其依赖/状态/证据、当前节点、blocker 和下一动作。Metadata 保存 checkpoint/run/事件 sequence、workspace revision、schema version、时间和 checksum。

`JsonTaskCheckpointStore` 为单 run 保存按 sequence 编号的 JSON：先写同目录临时文件并 flush + fsync，再以 `os.replace` 原子替换成正式文件。相同 checkpoint 重复保存按幂等处理，编号相同但内容不同则拒绝。载入时重新计算 checksum，因此手工修改 Task State 会被发现。

`replay_from_checkpoint()` 从快照 State 开始，只接受 checkpoint 已覆盖 sequence 之后的连续事件。测试已证明：

```text
full replay(events 1..6)
== checkpoint(events 1..4) + delta replay(events 5..6)
```

这说明 Checkpoint 能加速状态重建，但尚未验证 workspace 与外部系统仍和快照一致。

## Durable Task Session

直接要求每个调用者手工创建 sequence 和按正确顺序调用 Store/Reducer 很容易出错。`DurableTaskSession` 因此提供最小协调层：

```text
构造下一个 DomainEvent
        ↓
Reducer 计算候选 State，并验证转换是否合法
        ↓
Event Store append + fsync
        ↓
发布预先算好的新 DurableTaskState 到内存
```

Reducer 本身是纯函数，所以可以在写日志前安全计算候选 State。如果验证失败，非法事件不会污染日志；如果事件落盘后、内存 State 发布前进程崩溃，恢复时会从 Event Store 重放该事件。Session 支持接受 plan、开始/完成节点、blocker、next action、保存 checkpoint，以及从最新快照加后续事件恢复；没有快照时退回完整 replay。

这是持久化状态机的独立应用服务，还不是 AgentLoop 接线。当前 AgentLoop 仍使用原有 `AgentState` 和 Trace，CLI 也没有 `--resume`。

## Plan、Receipt 和 Resume 合同

直接把任意字典当 Plan、把任意字符串当完成证据、让 Runtime 猜测恢复目标，会产生三类错误：计划含循环或不稳定节点、节点引用不存在/失败的证据、恢复了同名但错误的任务或 workspace。

当前最小合同把它们拆开：

```text
TaskPlan / PlanNodeSpec
    -> 描述准备做什么、依赖和如何验收

ExecutionReceipt
    -> 描述某次工具、验证、workspace 修改或外部动作实际观察到什么

ResumeRequest
    -> 描述调用者明确要恢复的 run/task/workspace，以及是否强制要求 checkpoint
```

`JsonReceiptStore` 为每个 run 保存不可变 JSON receipt。Receipt 带 run/task/node、类型、成功状态、证据引用、时间和 checksum。节点完成前，Session 先验证 receipt 属于当前 run/task/node 且状态为 succeeded；节点声明 acceptance criteria 时，还必须至少有一张 verification receipt。随后 Session 验证完成状态转换、保存 receipt，最后 append 完成事件。恢复时会重新加载每个已引用 receipt；缺失、损坏、归属错误或验收证据类型错误都会失败。

顺序选择是有意的：如果保存 receipt 后、写完成事件前崩溃，最多留下没有被引用的孤儿 receipt，任务不会被错误标成完成；反过来先写完成事件，可能得到“已经完成但证据不存在”的危险状态。当前还没有跨 Receipt Store 与 Event Store 的事务，孤儿清理和幂等副作用协调属于后续边界。

`ResumeRequest` 会核对 event/checkpoint/receipt store 的 run ID、重放后的 task ID，以及调用者提供的 workspace revision。调用者要求 checkpoint 时不会静默退回完整 replay。它目前是 Python 层合同，不是 CLI Resume；workspace revision 也只是精确相等检查，尚不会自动 reconcile。

由于 Task State 增加 plan ID/节点验收条件，Domain Event 与 Checkpoint schema 已升到 v2。当前没有 v1 -> v2 migration，因此旧教学快照会明确拒绝，而不是被静默误读。

## 当前没有完成什么

这不是完整 Checkpoint/Resume：

- `AgentLoop` 尚未产生这些领域事件；
- Checkpoint save/load、checksum、delta replay 和显式 ResumeRequest 已完成；CLI Resume、schema migration 与 workspace reconcile 未实现；
- 没有 lineage、并发 writer、跨进程锁和日志压缩；
- Plan 节点已有 typed acceptance criteria，Receipt/Store 已有最小类型；尚无 verifier catalog、Receipt 查询索引、孤儿清理或跨存储事务；
- Trace Replay 与 Domain Event Replay 仍是不同的后续任务。

下一步应把最小任务生命周期接入领域事件：让 AgentLoop 在运行中实际 append event、把真实工具/Verifier 结果转换为 Receipt、在里程碑保存 checkpoint，并通过 ResumeRequest 加载快照。当前 API 仍由调用者手工驱动，不能声称 Agent 自动恢复。

## 面试短答

> Trace 解释 Runtime 怎么运行，Domain Event 记录任务状态发生了什么变化。Reducer 是确定性状态转换函数，把有序领域事件重建成当前 Task State。Checkpoint 保存包含 Plan 的 Task State 快照，typed Receipt 保存节点完成证据，ResumeRequest 明确恢复身份与 workspace 预期；这些合同已有独立测试，但还没有接入 AgentLoop，所以尚不具备产品级自动恢复入口。

常见追问：Receipt 有 checksum 是否就可信？

> 不是。checksum 能发现内容被意外修改，但攻击者若能改文件也可能重算 checksum；它更不是外部系统已完成动作的证明。真正的高风险验证还需要访问控制、签名或外部 API 回读。当前 checksum 只提供教学基线的完整性检查。

常见追问：为什么不直接反序列化 Trace 恢复？

> Trace 为观测优化，可能混有 token、Context 和 retry 等非业务事件，字段也可能为调试而变化。恢复日志需要更强的不丢失、顺序、版本、幂等和重放语义，所以应建立独立合同，即便底层以后共用存储或事件总线。
