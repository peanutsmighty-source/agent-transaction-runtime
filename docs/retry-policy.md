# Provider 无关的模型重试策略

## 它解决什么问题

一次模型请求可能因为 429、临时 5xx、连接失败、timeout 或 SSE 中途断开而没有产生可提交的 `ModelResponse`。如果 Runtime 立即把整个 Agent 标记失败，短暂网络抖动会破坏本来可完成的任务；如果把整个 Agent step 盲目重跑，又可能重复执行已经完成的写文件、发邮件或创建 Issue。

## 当前数据流

```text
固定 ModelContext
  -> model.generate(attempt=1)
  -> 归一化 ModelProviderError
  -> ModelRetryPolicy.evaluate
       ├─ retryable != true：停止
       ├─ 达到 max_attempts：停止
       ├─ 下一次将超过估算 token budget：停止
       └─ 允许：指数退避后重发同一 ModelContext
```

Retry 只包裹 `model.generate()`。Provider 在收到 `response.completed` 前不会向 Loop 提交 ResponseItem，因此流中断后的部分文字或 ToolCall 不会写入 State，也不会执行工具。上一轮已经完成的工具不属于这个重试边界，不会被重新执行。

## 为什么在 Runtime 层

Provider adapter 负责把 HTTP/SSE 和供应商错误映射成统一的 `ModelProviderError`；Policy 根据 `retryable`、尝试次数和预算决策。这样 DeepSeek、OpenAI-compatible 服务和 Fake/Mock 都使用同一套重试语义。CLI 默认关闭 SDK 自动重试，启用最多三次 Runtime 尝试，避免两层重试相乘。

## 预算与退避

当前采用有上限的指数退避：`base_delay * 2^(attempt-1)`，再限制在 `max_delay` 内。测试使用零延迟保持确定性。可选 token budget 按 `ModelContext.estimated_tokens * 尝试次数` 计算，它只能约束输入规模，无法获知失败请求是否已计费，也没有计算输出 token。

## 不重试什么

- 400 和其他明确标记 `retryable=false` 的协议/参数错误；重发相同请求只会再次失败。
- 普通 `RuntimeError`；只有 Provider 归一化并明确标记的模型错误进入 Policy。
- ToolResult 失败；工具副作用与幂等性需要独立 Tool Retry Policy，当前交给模型观察错误后决定下一步。
- 已经提交的 ModelResponse；避免重新解释或重复执行其中工具。

## 事件和证据

每次实际请求产生 `model_request(attempt)`。允许重试产生 `model_retry_scheduled`；不允许或预算耗尽产生 `model_retry_exhausted`，随后保留原始错误进入 `runtime_error`。Mock HTTP 已覆盖 SDK retry 关闭时由 Runtime 从 429 恢复，以及 SSE 提前 EOF 后重新请求且不提交部分文本。

## 当前边界

- 没有随机 jitter，大量并发 Agent 可能同步重试形成惊群。
- 没有读取供应商 `Retry-After`。
- 字符 token 估算不等于真实计费。
- 未统计失败请求的实际费用，因为供应商通常不返回 usage。
- 尚未实现跨进程 checkpoint；进程退出后不能继续 retry。
- 工具层幂等重试尚未实现。
