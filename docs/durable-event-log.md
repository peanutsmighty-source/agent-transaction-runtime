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

## 当前没有完成什么

这不是完整 Checkpoint/Resume：

- `AgentLoop` 尚未产生这些领域事件；
- 没有 checkpoint save/load、checksum、schema migration 或 workspace reconcile；
- 没有 lineage、并发 writer、跨进程锁和日志压缩；
- 没有 typed acceptance criteria 和 receipt store；
- Trace Replay 与 Domain Event Replay 仍是不同的后续任务。

下一步应先把最小任务生命周期接入领域事件，再实现 checkpoint = metadata + `DurableTaskState` snapshot，并验证“checkpoint + 后续事件”的结果与完整 replay 一致。

## 面试短答

> Trace 解释 Runtime 怎么运行，Domain Event 记录任务状态发生了什么变化。Reducer 是确定性状态转换函数，把有序领域事件重建成当前 Task State。本项目先实现带版本和连续序号的单 run JSONL Event Store，并用 replay 等价测试证明基础合同；它还没有接入 AgentLoop，也不能声称已经支持 checkpoint 恢复。

常见追问：为什么不直接反序列化 Trace 恢复？

> Trace 为观测优化，可能混有 token、Context 和 retry 等非业务事件，字段也可能为调试而变化。恢复日志需要更强的不丢失、顺序、版本、幂等和重放语义，所以应建立独立合同，即便底层以后共用存储或事件总线。
