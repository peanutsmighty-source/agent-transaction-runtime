# DeepSeek Responses 真实 Smoke Test — 2026-09-04

## 目的

Mock Server 能证明协议映射在可控输入下正确，但不能证明真实供应商、TLS、鉴权、SDK SSE 解析和模型工具选择能够一起工作。本实验使用真实 DeepSeek API 验证这条边界。

密钥只从 workspace 外的临时文件读入测试进程环境，没有回显，也没有写入仓库。测试设置 `max_retries=0`，避免失败时悄悄扩大请求次数。

## 配置

- Provider protocol：OpenAI Responses-compatible
- Base URL：`https://api.deepseek.com`
- Model：`deepseek-v4-flash`
- SDK：项目锁定范围内实际安装的 OpenAI Python SDK
- 日期：2026-09-04（Asia/Shanghai）

DeepSeek 官方文档说明该 endpoint 原生支持 Responses API，且该模型支持 `function` 工具和流式 SSE。

## 实验一：最小真实响应

测试直接通过 `ResponsesModelClient.generate()` 发送一条短任务，并检查：

- 返回项最后一项是非空 `FinalAnswer`；
- 返回真实 `response_id`；
- 流正常到达 `response.completed`。

结果：`1 passed in 4.52s`。

## 实验二：完整 Agent Loop 工具往返

临时 workspace 中创建 `smoke-marker.txt`，指示模型必须先调用 FileTool 列出目录，再根据工具结果回答。

实际数据流：

```text
AgentLoop
  -> DeepSeek Responses
  -> function_call(file: list .)
  -> FileTool 返回目录内容
  -> function_call_output
  -> DeepSeek Responses 第二轮
  -> FinalAnswer（包含 smoke-marker.txt）
```

断言确认工具只执行一次、执行成功、Agent 状态为 completed，最终答案引用了真实工具观察结果。

结果：`1 passed, 1 deselected in 5.26s`。

Provider 泛化完成后，又使用新的 `RESPONSES_*` 环境变量和 `provider_name="deepseek"` 重跑最小真实请求，结果为 `1 passed, 1 deselected in 4.25s`。这证明重命名和配置抽象没有破坏真实兼容性。

## 结论和限制

这证明当前框架已经能由真实模型驱动一次完整工具循环，而不只是“HTTP 接口能连通”。它仍不是 benchmark：任务非常短，没有验证代码修改、shell、审批、Compaction、长上下文、限流或长时间稳定性。实验后已将公共类名、CLI 和错误 provider 字段泛化为 Responses-compatible 配置，并保留旧 OpenAI 名称兼容。
