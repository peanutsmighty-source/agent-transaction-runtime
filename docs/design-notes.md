# Runtime 设计记录

本文件记录项目级的设计约定。每次新增关键 runtime 机制时，相关文档和提交说明应回答：

1. 它解决什么问题？
2. 为什么采用这个设计？
3. 主流 Coding Agent / runtime 的相近做法是什么？
4. 它的代价、边界和替代方案是什么？

沟通时必须先用面向初学者的语言解释问题和数据流，再汇报类名、文件和测试。不得只列出新增内容。完整约定见项目根目录 `AGENTS.md`。

## Approval Policy

### 问题

模型能够提出工具请求，但模型输出本身不应成为执行宿主机命令或修改文件的授权。尤其在 prompt injection 或错误判断下，两者直接绑定会放大风险。

### 设计

每个 ToolCall 在进入 ToolRuntime 前先经过 `ToolApprovalPolicy`，得到 allow、require_approval 或 deny。第一版没有交互审批 UI；只有明确的只读文件操作自动放行，其他未预授权操作会返回结构化失败 observation。默认采用 fail-closed，避免新工具被意外放行。

### 主流做法与取舍

成熟 Coding Agent 通常将授权策略与技术沙箱分开。我们的静态 run-level 预授权容易理解和测试，但粒度较粗，也无法判断某条 shell 命令本身是否安全；它是审批生命周期的基线，不替代 OS sandbox。

## Sandbox Runner 抽象

### 问题

ShellTool 原先直接创建宿主机进程，把 Agent 的工具协议与命令执行环境耦合在一起。`cwd` 检查只能约束起始目录，不能阻止绝对路径、网络访问或子进程越界。

### 设计

ShellTool 将校验后的命令转换为 `CommandRequest`，交给 `SandboxRunner`；Runner 返回不含 Agent 语义的 `CommandExecution`，再由 ShellTool 转换为 `ToolResult`。每个 Runner 必须声明隔离类型，结果和 trace 会记录 runner 名称与 isolation。当前 `HostRunner` 明确声明 `none`，作为后续容器实现的对照基线。

### 主流做法与取舍

Codex、Claude Code 等本地 Agent 使用 OS 级执行边界，OpenHands 使用 Docker，云端 Agent 常使用容器或 VM；它们都需要让 Agent loop 与具体隔离后端解耦。协议抽象让后端可替换、可测试，但抽象本身不提供安全性。HostRunner 仍以用户权限运行；需要显式选择 DockerSandboxRunner 才进入容器边界。

## Docker Sandbox Runner

### 问题

HostRunner 只能证明 Runner 协议可替换，不能阻止命令读取宿主机文件、访问网络或消耗资源。需要一个由 Agent 进程之外的机制强制执行边界。

### 设计

DockerSandboxRunner 为每次 shell 调用创建短生命周期容器。默认断网、只读 rootfs、移除 capabilities、禁止提权、使用非 root 用户，并限制内存、CPU、进程数和临时目录；workspace 是唯一的宿主机读写挂载。镜像必须预先存在，超时会按唯一容器名强制清理。Docker 不可用时返回结构化失败，绝不回退 HostRunner。

### 主流做法与取舍

这接近 OpenHands 的本地 Docker Runtime 路线，比 Codex/Claude Code 的 OS 原生 sandbox 更容易跨平台学习和复现。代价是启动较慢、依赖 Docker，并与宿主机共享内核；workspace 内部仍属于允许破坏的范围。当前网络是开/关二值策略，尚未实现 Claude Code 风格的域名代理。

## Model Response / ResponseItem

### 问题

单个 `ToolCall | FinalAnswer` 无法表达真实模型一次响应中的说明文字、多个工具调用、usage 和稳定的调用关联。

### 设计

`ModelClient.generate()` 返回 `ModelResponse`，其中包含有序 `ResponseItem[]`、`response_id` 与 usage。ToolCall 使用 `call_id` 贯通 result、message 和 trace；`step` 改为模型轮次计数。

### 主流做法与取舍

生产级 Agent Runtime 通常保留 response/turn/item 边界和 tool call ID。我们的协议是 provider-neutral 的最小版本，不直接复制任一厂商对象；代价是 provider adapter 需要做显式映射。多工具目前串行执行，以维持确定性；Context 中先保留同一响应的全部 function call，再按相同顺序追加全部 output，以满足 Responses 协议。整批 call/output 构成不可拆分的 ContextUnit，后续再通过独立 execution policy 实验并行。

## Context Inspector

### 问题

Agent 的模型输入会随着消息和 tool result 累积而增长。若不先测量，后续的截断或 compaction 只能凭感觉设计。

### 设计

Context 采用 `ContextItem` 显式表示，并标记为 `system`、`task`、`summary`、`assistant` 或 `tool_result`。`ContextObserver` 是只读组件，统计总 token、分类 token 和最大 item；统计写回 State 和 trace。

### 取舍

当前 token 数使用与 provider 无关的字符近似估算，适合观察趋势，不应用于精确计费或硬 context limit。接入真实模型后可替换为 provider tokenizer。

## OpenAI Responses Provider

### 问题

Fake Model 能稳定验证 Loop，却不能证明真实 HTTP、SSE 和 function call 协议能贯通。尤其是流式连接可能把事件拆成任意网络分片，也可能在完整响应到达前断开。

### 设计

`ResponsesModelClient` 是 `ModelClient` 的适配器。它把显式 `ContextItem` 映射为 Responses input，把完成的 output item 映射回 provider-neutral 的 `ResponseItem`。工具调用与结果在 State/Context 中使用独立类型和同一个 `call_id`，下一轮分别发送为 `function_call` 与 `function_call_output`。只有 `response.completed` 才是成功终点；提前 EOF 不接受部分结果。`provider_name`、base URL、模型和密钥来源是配置，不进入 Loop。

429、5xx、连接失败、timeout 和流中断会被 adapter 归一化为带 provider、状态码、request ID 和 retryable 标记的结构化 Runtime Error。CLI 默认关闭 SDK retry，由 Agent Loop 外层的 `ModelRetryPolicy` 统一决定是否重试，避免两层尝试次数相乘。

### 主流做法与取舍

生产 Agent 通常也把 provider transport 与 Agent orchestration 分开，并保留稳定的 tool call ID。当前实现用官方 SDK 解析 SSE，避免自行维护字节级协议；代价是增加 SDK 依赖。无工具调用时把最后一条 message 解释为 FinalAnswer 是本项目首版停止约定，不是供应商协议字段。Mock Server 已覆盖分片、断线、429/5xx/timeout 和完整 Loop，但外部 API 与真实网络行为尚未验收。

## Tool Output Truncation

### 问题

完整的 shell 输出、文件内容或报错堆栈可能远大于用户任务和系统指令，挤占 Context Window，甚至使下一次模型调用超限。

### 设计

完整 `ToolResult` 写入 `ToolExecution` 与 trace；`ToolOutputPolicy` 单独生成模型可见的受限预览。过长结果保留头尾，并发出带 token 统计的 truncation event。

### 主流做法与取舍

工具输出限制、截断或摘要是长任务 Agent 的常用 context-management 手段。这里选择“保留头尾”作为可解释基线：它通常同时保留命令开头信息和结尾的错误/测试摘要，但会丢失中间内容。后续可实验语义压缩和按工具类型定制的保留策略。

## Context Budget

### 问题

模型的 context window 必须同时容纳输入和下一轮输出。只在窗口完全耗尽时才处理 context，往往已没有足够空间安全地产生总结或工具调用。

### 设计

`ContextBudget` 是纯计算 policy：从最大窗口、预留输出和阈值计算有效输入预算与 compaction trigger。当前 loop 仅记录预算检查和“需要压缩”的事件；不在 Budget 内部实现压缩。

### 取舍

这让触发条件可测、可测试、可替换，也避免把“何时压缩”和“如何压缩”耦合。阈值必须按模型、任务类型和压缩成本实验校准，75% 只是可观察的默认值。

## Sliding Window Compaction

### 问题

ContextBudget 原先只能报告即将超限，无法缩小下一轮模型输入。直接删除 AgentState 历史虽然简单，却会破坏 trace 审计、回放和不同压缩策略之间的公平比较。

### 设计

Budget 决定“何时压缩”，CompactionPolicy 决定“如何压缩”。SlidingWindowCompaction 永久保留 system 与 task，并保留满足目标 token 数的最大连续近期 unit 后缀；至少保留配置数量的最近 unit。共享 `tool_call_id` 的连续调用与结果组成不可拆分的 ContextUnit。策略只替换当前轮的 ModelContext 视图，不修改 AgentState.messages。Trace 同时记录 `pre_compaction` 与实际 `model_input`，汇总中的 compaction 次数从 event 派生。

### 主流做法与取舍

近期窗口是成熟 Agent 常用的低成本基线：确定、快速，不产生额外模型调用。它按时间而不是语义判断价值，所以可能遗忘旧约束和关键决策；当前只保证单次 tool call/result 不被拆分，还没有表达整个模型 turn。正因为缺点明确，它适合作为后续 Full Summary 与 Structured Compaction 的对照组，而不是最终方案。

## Full Summary Compaction

### 问题

Sliding Window 会无条件忘掉窗口外历史，无法保留很早但仍有效的约束、决定和失败经验。摘要能够缩短旧历史，同时保留其中与任务相关的含义。

### 设计

`CompactionPolicy.compact()` 是异步协议，因为真正摘要需要额外模型调用。`FullSummaryCompaction` 注入独立 `Summarizer`；`FakeSummarizer` 负责确定性测试，`ModelSummarizer` 则通过隔离且无工具的 `ModelClient` 轮次接入真实 Provider。策略永久保留 system/task，把历史按 `ContextUnit` 切成 older 和 recent，只总结 older，并生成独立 `SUMMARY` item。Summary 是临时派生视图，不写回 State；首版每次从完整旧历史重新总结，不增量总结旧摘要。

摘要调用失败、返回空值或结果超过剩余 token 预算时，策略发出结构化失败数据并回退 Sliding Window。event 记录压缩前后 token、摘要来源 item、原样保留 item、错误码和回退策略。

### 主流做法与取舍

生产 Agent 同样需要通过压缩或保存外部状态延长任务跨度。OpenAI Responses compaction 使用 opaque 压缩项，公开的 Anthropic 指南也说明 Claude Code/agent harness 会自动 compact。我们的自然语言摘要可读、可替换、便于教学评测，但比 opaque/provider-native 压缩更容易被模型误读，也缺少真实 tokenizer、摘要专用 retry、缓存和质量保证。重新总结完整旧历史避免误差逐代累积，却会在真实 provider 下增加延迟和费用；后续可用派生缓存和 Structured Compaction 比较。

### Memory 可信边界

Provenance 只回答“这条派生 claim 来自哪里”，不能证明 claim 正确，也不能保证模型会主动回查。Retrieval 会把证据重新加入当轮 Context，因此也不是免费记忆。后续主线采用风险分级：高风险 claim 在副作用、完成、过期或冲突边界由 Runtime 强制 read-back；证据按 token/item/call 预算取回短片段，阶段结束后移出活跃视图。无法被模型、用户或 verifier 察觉的错误仍是残余风险，必须通过错误摘要注入和 long-horizon retention 测试量化。完整风险登记见 `docs/context-memory-risks.md`。

## 重复失败工具调用检测

### 问题

模型可能重复发出同一工具调用，且每次都失败；`max_steps` 能最终终止运行，但无法及时解释或观测无进展。

### 设计

`RepeatedFailedActionDetector` 对最近 N 次 `ToolExecution` 计算由工具名和规范化参数组成的 fingerprint。
只有连续 N 次 fingerprint 相同且结果均失败时，才产生 `possible_loop_detected` event。

本阶段该 policy 只观测，不强制停止：模型仍可以根据工具错误自行改变策略。这样可以先收集真实循环数据，再实验 warning、强制停止、reflection 或人工介入等不同策略。

### 取舍

这会漏掉“参数不断微调但本质无进展”的循环，也可能将某些合理重试标记为 warning。它是低成本、可解释的基线，不是完整的 progress detector。

## Task Verifier

### 问题

模型输出 `FinalAnswer` 只能证明模型认为任务完成，不能证明磁盘上的代码正确、测试通过或用户要求的文件真的存在。如果 Runtime 直接相信这段文字，会产生“报告成功、实际失败”的假完成。

### 设计

`FinalAnswer` 在配置了 `TaskVerifier` 时只是候选答案。Loop 发出验证事件，由独立 verifier 检查可观察结果：`FileStateVerifier` 检查 workspace 内文件后置条件，`CommandTaskVerifier` 用命令退出码检查测试，`CompositeTaskVerifier` 组合多个契约。验证通过后才转为 `COMPLETED`；失败时保留候选答案和结构化验证结果，并作为 `VERIFICATION` context item 反馈给下一轮模型修正。

未配置 verifier 时保留原有行为，以便通用交互任务和教学实验使用。验证命令由 Runtime 所有者配置，不由模型生成；默认 `HostRunner` 没有隔离能力，处理不可信仓库时仍应显式注入受隔离的 Runner。

### 主流做法与取舍

成熟 Coding Agent 通常也把模型的完成声明与测试、lint、构建或环境断言分开。显式完成契约能降低假成功，并生成可复现的失败证据；代价是每类任务需要定义合适的 verifier，测试本身也可能不完整或不稳定。当前实现是 run 内即时验证，尚未提供声明式 CLI 配置、测试覆盖率判定或跨进程恢复。

## Cancellation Lifecycle

### 问题

把 `AgentState.status` 改成 cancelled 并不足以取消任务。Agent 可能仍在读取模型流、等待工具进程或执行 verifier；如果取消信号没有向下传播，请求会继续计费，子进程也可能残留。

### 设计

调用者通过取消 `AgentLoop.run()` 所在的 asyncio Task 发出信号。`CancelledError` 会穿过当前模型、工具、compaction 或 verification await：Responses Provider 关闭尚未完成的流；HostRunner 杀死并等待直接 shell 进程；DockerRunner 杀死 CLI 进程并强制删除对应容器。资源清理完成后，Loop 将 State 转为 `CANCELLED`，写入 `agent_cancelled`、`agent_stopped` 和最终 result trace。

### 主流做法与取舍

生产 Agent 同样需要结构化并发和向下取消，而不是轮询一个布尔字段。当前 asyncio task cancellation 简单且能覆盖单进程 Runtime，但还没有产品级 cancel handle、CLI/UI 取消命令和取消超时。HostRunner 杀死直接 shell 进程，在 Windows 上不能保证任意孙进程都被递归清理；DockerRunner 的真实容器清理仍待硬件升级后验收。因此这是可测试的生命周期基线，不是完整生产保证。

## Model Retry Policy

### 问题

429、临时服务错误、连接失败、timeout 和 SSE 中断可能发生在有效 ModelResponse 提交之前。完全不重试会把短暂故障放大成任务失败；重跑整个 Agent step 则可能重复工具副作用。

### 设计

Retry 边界只包裹 `ModelClient.generate()`。Provider 先把供应商错误归一化为带 `retryable` 的 `ModelProviderError`，`ModelRetryPolicy` 再根据最大尝试、指数退避和可选估算输入 token 预算决定是否用完全相同的 Context 重试。未收到 completed 的部分流不会提交 State，因此不会提前执行其中 ToolCall；上一轮已经完成的工具也不在重试范围内。CLI 默认关闭 SDK retry，避免 Provider 和 Runtime 两层次数相乘。

### 主流做法与取舍

生产系统通常把 transport retry、orchestration retry 和业务副作用 retry 分开，并使用 jitter、Retry-After、幂等键与全局费用预算。当前实现是确定、可观察的模型请求级基线；token 仍是字符估算，失败请求的真实费用不可见，也没有实现工具副作用重试或跨进程恢复。完整说明见 `docs/retry-policy.md`。
