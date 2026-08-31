# Tool Output Truncation

一个工具结果有两种去向，不能混为一谈：

```text
完整 ToolResult  -> ToolExecution / tools.jsonl trace
受限预览         -> state.messages -> 下一轮 ModelContext
```

`ToolOutputPolicy` 在工具执行成功或失败后，把结果序列化为模型可见的 observation。超过 `max_tool_output_chars` 时保留头尾片段和元数据，并产生 `tool_output_truncated` event，记录原始、保留和截断的估算 token。

这样做的目的是保护 Context Budget，而不是删除运行事实。调试或 replay 仍可从 trace 读取完整结果。当前默认限制为 4,000 字符；可通过 CLI 的 `--max-tool-output-chars` 修改。
