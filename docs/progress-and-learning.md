# 项目进度与学习记录

这是一份给初学者看的项目地图。它回答三个问题：

1. 我们正在做什么？
2. 目前做到什么程度？
3. 到现在为止学会了哪些 Agent 核心技术？

## 一句话定位

Agent Runtime Lab 是一个用来拆解 Coding Agent 工作原理的教学项目。

它现在已经有了 Agent 的主要骨架并接入了真实模型，但尚未通过复杂 Coding Task 验收，因此更准确地说，它是：

> 可运行、可观察、可测试，并已完成最小真实模型工具闭环的单 Agent Runtime 教学原型。

它的目标不是尽快做出另一个 Codex 或 Claude Code，而是把这些产品隐藏起来的核心机制逐个实现、观察和比较。

## Agent 是怎么运行的

当前程序的主流程可以理解成一个反复进行的问答循环：

```text
用户给任务
   ↓
AgentState 保存当前事实
   ↓
ContextBuilder 挑出这一轮要给模型看的内容
   ↓
模型决定：回答，还是调用工具
   ↓
ToolRuntime 检查授权并执行工具
   ↓
工具结果写回 AgentState
   ↓
进入下一轮，直到完成或停止
```

这就是 `AgentLoop`。ReAct 等 Agent 方法也采用“思考/行动/观察/再行动”的循环思想。我们的实现没有强制模型输出固定的 ReAct 文本格式，而是使用结构化的 `ToolCall` 和 `ToolResult`，更接近现代 Coding Agent 的工具调用方式。

## 当前进度

### 已完成：单 Agent 的基础骨架

- 显式的 `AgentLoop`，可以连续调用模型和工具。
- `AgentState`，保存任务、消息、事件、工具执行记录、用量和最终结果。
- `FakeModelClient`，用预先写好的响应稳定测试整个 Agent，而不消耗模型 API。
- 文件工具和 Shell 工具。
- max steps、错误处理、停止条件和重复失败调用告警。
- CLI 和“读取文件—修改代码—运行测试—最终回答”的演示流程。

### 已完成：模型响应协议

一次模型响应不再只能表示一个动作，而是可以包含多个有顺序的 `ResponseItem`：

```text
ModelResponse
├─ AssistantMessage   给用户看的过程说明
├─ ReasoningSummary   可展示的简短推理摘要
├─ ToolCall           工具请求
└─ FinalAnswer        最终回答
```

每次工具调用都有 `call_id`。工具结果使用同一个 ID，避免多次工具调用时把问题和答案配错。

### 已完成：OpenAI Responses 适配层

- 使用官方 SDK 连接 Responses API，但 SDK 不接管 Agent Loop 或工具执行。
- 流式 SSE 只有收到 `response.completed` 才算成功；中途断线不会把部分文字当答案。
- 工具调用和结果在下一轮保持为结构化 `function_call` / `function_call_output`。
- 本地 Mock Server 已覆盖字节分片、断线和一次完整的“模型—工具—模型”循环，不产生真实 API 费用。
- 429、500 和 timeout 已有可控测试；Provider 错误会把状态码、request ID 和是否可重试写入 trace。
- 已提供默认跳过的真实 API smoke harness；2026-09-04 使用 DeepSeek `deepseek-v4-flash` 实测了普通响应和完整的“模型—FileTool—模型”循环。

### 已完成：可观察的 Context

- `ContextItem` 明确区分 system、task、assistant 和 tool result。
- `ContextObserver` 统计每类内容大约占多少 token、哪一项最大。
- `ContextBudget` 判断模型输入是否接近预算上限。
- 每次运行产生结构化 trace，可以查看 Agent 当时看到了什么、调用了什么工具、为什么停止。

### 已完成：两种 Context 压缩策略

- 过长工具输出采用“保留开头和结尾”的截断方式。
- `SlidingWindowCompaction` 在超预算时保留最近内容。
- 压缩只改变“这一轮给模型看的内容”，不会删除 `AgentState` 和 trace 中的完整历史。
- `ToolCall` 与对应的 `ToolResult` 被组成一个 `ContextUnit`，压缩时一起保留或一起删除，不会留下半次工具交互。
- `FullSummaryCompaction` 把较早的完整 unit 总结为可读 `SUMMARY`，同时原样保留最近 unit。
- 摘要由可注入的异步 `Summarizer` 产生；`FakeSummarizer` 用于确定性测试，`ModelSummarizer` 已接入真实 Responses Provider；失败或摘要超预算时明确记录并回退 Sliding Window。

### 已完成代码、等待实机验收：Docker Sandbox

- `SandboxRunner` 把“Agent 想执行命令”和“命令在哪里执行”分开。
- `HostRunner` 在本机执行，只是开发和对照基线，不提供真正隔离。
- `DockerSandboxRunner` 已实现默认断网、非 root、只读根文件系统和资源限制等参数。

由于本机启动 Docker 时内存不足并导致系统崩溃，目前只完成了参数级测试，尚未证明这些隔离边界在本机真实有效。因此不能把 Docker Runner 标记为“已经安全验收”。

### 正在进行：单 Agent Loop 的任务级验收

TaskVerifier、复杂分支场景、真实代码修复任务、取消生命周期和高层 Model Retry Policy 已完成基线。真实 Summarizer 的隔离 Provider 链路也已接通；下一步是摘要专用 timeout/retry/费用边界、派生缓存、long-horizon retention、Structured Compaction 与 Trace Replay。在这些质量证据完成前不开始正式 benchmark。

新的设计结论是：provenance 不等于自动纠错，Evidence Retrieval 也会重新占用 Context。P1 因此先定义 Memory/Retention Contract，并用错误摘要注入测试证明 Runtime 能在高风险边界强制核验；之后再做 Structured State、Artifact Store、有界 Retrieval 和分层摘要，最后才优化摘要缓存。问题与方案清单见 `docs/context-memory-risks.md`。

Memory/Retention Contract 的纯 Policy 已完成：保留等级与可信状态不再混为一谈，derived claim 在技术选型、计划和代码修改等 actionable boundary 会被标记为必须核验并阻断。它尚未自动取回证据；下一步先用错误摘要注入建立 Loop 级失败测试，再实现 State/Artifact 接线。详见 `docs/memory-retention-contract.md`。

### 尚未开始

- 生产级真实网络、限流与长任务稳定性测试。
- 交互式审批界面。
- Replay 和完整 benchmark。
- Multi-Agent 的 spawn、wait、cancel 和结果汇总。

## 到目前为止学到的核心知识

### 1. Agent Loop 是 Agent 的发动机

大模型本身只生成一次响应。Agent Loop 把模型、工具结果和下一次模型调用连接起来，才形成能够连续工作的 Agent。

关键认识：

> Agent 不是一个“更聪明的模型”，而是模型外面的一套循环、状态、工具和控制策略。

### 2. State 和 Context 不是同一个东西

- `AgentState` 是运行时掌握的完整事实，像项目档案柜。
- `Context` 是某一轮实际交给模型看的内容，像从档案柜里拿出来放在办公桌上的材料。

档案柜可以很大，办公桌受模型 context window 限制。因此 Context 必须由 State 派生，而不是简单复制全部历史。

### 3. Event 和 Trace 让 Agent 不再是黑盒

- Event 是运行过程中发生的一件事，例如工具开始、工具失败、触发压缩。
- Trace 是按顺序保存这些事件和相关数据的运行记录。

其他统计和界面可以从事件推导出来。这样只需要维护一份事实来源，也更容易调试、回放和评测。

### 4. Tool Call 失败不等于程序崩溃

工具失败会被包装成结构化 `ToolResult`，然后重新交给模型。模型可以根据错误换一种方法。

同时还有两层保险：

- `max_steps` 防止 Agent 永远运行。
- `RepeatedFailedActionDetector` 发现连续重复的失败调用并写入告警事件。

当前重复失败检测只负责观察和告警，不会强制停止。这方便以后比较“告警、反思、停止、人工介入”等不同策略。

### 5. Approval 和 Sandbox 解决的是两个问题

- Approval Policy 回答：“是否允许执行这个操作？”
- Sandbox 回答：“即使允许了，这个操作在技术上能访问什么？”

只做审批不能阻止命令越界；只做沙箱也不能表达用户是否授权。Codex、Claude Code 等成熟 Coding Agent 同样需要把这两个边界分开。

### 6. Context 管理不是简单删除旧消息

最简单的近期窗口快速、便宜，但可能忘记很早以前的重要约束。总结可以保留更多含义，但会增加模型调用和总结错误。结构化压缩更适合保存 Agent 状态，但需要提前定义哪些信息重要。

因此项目不会直接宣布某一种方案最好，而是让不同策略运行同一组任务，用 token、成功率和信息保留率比较。

### 7. 关键设计原则是“事实保留，视图可变”

我们保留完整 State 和 Trace，只压缩模型输入视图。好处是：

- 可以审计 Agent 实际做过什么。
- 可以用同一份历史比较多种 Context 策略。
- 压缩策略出错时，原始事实仍然存在。

这是当前 Context 设计中最重要的原则。

## 和主流 Coding Agent 的关系

项目采用的核心方向与 Codex、Claude Code 等产品相同：模型在工具反馈中循环决策，并配合状态、Context 管理、审批、隔离和 Trace。

主要区别是：

- 主流产品是生产级 Agent，功能完整且很多内部细节不可见。
- 本项目是教学型白盒 Runtime，功能更少，但每个机制都尽量显式、可替换、可测试。
- 我们不会照搬某个产品，而是把共同机制做成小实验，理解它为什么存在以及有什么代价。

## 当前最重要的限制

1. 真实 Provider 已完成核心 Coding Task 重复实验，但还没有 long-horizon retention 和正式 benchmark。
2. token 是近似估算，不等于模型供应商的真实计费数据。
3. Sliding Window 可能遗忘较早的重要约束；Full Summary 可能遗漏或错误概括事实；provenance 目前也不能触发 Runtime 强制核验。
4. Docker Sandbox 尚未经过本机真实隔离测试。
5. HostRunner 没有隔离能力，不能用于不可信命令。
6. 没有完整的任务 benchmark，暂时不能用数据证明策略优劣。
7. 还没有 Multi-Agent；这是刻意的，单 Agent 的状态和 Context 应先稳定。

## 后续开发顺序

不使用时间节点，只按阶段验收：

1. Memory/Retention Contract 已完成；下一步建立错误摘要注入和 long-horizon retention 测试。
2. 实现 Pinned Context、Structured Working State、claim-level provenance schema 和 Policy 的 Loop enforcement。
3. 实现 Artifact Store、有界 Evidence Retrieval 与 Runtime 强制 Verification Policy。
4. 实现分层 Structured Compaction，再补摘要 timeout/retry/费用边界和派生缓存。
5. 比较 Sliding Window、Full Summary、Structured/分层方案和可用的 Provider-native Compaction。
6. 实现 Replay，再建立正式 Coding Task benchmark。
7. 更换内存后完成 Docker Sandbox 实机验收。
8. 单 Agent 稳定后，再进入最小 Multi-Agent。

## 从哪里继续阅读

- `docs/agent-loop.md`：Loop 为什么这样循环。
- `docs/context-lifecycle.md`：State 如何变成模型 Context。
- `docs/model-response-protocol.md`：一次模型响应如何表达文字、工具和最终回答。
- `docs/approval-policy.md`：为什么模型请求不等于用户授权。
- `docs/sandbox-runner.md`：Host 和 Docker 执行环境的区别。
- `docs/sliding-window-compaction.md`：当前压缩策略如何工作。
- `docs/full-summary-compaction.md`：异步摘要、失败回退和事实保留如何工作。
- `docs/context-memory-risks.md`：错误摘要、provenance、证据取回、注意力偏移等风险和计划中的应对方案。
- `docs/memory-retention-contract.md`：记忆保留、可信状态、风险等级和强制核验边界。
- `docs/openai-responses-provider.md`：真实 Provider、SSE 分片和断线边界如何工作。
- `docs/interview-guide.md`：把已实现机制整理成面试可复述答案和追问。
- `docs/session-learning-notes.md`：本轮 Agent Loop、Context/Memory、恢复与 Multi-Agent 概念的完整复习笔记和面试题库。
- `docs/design-notes.md`：所有关键设计决定的集中记录。
- `TODO.md`：尚未完成和暂缓的任务。

以后每完成一个关键机制，都应在本页补充：它解决什么问题、为什么这样设计、主流 Agent 是否使用类似机制、它有什么限制。

## Durable Event Log / Reducer 基线

超长任务不能只依赖 Context Summary。当前新增了独立于 Trace 合同的 `DomainEvent`、`DurableTaskState`、纯 Reducer 和单 run JSONL Store：计划本身保存在 Task State，节点开始、完成、证据 receipt ID、blocker 和下一动作都由事件更新。测试证明同一事件流在内存和落盘重放后得到相同状态，并拒绝循环依赖、依赖未完成、sequence 间隙、冲突重复和损坏 JSONL。

这一步只证明“领域事件可以确定性重建 Task State”，还没有把现有 AgentLoop 改成可恢复 Runtime，也没有 Checkpoint、Lineage、schema migration 或 workspace reconcile。下一步先接入最小任务生命周期，再实现 checkpoint + delta replay，而不是继续增加摘要或 Memory Policy。详见 `docs/durable-event-log.md`。

Checkpoint 存储基线随后已完成：快照直接序列化包含 plan 的 `DurableTaskState`，不是从对话重新生成摘要；metadata 记录快照覆盖的 event sequence，checksum 检测内容变化。单 run JSON Store 通过临时文件 + 原子替换发布快照，`replay_from_checkpoint` 只应用后续事件。测试已证明 snapshot + delta replay 与完整 replay 等价。仍未完成 AgentLoop 接线、自动里程碑、Resume CLI、schema migration、workspace reconcile 或 lineage。

`DurableTaskSession` 随后补上调用顺序合同：先用纯 Reducer 计算并验证候选 State，再 append + fsync，最后发布预先算好的内存 State；它负责 sequence 分配和 checkpoint 恢复。故障注入测试模拟事件已经落盘但内存尚未更新时崩溃，新 Session 能重放该事件；非法状态转换则不会写入日志。Receipt 目前仍只是 plan 节点保存的 ID，没有 Receipt 类型或 Store。
