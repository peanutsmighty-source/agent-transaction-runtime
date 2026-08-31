# Agent Runtime Lab

一个用于学习 Agent Runtime 核心机制的最小、显式、可观测 Coding Agent。

当前已经完成单 Agent 基础骨架和 Context Inspector，正在进行 Compaction 阶段：
单 Agent loop、状态和事件、文件/shell 工具、workspace 路径校验、审批策略、
Host/Docker 命令 Runner、结构化 execution trace、Tool Output Truncation 和
Sliding Window Compaction 均已有实现。尚未实现真实模型 provider、OS 原生 sandbox、
Full Summary/Structured Compaction 或 multi-agent；Docker 隔离也尚未完成实机验收。

如果你第一次阅读本项目，建议先看[项目进度与学习记录](docs/progress-and-learning.md)。
它用尽量少的术语解释当前做到哪里、每个机制解决什么问题，以及接下来要学习什么。

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

真实模型接入将是下一轮阶段 A 的增量；模型 SDK 只会位于 `runtime/model/`，不会隐藏 Agent Loop。

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

## 安全边界

`FileTool` 限制在指定的 workspace 内。文件写入和 shell 默认需要授权；CLI 的
`--allow-file-writes` 与 `--allow-shell` 表示用户为当前 run 预授权。

`ShellTool` 通过 `SandboxRunner` 执行命令。默认 `HostRunner` 明确声明
`isolation=none`，只限制工作目录、超时和输出长度，**不能阻止命令通过绝对路径访问 workspace 外部或访问网络**。
使用 `--shell-runner docker` 才会启用容器隔离；它默认断网并只挂载 workspace，但 Agent 仍能修改或删除 workspace 内文件。
ApprovalPolicy 是授权判断，不是操作系统级隔离。切勿让当前版本处理不可信任务或重要凭据。
