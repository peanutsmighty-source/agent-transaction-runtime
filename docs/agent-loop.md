# 显式 Agent Loop

阶段 A 的 loop 没有隐藏在框架调用中：每轮从 `AgentState` 构建 `ModelContext`，请求模型取得一个包含多个 `ResponseItem` 的 `ModelResponse`，执行其中的工具并把结构化 observation 写回 State。

```python
while state.status == RUNNING:
    context = context_builder.build(state)
    response = await model.generate(context, tools)
    for item in response.items:
        if isinstance(item, ToolCall):
            result = await tool_runtime.execute(item)
            append_tool_messages_and_events(item.call_id, result)
        elif isinstance(item, FinalAnswer):
            complete()
```

`step` 表示模型轮次，而不是工具调用次数。同一轮可以产生多个 ToolCall；第一版按响应顺序串行执行。`call_id` 将 ToolCall、ToolResult、Message 与 trace 关联起来。

`AgentState` 是运行时事实与历史；`ModelContext` 只是本轮模型输入的派生视图。现在 ContextBuilder 保留完整消息历史，后续的观察、截断和 compaction 会在不破坏这一边界的前提下演进。

所有关键转换都会生成 `AgentEvent`，并由 TraceWriter 持久化，因此可以在不重新调用模型的情况下检查一次 run 发生了什么。
