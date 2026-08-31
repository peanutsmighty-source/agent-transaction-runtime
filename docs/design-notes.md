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

生产级 Agent Runtime 通常保留 response/turn/item 边界和 tool call ID。我们的协议是 provider-neutral 的最小版本，不直接复制任一厂商对象；代价是 provider adapter 需要做显式映射。多工具目前串行执行，以维持确定性，后续再通过独立 execution policy 实验并行。

## Context Inspector

### 问题

Agent 的模型输入会随着消息和 tool result 累积而增长。若不先测量，后续的截断或 compaction 只能凭感觉设计。

### 设计

Context 采用 `ContextItem` 显式表示，并标记为 `system`、`task`、`assistant` 或 `tool_result`。`ContextObserver` 是只读组件，统计总 token、分类 token 和最大 item；统计写回 State 和 trace。

### 取舍

当前 token 数使用与 provider 无关的字符近似估算，适合观察趋势，不应用于精确计费或硬 context limit。接入真实模型后可替换为 provider tokenizer。

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

## 重复失败工具调用检测

### 问题

模型可能重复发出同一工具调用，且每次都失败；`max_steps` 能最终终止运行，但无法及时解释或观测无进展。

### 设计

`RepeatedFailedActionDetector` 对最近 N 次 `ToolExecution` 计算由工具名和规范化参数组成的 fingerprint。
只有连续 N 次 fingerprint 相同且结果均失败时，才产生 `possible_loop_detected` event。

本阶段该 policy 只观测，不强制停止：模型仍可以根据工具错误自行改变策略。这样可以先收集真实循环数据，再实验 warning、强制停止、reflection 或人工介入等不同策略。

### 取舍

这会漏掉“参数不断微调但本质无进展”的循环，也可能将某些合理重试标记为 warning。它是低成本、可解释的基线，不是完整的 progress detector。
