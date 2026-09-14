# Agent Runtime Lab

一个用于学习 Agent Runtime 核心机制的最小、显式、可观测 Coding Agent。

当前已经完成单 Agent 基础骨架、Context Inspector、两种 Compaction 基线和真实模型适配层：
单 Agent loop、状态和事件、文件/shell 工具、workspace 路径校验、审批策略、
Host/Docker 命令 Runner、结构化 execution trace、Tool Output Truncation 和
Sliding Window Compaction、可注入 Fake Summarizer 的 Full Summary Compaction，以及
Responses-compatible 流式 Provider 均已有实现。
此外已加入尚未接入 AgentLoop 的 Durable Domain Event、Reducer、Checkpoint 与 Task Session 协调层，
以及版本化 TaskPlan、typed Receipt Store 和 ResumeRequest，用于验证任务状态可由事件确定性重建、
证据可核对，并能从满足身份和 workspace 约束的快照继续重放；
尚未实现 OS 原生 sandbox、Structured Compaction 或 multi-agent；
Docker 隔离也尚未完成实机验收。

如果你第一次阅读本项目，建议先看[项目进度与学习记录](docs/progress-and-learning.md)。
它用尽量少的术语解释当前做到哪里、每个机制解决什么问题，以及接下来要学习什么。
本轮从 Agent Loop、Context/Memory 到 Durable Task 与 Multi-Agent 边界的完整复习材料，见
[Session 学习笔记](docs/session-learning-notes.md)。

## 关键设计

`AgentState` 保存运行时事实；`ContextBuilder` 从 State 派生出某一轮真正送给模型的
`ModelContext`。工具调用由 `ToolRuntime` 执行，并将结果作为 event 和 message 写回 State。

```text
State -> ContextBuilder -> ModelClient -> Action -> ToolRuntime -> State
```

每次 run 会在 `.runs/<run-id>/` 下写入 `metadata.json`、`events.jsonl`、
`context.jsonl`、`tools.jsonl` 与 `result.json`，用于调试和后续 replay/评测。

## 快速开始

安装开发依赖后运行测试：

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

使用脚本化 fake model 演示 loop：

```bash
python -m runtime run "inspect the workspace" --workspace examples/demo_project --responses examples/responses.json --allow-shell
```

执行完整的“修改—验证”演示（它会修复 `examples/demo_project/calculator.py`）：

```bash
python -m runtime run "fix the add function" --workspace examples/demo_project --responses examples/repair_responses.json --allow-file-writes --allow-shell
```

`--responses` 是 JSON 数组，每项代表一次模型响应。一次响应可以包含一个或多个 item：

```json
{
  "response_id": "response_1",
  "items": [
    {
      "type": "tool_call",
      "call_id": "call_1",
      "name": "file",
      "arguments": {"operation": "list", "path": "."}
    }
  ]
}
```

或：

```json
{"response_id": "response_2", "items": [{"type": "final_answer", "content": "Finished."}]}
```

使用任意兼容 Responses API 的供应商。以 DeepSeek 为例：

```powershell
python -m runtime run "inspect the workspace" `
  --workspace examples/demo_project `
  --provider responses `
  --provider-name deepseek `
  --api-base-url https://api.deepseek.com `
  --api-key-file E:\tmp\key.txt `
  --model deepseek-v4-flash
```

生产使用更推荐把密钥放在环境变量中，并用 `--api-key-env` 指定变量名；
`--api-key-file` 主要用于本地实验，文件必须放在仓库外。

模型 SDK 只位于 `runtime/model/`，不会隐藏 Agent Loop。当前已经用本地 Mock Server
覆盖 HTTP/SSE、工具调用往返、中途断线、429/5xx 重试和 timeout；真实 API smoke
必须通过环境变量显式开启。2026-09-04 已使用 DeepSeek 官方 Responses API 和
`deepseek-v4-flash` 验证普通响应与完整 Agent Loop 工具往返。
协议与测试边界见 [Responses-compatible Provider](docs/openai-responses-provider.md)。

CLI 默认由 Runtime 对尚未提交的模型请求最多尝试三次，并关闭 SDK 内部重试，避免两层重试次数相乘。可以通过 `--model-retry-max-attempts`、`--model-retry-base-delay`、`--model-retry-max-delay` 和 `--model-retry-token-budget` 调整；400 等非 retryable 错误不会重试，已执行的工具也不会重放。设计边界见[模型重试策略](docs/retry-policy.md)。

使用已经在本机准备好的 Docker 镜像执行 shell：

```bash
python -m runtime run "inspect the workspace" --workspace examples/demo_project --responses examples/responses.json --allow-shell --shell-runner docker --docker-image python:3.12-slim
```

Docker Runner 默认断网，并使用 `--pull never`，因此不会自动下载缺失镜像。只有明确需要网络并理解数据外传风险时才传入 `--allow-sandbox-network`。

启用不需要额外模型调用的 Sliding Window Compaction：

```bash
python -m runtime run "inspect the workspace" --workspace examples/demo_project --responses examples/responses.json --compaction sliding-window
```

它只缩小模型可见的 Context，不会删除 AgentState 和 trace 中的完整历史。共享 `tool_call_id` 的调用与结果会作为一个不可拆分的 ContextUnit 保留或删除。Docker 的真实隔离验收因本机内存升级暂缓，状态见 `TODO.md`。

Full Summary 既可通过 Python API 注入，也可在 Responses Provider 下直接启用真实摘要模型：

```bash
python -m runtime run "inspect the workspace" --workspace examples/demo_project --provider responses --model agent-model --compaction full-summary --summary-model summary-model
```

`--summary-model` 省略时复用主模型 ID，但摘要仍使用独立 client 和不带工具的隔离模型轮次。摘要失败或超过预算时回退 Sliding Window；当前尚未为摘要调用实现独立 retry、硬输出 token 上限和派生缓存。确定性测试继续使用 `FakeSummarizer`。设计见[Full Summary Compaction](docs/full-summary-compaction.md)。

Context/Memory 的下一阶段不把摘要视为事实：`MemoryRetentionPolicy` 已把保留等级、可信状态、风险和使用边界显式化，derived claim 用于计划、技术选型或代码修改时必须先核验。当前只是决策合同，尚未接入 Artifact read-back 和 Loop enforcement。详见[Memory/Retention Contract](docs/memory-retention-contract.md)与[风险登记](docs/context-memory-risks.md)。

超长任务恢复的独立基础已实现：`DomainEvent` 使用稳定 ID、run ID、连续 sequence 和 schema version，纯 Reducer 可从事件流重建包含计划进度的 `DurableTaskState`，单 run JSONL Store 拒绝乱序和冲突重复。版本化 `TaskCheckpoint` 保存完整 Task State（包括 plan），通过 checksum 检测篡改，并支持从 snapshot 继续重放后续事件。`DurableTaskSession` 进一步统一 Reducer 预计算/验证、事件持久化、新 State 发布、sequence 分配、checkpoint 和恢复。它目前还没有接入现有 `AgentLoop`，因此 CLI 不会自动保存或恢复任务；设计和测试边界见[Durable Event Log 与 Reducer](docs/durable-event-log.md)。

## 安全边界

`FileTool` 限制在指定的 workspace 内。文件写入和 shell 默认需要授权；CLI 的
`--allow-file-writes` 与 `--allow-shell` 表示用户为当前 run 预授权。

`ShellTool` 通过 `SandboxRunner` 执行命令。默认 `HostRunner` 明确声明
`isolation=none`，只限制工作目录、超时和输出长度，**不能阻止命令通过绝对路径访问 workspace 外部或访问网络**。
使用 `--shell-runner docker` 才会启用容器隔离；它默认断网并只挂载 workspace，但 Agent 仍能修改或删除 workspace 内文件。
ApprovalPolicy 是授权判断，不是操作系统级隔离。切勿让当前版本处理不可信任务或重要凭据。
