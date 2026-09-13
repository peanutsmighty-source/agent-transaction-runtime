# Changelog

## Unreleased

- 增加版本化 Durable Domain Event、不可变 Task State Reducer 和单 run JSONL Event Store 基线。
- 增加计划依赖、乱序/冲突事件、幂等 append、损坏日志和 replay 等价测试；尚未接入 AgentLoop 或 Checkpoint Resume。
- 增加带 checksum 的版本化 Task Checkpoint、单 run 原子文件 Store 和 delta replay；测试证明 checkpoint 中保留 plan，且 snapshot + 后续事件与完整 replay 得到相同 State。AgentLoop 自动保存/恢复尚未接入。

本文件记录 Agent Runtime Lab 每个阶段真正完成并经过验证的能力。

项目仍处于 alpha 阶段。这里的“完成”表示对应机制已有代码和测试，
不表示已经达到生产环境的安全性或稳定性。

## 0.1.0-alpha — Initial public baseline

### 已实现

- 建立显式单 Agent Loop，连接模型响应、工具执行、状态更新和停止条件。
- 建立 `AgentState`、Agent Event 与结构化 execution trace。
- 加入结构化模型响应协议，支持 assistant message、reasoning summary、
  多个 tool call 和 final answer。
- 实现脚本化 `FakeModelClient`，可以在不调用真实模型的情况下稳定测试 Agent 行为。
- 实现文件工具、Shell 工具、工具注册表和统一的 `ToolRuntime`。
- 文件访问限制在 workspace；文件写入和 Shell 执行默认需要授权。
- 实现 max-steps、结构化工具失败和重复失败调用告警。
- 实现 `ContextBuilder`、`ContextObserver`、近似 token 统计和 `ContextBudget`。
- 实现 Tool Output Truncation，完整工具结果仍保留在 State 和 Trace 中。
- 实现 Sliding Window Compaction，并保证配对的 tool call/result 不会被拆开。
- 抽象 `SandboxRunner`，提供无隔离的 `HostRunner` 和 Docker Runner 基线实现。
- 提供 CLI、端到端 Fake Model 示例和小型 Python 修复演示项目。
- 补充 Agent Loop、Context 生命周期、模型响应协议、审批、Sandbox 和
  Compaction 的教学文档。

### 当前限制

- 尚未接入真实模型 provider；当前自主行为由预先编排的 Fake Model 响应模拟。
- Docker Runner 只有参数级测试，尚未完成本机真实隔离验收。
- HostRunner 不提供操作系统级隔离，不能运行不可信命令。
- token 数量是近似估算，不是模型供应商的真实 tokenizer 结果。
- Sliding Window 可能遗忘早期的重要约束或决定。
- 尚未实现 Full Summary、Structured Compaction、Replay、Benchmark 和 Multi-Agent。

### 下一阶段

- 实现 Full Summary Compaction。
- 实现 Structured Compaction，显式保留约束、决定、修改文件、失败经验和下一步。
- 建立 long-horizon retention 测试与可复现的 Compaction Benchmark。
- Compaction 稳定后接入真实模型 provider，同时保持 Agent Loop 显式可观察。
