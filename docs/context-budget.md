# Context Budget

Context Window 不是全部可用于输入：模型还需要空间生成下一轮输出。因此 `ContextBudget` 计算：

```text
effective_input_tokens = max_tokens - reserved_output_tokens
compact_trigger_tokens = effective_input_tokens * compact_threshold
```

例如默认设置：

```text
max_tokens              128,000
reserved_output_tokens   16,000
effective input          112,000
compact threshold          75%
trigger                   84,000
```

`ContextBudget` 自己仍不删除或总结内容；它只发出 `context_budget_checked` 与 `context_compaction_required`。如果运行时配置了 `SlidingWindowCompaction`，该 policy 会消费触发信号，生成更小的模型输入视图，并发出 `context_compaction_applied`。
