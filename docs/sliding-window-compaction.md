# Sliding Window Compaction

## 它解决什么问题

Agent 每轮都会把此前消息和工具结果再次交给模型。任务越长，输入越大；超过模型 Context Window 后，即使 Agent 的逻辑仍然正确，也无法继续调用模型。

Sliding Window 是最简单的压缩基线：永久保留 system 与 task，只让模型看到最近的一段历史。完整消息仍保存在 `AgentState` 和 trace 中，因此“模型忘记了什么”与“系统是否还保留事实”是两回事。

```text
完整 AgentState: system task old-1 old-2 recent-1 recent-2
                           ↓ 生成模型视图
ModelContext:      system task             recent-1 recent-2
```

## 为什么不直接修改 AgentState

删除 State 历史会同时破坏审计、回放和后续策略比较。当前实现只压缩派生出的 ModelContext：模型输入变小，但 trace 仍能还原发生过的动作。这接近成熟 Agent 将 durable history 与 model working context 分开的设计。

## 触发与目标

`ContextBudget` 仍只负责判断是否超过 trigger。超过后，SlidingWindowCompaction 尝试把模型输入缩到 `effective_input_tokens * compaction_target_ratio`，默认是有效输入预算的 50%。system、task 和至少最后两个历史 unit 优先保留，所以当这些必要内容自身过大时，最终结果可能仍高于目标；实现会如实记录，而不是偷偷截断指令。

`ContextUnit` 是压缩的最小原子：普通 assistant 消息各自形成一个 unit；共享同一 `tool_call_id` 的连续 tool call 与 tool result 形成一个 unit。窗口只能保留整个 unit 或删除整个 unit，不能留下半次工具交互。

Trace 会保存 `pre_compaction` 和 `model_input` 两种 context view，并产生 `context_compaction_applied` event，记录删除的 item ID 和 token 数。

## 主流做法与限制

保留最近窗口是 Coding Agent 常见的低成本基线：无需额外模型调用、结果确定、容易测试。缺点是按时间新旧决定价值，旧约束、关键决策和早期失败原因都可能被遗忘。工具调用边界已经能够保持完整，但多个工具调用属于同一个模型 turn 的更高层分组尚未实现。因此它不是最终策略，而是后续 Full Summary 与 Structured Compaction 的对照组。
