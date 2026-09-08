# Responses-compatible Provider

## 它解决什么问题

`FakeModelClient` 能验证 Agent Loop 的状态转换，但它只播放预先写好的动作，不能让模型根据刚刚读到的文件或测试结果决定下一步。`ResponsesModelClient` 把同一个显式 Loop 接到兼容 Responses API 的供应商：Loop 仍负责状态、工具、审批和停止，SDK 只负责模型网络协议。

这里必须区分三个概念：Responses 是协议，OpenAI/DeepSeek 是供应商，OpenAI Python SDK 是当前使用的协议传输实现。供应商通过 `provider_name`、`base_url`、模型和密钥配置选择，不会渗透进 Agent Loop。

## SSE、分片和中途断开

SSE（Server-Sent Events）是一条长期 HTTP 响应。模型不必等全部内容生成完才返回，而是连续发送事件。例如：

```text
response.output_text.delta   "work"
response.output_text.delta   "space"
response.output_item.done    完整 message item
response.completed           整个响应正式完成
```

“分片”有两层：模型把输出拆成多个事件，网络又可能把一个事件的 JSON 拆成任意字节块。接收端不能假设一次读取就是一个完整事件。官方 SDK 负责把字节重新组装为事件；本项目再按 `output_index` 收集完成的 output item。

“中途断开”表示连接在 `response.completed` 前结束。即使此前已经收到一部分文字，也不能当作成功，否则 Agent 可能把半句话当最终答案，或漏掉尚未到达的工具参数。当前 Provider 因此只把 `response.completed` 视为成功终点；提前 EOF 会抛出 `ModelStreamIncompleteError`，由 Agent Loop 记录为 runtime error 并停止。

## 重试和超时

一次模型请求可能在两处失败：

```text
建立响应流之前：429、5xx、连接失败、等待响应头超时
建立响应流之后：只收到部分 SSE，随后断线或收到 error 事件
```

第一类失败会统一映射为 `ModelProviderError`。SDK 仍保留可配置的 `max_retries`，但 CLI 默认设为 0，由 AgentLoop 外层的 `ModelRetryPolicy` 根据 retryable、最大尝试、退避和 token 预算决策，避免 SDK 与 Runtime 两层重试次数相乘。

第二类失败不在 Provider 内盲目重放。Provider 丢弃未完成缓冲并标记为 retryable；高层 Policy 可以重发同一 Context，因为任何 ResponseItem 和 ToolCall 都还没有提交到 AgentState。供应商侧仍可能已经计费，因此尝试次数和估算 token 必须有界。

Provider 错误写入 `runtime_error` event 时包含：错误类型、provider、稳定错误码、HTTP 状态码、request ID、`retryable` 和供应商返回的结构化错误 detail。这让 trace 能区分“模型拒绝任务”“被限流”和“网络坏了”，也能定位 400 对应的具体协议字段。detail 只来自错误响应体，不记录请求头或 API key。

## 一次工具往返的数据流

```text
Responses function_call(call_id, name, JSON arguments)
  -> runtime ToolCall
  -> ToolRuntime 执行
  -> State 保存结构化 TOOL_CALL + TOOL_RESULT
  -> 下一次 Responses 请求发送 function_call + function_call_output
```

调用和结果共享 `call_id`，因此多次调用不会配错。它们也是同一个 ContextUnit，Compaction 不会只留下其中一半。

一轮产生多个调用时，下一轮输入保持批次顺序：先发送该响应的全部 `function_call`，再按调用顺序发送全部 `function_call_output`。不能交错成 `call1/output1/call2/output2`；真实 DeepSeek 重复实验曾因此在第二轮返回 HTTP 400，Mock 回归现已固定覆盖这一协议边界。

## 为什么这样设计

成熟 Agent runtime 通常把供应商 API 适配层和 Agent 控制循环分开。这里也不让 SDK 接管工具执行：Responses item 被显式映射为项目自己的 `AssistantMessage`、`ReasoningSummary`、`ToolCall` 和 `FinalAnswer`。这样 Fake 与真实 Provider 能复用相同 Loop 和测试。

本项目采用一个明确的首版规则：响应包含 function call 时，message 是过程消息；没有 function call 时，最后一条 message 是 `FinalAnswer`。这能驱动单 Agent Loop，但它是 runtime 约定，不是 Responses API 自带的“最终答案”标记。

## 当前验证与限制

本地 Mock Responses Server 已验证：

- SSE 在任意字节边界被拆开后仍能还原；
- tool call/result 在下一轮仍保持结构化；
- 完成事件前断线会失败，不会误报成功；
- 完整 Agent Loop 能经过真实 HTTP/SSE 适配层执行工具并完成第二轮。
- SDK retry 关闭时，Runtime Policy 能从 429 和 SSE 提前 EOF 恢复；400、timeout 和流内 error 的结构化失败边界也有覆盖。

这些 Mock 测试不访问外部服务、没有 API 费用，并能稳定复现异常。2026-09-04 另使用 DeepSeek 官方 Responses API 与 `deepseek-v4-flash` 完成两组真实 smoke：最小文本响应，以及包含一次 FileTool 调用和第二轮最终回答的完整 Agent Loop。真实限流、长时间运行和生产负载仍未实测。

## 手动运行

以 DeepSeek 和仓库外密钥文件为例：

```powershell
python -m runtime run "inspect the workspace" `
  --workspace examples/demo_project `
  --provider responses `
  --provider-name deepseek `
  --api-base-url https://api.deepseek.com `
  --api-key-file E:\tmp\key.txt `
  --model deepseek-v4-flash
```

文件写入和 shell 仍然默认需要授权；选择真实 Provider 不会绕过 Approval Policy。

真实 smoke test 默认跳过。只有同时提供三个环境变量才会发起一个最小外部请求：

```powershell
$env:AGENT_RUNTIME_RESPONSES_SMOKE = "1"
$env:RESPONSES_SMOKE_PROVIDER = "deepseek"
$env:RESPONSES_SMOKE_MODEL = "deepseek-v4-flash"
$env:RESPONSES_SMOKE_BASE_URL = "https://api.deepseek.com"
# RESPONSES_API_KEY 需要已经存在于环境中；不要写进仓库。
python -m pytest tests/test_openai_smoke.py -q
```

模型 ID 由运行者显式选择，测试不会猜测“当前默认模型”。测试关闭 SDK 自动重试，避免一次 smoke 意外扩大为多次计费请求。

旧的 `OpenAIResponsesClient`、`--provider openai`、`--openai-base-url` 和旧 smoke 环境变量仍作为兼容入口保留，但新代码应使用通用名称。
