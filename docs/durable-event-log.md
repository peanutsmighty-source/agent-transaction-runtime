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

当前领域事件支持创建任务、接受计划、开始和完成计划节点、增加和解决 blocker、设置下一动作。`PLAN_ACCEPTED` 的节点会进入 `DurableTaskState.plan`，因此未来 checkpoint 序列化 Task State 时也会包含计划、节点状态、依赖和完成证据；它不是从原始对话自由摘要出来的文本。

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

## 当前没有完成什么

这不是完整 Checkpoint/Resume：

- `AgentLoop` 尚未产生这些领域事件；
- Checkpoint save/load、checksum 和 delta replay 已完成；schema migration 与 workspace reconcile 未实现；
- 没有 lineage、并发 writer、跨进程锁和日志压缩；
- 没有 typed acceptance criteria 和 receipt store；
- Trace Replay 与 Domain Event Replay 仍是不同的后续任务。

下一步应把最小任务生命周期接入领域事件：让 AgentLoop 在运行中实际 append event、在里程碑保存 checkpoint，并通过 ResumeRequest 加载快照。当前 API 只能由调用者手工创建事件和 checkpoint，不能声称 Agent 自动恢复。

## 面试短答

> Trace 解释 Runtime 怎么运行，Domain Event 记录任务状态发生了什么变化。Reducer 是确定性状态转换函数，把有序领域事件重建成当前 Task State。Checkpoint 保存包含 Plan 的 Task State 快照，snapshot + delta replay 已通过等价测试；它还没有接入 AgentLoop，所以尚不具备产品级自动恢复入口。

常见追问：为什么不直接反序列化 Trace 恢复？

> Trace 为观测优化，可能混有 token、Context 和 retry 等非业务事件，字段也可能为调试而变化。恢复日志需要更强的不丢失、顺序、版本、幂等和重放语义，所以应建立独立合同，即便底层以后共用存储或事件总线。
