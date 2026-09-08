# Full Summary Compaction

## 它解决什么问题

Sliding Window 只按时间保留最近历史。早期的关键约束、决定或失败经验即使仍然重要，也可能因为“太旧”而从模型输入中消失。

Full Summary 把较早历史压缩成一段较短的自然语言，同时原样保留 system、原始 task 和最近若干个 `ContextUnit`：

```text
system + task + older history + recent history
                       ↓ summarize(older history)
system + task + SUMMARY       + recent history
```

例如，older history 包含“公开 API 不得变化”和一次失败的文件修改，摘要可以保留这两点；最近的工具调用与结果仍按原文交给主模型。

## 为什么接口必须是异步的

真正的摘要通常需要调用另一个模型，模型请求会等待网络。因此 `CompactionPolicy.compact()` 现在是异步协议，`AgentLoop` 显式等待压缩完成。把它伪装成同步纯函数，会迫使未来 provider 在内部阻塞或把模型调用偷偷移到别处。

`Summarizer` 是单独的可注入协议。`FakeSummarizer` 返回测试预先给定的结果，使成功、失败和超预算路径都确定且免费；`ModelSummarizer` 则把待摘要历史序列化为数据，通过独立、无工具的模型轮次调用任意 `ModelClient`。CLI 在 Responses Provider 下支持 `--compaction full-summary`，并可用 `--summary-model` 选择独立模型。

摘要系统指令明确要求把历史视为数据而不是新指令，以降低旧工具输出或消息中的 prompt injection 风险。它只能读取传入的 older items，不能访问 ToolRuntime，也不写 AgentState。模型仍可能不遵守要求，因此 tool call 和无正文响应会被拒绝，由 Full Summary 走显式回退。

## 事实与视图

Summary 使用独立的 `ContextItemType.SUMMARY`，但它只是当前模型输入中的派生项，不会写入 `AgentState.messages`。因此：

- `AgentState` 继续保存全部原始消息。
- trace 保存压缩前的 `pre_compaction` 和压缩后的 `model_input`。
- event 记录被总结的 item ID、原样保留的 item ID、压缩前后 token 和 summary item ID。

首版每次都从完整旧历史重新生成摘要，不复用上一次摘要。这样不会把摘要误差反复总结并累积，也更容易验证；接入真实模型后，它会增加延迟和费用，后续可以加入只缓存派生结果的 Compaction Runtime。

## 不拆分工具交互

历史先通过 `group_context_units()` 分组。共享 `tool_call_id` 的连续 tool call/result 是一个不可拆分单元，然后才切分 older 与 recent。Summarizer 要么同时看到调用和结果，要么两者都留在 recent，不能只看到一半。

## 失败与 fail-closed 回退

system、task 和最近配置数量的 unit 永远优先保留。剩余 token 才是摘要预算。以下情况不会把不合格摘要交给主模型：

- summarizer 抛出异常；
- 返回空摘要；
- 没有可总结的旧历史；
- 必须保留内容已经用完目标预算；
- 摘要自身超过剩余预算。

这些情况产生 `context_compaction_failed` event，包含稳定错误码、错误细节和 `fallback_strategy`，随后使用 `SlidingWindowCompaction` 缩小当前视图。回退也只改变 Context，不删除 State。

## 主流做法与本实现的差别

长程 Agent 使用压缩、近期窗口或外部状态跨越 context window 是常见做法。[OpenAI Responses compaction](https://developers.openai.com/api/docs/guides/latest-model#4-compaction-extending-effective-context) 把压缩结果作为不可解析的 opaque item 继续传入；[Anthropic 的公开指南](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices#context-awareness-and-multiwindow-workflows)也讨论 Claude Code/agent harness 的自动 compaction 和跨窗口状态保存。

本项目选择可读的自然语言 `SUMMARY`，目的是观察摘要到底保留和遗漏了什么。代价是摘要可能产生事实错误，也可能遗漏结构化约束。真实 Provider 链路已经接通，但摘要预算目前只是 prompt 要求与返回后的估算校验，并非 Provider 级硬输出上限；独立 retry、缓存、成本统计、真实 tokenizer 和质量评测仍未实现。后续 Structured Compaction 与 long-horizon retention 测试将专门比较这些问题。
