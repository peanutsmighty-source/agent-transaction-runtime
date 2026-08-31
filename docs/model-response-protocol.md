# Model Response Protocol

模型层不再返回单个 `Action`，而是返回一次完整模型轮次：

```text
ModelResponse
├─ response_id
├─ usage
└─ items[]
   ├─ AssistantMessage
   ├─ ReasoningSummary
   ├─ ToolCall(call_id, name, arguments)
   └─ FinalAnswer
```

## 为什么这样设计

真实模型 API 的一次响应可能同时包含说明文字和多个工具调用。把它压扁成单个 Action 会丢失响应边界、调用关联和 usage，也很难支持 streaming、并行工具与 replay。

`call_id` 是工具调用和结果之间的稳定关联键。即使同一轮调用两次相同工具，runtime 也能明确知道每个 ToolResult 属于哪次调用。

## 当前边界

- 多个 ToolCall 已支持，但按 item 顺序串行执行。
- `ReasoningSummary` 保存的是可公开的推理摘要，不代表或要求模型暴露隐藏思维链。
- 不允许同一响应同时包含 ToolCall 和 FinalAnswer，避免“是否应先执行副作用”的语义歧义。
- streaming 和 provider-specific item 尚未实现。
