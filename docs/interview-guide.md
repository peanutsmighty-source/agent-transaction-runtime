# Agent Runtime 面试复习指南

这份文档不追求背术语，而是把项目中已经实现并有测试证据的机制整理成可复述答案。

## 当前一句话项目介绍

> 我实现了一个白盒单 Agent Runtime：模型通过显式状态机循环调用文件和 Shell 工具；State 与模型 Context 分离；运行过程写结构化 trace；Context 支持预算、工具输出截断、Sliding Window 和 Full Summary；模型层通过 Responses-compatible adapter 接入真实 DeepSeek，同时用 Fake、Mock HTTP/SSE 和真实 smoke 分层测试。

## 高频问题：协议、供应商和 SDK 有什么区别？

- 协议定义数据形状和交互语义，例如 Responses 的 input item、SSE event、function call 和 `response.completed`。
- 供应商提供具体服务，例如 OpenAI 或 DeepSeek，差异体现在 endpoint、模型、密钥、限流和部分能力兼容性。
- SDK 是发送请求和解析协议的代码库。当前复用 OpenAI Python SDK，但公共 Runtime 抽象是 `ResponsesModelClient`。

面试短答：

> 我对协议编程，不让 Agent Loop 依赖某个供应商。供应商差异通过 provider name、base URL、model 和 key source 注入。这样 Fake、OpenAI-compatible 服务和 DeepSeek 可以复用同一个 Loop；trace 又能记录真正的供应商，而不是被 SDK 名称误导。

常见追问：为什么不直接在 Loop 里调用 SDK？

> 那会把状态机、网络协议和供应商错误耦合。拆成 `ModelClient` 后，Loop 只处理 `ModelResponse`；adapter 单独负责序列化、SSE 和错误归一化，因此可替换、可测试，也更容易做故障注入。

## 高频问题：为什么工具调用必须结构化？

工具请求和结果通过 `call_id` 配对：

```text
function_call(call_id, name, arguments)
  -> ToolRuntime
  -> function_call_output(call_id, output)
```

如果把工具调用保存成普通助手文本，下一轮无法可靠恢复 JSON 参数，多工具调用也可能把结果配错。项目用独立的 `TOOL_CALL`、`TOOL_RESULT` 和不可拆分 `ContextUnit` 保留语义。

## 高频问题：流式响应何时算成功？

收到文字 delta 不等于成功。连接可能在半句话或半个工具参数时断开。只有终止事件 `response.completed` 才提交 `ModelResponse`；`failed`、`incomplete`、`error` 或提前 EOF 都进入结构化失败路径。

常见追问：流中断为什么不直接自动重试？

> Provider 不在协议解析层盲目重放，而是标记 `retryable`。当前高层 ModelRetryPolicy 只重试尚未提交 ModelResponse 的 `generate()`，并受尝试次数、退避和估算 token 预算约束；已执行工具不会被重放。请求仍可能重复计费，因此不能无限重试。

### 流式响应如何管理状态、断线和取消？

一条模型流可以抽象为：

```text
IDLE -> CONNECTING -> STREAMING -> COMPLETED
                 |         |----> FAILED
                 |         |----> CANCELLED
                 |--------------> FAILED
```

STREAMING 阶段按 `output_index`、item 和 content part 收集增量，未完成数据只属于当前请求缓冲区，不能提前写成最终 Agent 事实。收到 `response.completed` 才提交完整 `ModelResponse`；提前 EOF、`failed`、`incomplete` 或协议错误进入 FAILED。取消时应停止消费 HTTP 流、关闭连接、取消正在等待的模型任务，并把 Agent 和工具清理结果写入 trace。

当前项目现已实现 asyncio 取消传播、未完成 Responses 流关闭、工具 Runner 清理以及 `CANCELLED` trace；仍未提供产品级 UI cancel handle，也没有把文字 delta 实时暴露给 UI。

## 高频问题：什么是状态转换和状态机？

状态是系统当前所处阶段；状态转换是某个事件让系统从一个合法阶段进入另一个阶段。例如：

```text
RUNNING --收到 ToolCall--> RUNNING（执行工具并进入下一轮）
RUNNING --收到 FinalAnswer--> COMPLETED
RUNNING --模型异常/max_steps--> FAILED
RUNNING --用户取消并完成资源清理--> CANCELLED
```

状态机就是把“有哪些状态、哪些事件允许怎样变化”明确下来。主流 Agent Loop 都存在这种逻辑，但不一定都写成名为 StateMachine 的类：教学 demo 常把状态隐含在 `while` 和 `if` 中；生产 Agent 为了支持审批、暂停、恢复、取消、并发工具和持久化，通常会显式记录生命周期状态和事件。

面试短答：

> Agent Loop 本质上一定有状态机语义，因为它必须区分等待模型、执行工具、等待审批和终止。区别只是状态是隐含在控制流里，还是显式建模。我的首版用 `AgentStatus + while/if + event trace` 表达状态机，易于教学和测试；后续取消、恢复和并发增加后，可能演进为更明确的 transition table 或 reducer。

## 高频问题：分片、429、500、timeout 和协议映射分别是什么？

- 分片：一个 SSE 事件可能被拆成多次网络读取；SDK 先重组字节，adapter 再收集完成 item。
- 断线：终止事件前连接结束；adapter 丢弃未完成结果并抛出结构化错误。
- 429：供应商限流，表示请求过多或 token 配额达到限制。
- 500：供应商内部错误，不代表任务或工具本身有错。
- timeout：在规定时间内没有完成连接、读取或响应。
- 协议映射：把供应商的 message/function_call/usage/error 转成 Runtime 自己的 `ResponseItem`、`ModelUsage` 和 `ModelProviderError`。

当前 Agent Loop 不直接解析 HTTP。Provider 处理分片并将错误归一化；高层 ModelRetryPolicy 对 retryable 的未提交模型请求做有限重试，耗尽后由 Loop 转为 FAILED 并写 trace。工具失败则不同：它被包装为 `ToolResult` 返回模型，让模型有机会修正，而不是自动重放有副作用的操作。

## 高频问题：为什么 Retry 不能包住整个 Agent step？

一个 step 可能先取得 ModelResponse，再执行写文件、发邮件等工具。若整个 step 失败后重跑，已经成功的工具可能执行第二次。当前 Retry 只包裹 `model.generate()`，并且只有 `response.completed` 才提交结果，因此重复请求不会重放上一轮工具。

面试短答：

> 我把重试分层：Provider 做错误归一化，Runtime policy 决定模型请求是否重试，工具副作用不自动重放。Policy 只覆盖未提交的 generate 调用，用最大尝试、指数退避和估算 token 预算限制成本；400 直接失败，429、连接失败和流中断才可能重试。

常见追问：模型请求重试就完全没有副作用吗？

> 对本地 Agent State 和工具执行而言没有，因为部分响应没有提交；但供应商可能已经计算 token 或记录请求，所以仍可能重复计费。生产版本还要结合 request id、供应商幂等能力、Retry-After 和全局费用预算。

## 高频问题：你怎么证明 Agent Loop 真能工作？

项目使用四层证据：

1. Fake Model 单元测试：确定性覆盖状态转换和停止条件。
2. Mock HTTP/SSE：覆盖字节分片、断线、429、500、timeout 和工具协议映射。
3. Mock 端到端 Loop：经过真实 SDK 和 HTTP 层执行 FileTool，再完成第二轮。
4. DeepSeek 真实 smoke：真实模型调用 FileTool，观察结果回传后生成 FinalAnswer。

面试短答：

> Fake 测控制逻辑，Mock Server 测传输故障，真实 smoke 测供应商集成；三者不能互相替代。真实 smoke 成功不代表 benchmark 成功，因为任务长度、代码修改质量和长期稳定性还没有评测。

## 当前不能声称什么

- 不能声称生产可用：还没有生产限流、长期运行和完整安全验收。
- 不能声称摘要策略更好：还没有 retention benchmark。
- 不能声称 Docker sandbox 已安全：实现存在，但本机尚未完成隔离实测。
- 不能声称支持 Multi-Agent：当前刻意先稳定单 Agent 状态与 Context。

## 高频问题：Agent 取消为什么不是改一个状态？

取消是沿 await 调用链传播的控制信号。调用者取消 Agent asyncio Task 后，当前模型流或工具执行必须先释放网络连接、杀死进程或清理容器；随后 Loop 才记录 `CANCELLED` State 和 trace。否则 UI 虽显示取消，后台仍可能运行和计费。

面试短答：

> 我使用结构化 asyncio cancellation：`CancelledError` 向下传播到 Provider 和 Runner，底层资源完成清理后，AgentLoop 再写入 CANCELLED 与停止事件。测试分别覆盖模型等待、工具等待、Responses 流关闭和 Host 进程 kill/wait。当前边界是还没有 UI cancel handle，Windows 任意孙进程和 Docker 实机清理也尚未完成安全验收。

## 高频问题：为什么模型说完成还要 Task Verifier？

`FinalAnswer` 是模型生成的文本，不是环境事实。配置 verifier 后，Loop 把它视为候选完成信号，再用文件后置条件、测试命令退出码或多个契约组合独立验收。通过才进入 `COMPLETED`；失败结果以独立的 `VERIFICATION` 输入反馈给模型，使它能继续修复。

面试短答：

> 我把“模型认为完成”和“任务实际完成”拆开。模型负责提出候选结果，Runtime 的 TaskVerifier 依据文件状态和测试退出码验收。失败不会被记成成功，而会成为下一轮可观察反馈。这降低了假完成，但 verifier 的质量仍受测试充分性和环境稳定性限制。

常见追问：为什么不用模型自己检查？

> 模型可以调用测试帮助诊断，但最终判定不能只依赖同一个模型的文字判断。独立的确定性检查器更容易复现、追踪和统计任务成功率。

实验证据：单文件修复在隐藏验收移出 Agent workspace 后首次重复得到 2/3，其中一次因多 Tool Call 的 Responses input 顺序错误收到 HTTP 400。修正为“整批 call 在前、对应 output 在后”并加入 Mock 回归后，真实 DeepSeek 重新运行 3/3 通过。这说明重复真实实验不仅测模型波动，也能发现 Fake/Mock 未覆盖的供应商协议约束。

常见追问：发邮件、提交 Issue 这类任务怎么验证？

> TaskVerifier 是协议，不是一个万能判断器。代码任务可以检查测试退出码；外部系统任务通常使用领域 verifier，通过 API 回读资源并断言收件人、标题、正文或 Issue 状态。工具返回成功只证明一次 API 调用完成，任务 verifier 检查的是最终业务后置条件。对于“对方是否阅读邮件”这种系统无法观察的结果，只能验证服务已接受或邮件出现在 Sent 中，不能虚构更强保证。
