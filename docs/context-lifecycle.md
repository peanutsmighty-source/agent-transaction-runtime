# Context 生命周期与 Inspector

每一轮模型调用前，`ContextBuilder` 从 `AgentState` 派生 `ModelContext`。Context 由以下类型组成：

- `system`：Agent 必须遵守的运行规则。
- `task`：本次用户任务。
- `assistant`：此前 Agent 发起的工具调用等动作记录。
- `tool_result`：工具返回的 observation，例如文件内容、测试失败输出。

`ContextObserver` 在 Context 建好后进行只读统计：总 token、按类型 token 和最大 item。它不删除、总结或重排任何内容；因此可以在引入 compaction 前建立基线。

```text
AgentState -> ContextBuilder -> ModelContext -> ContextObserver -> ModelClient
```

在 CLI 中使用 `--show-context` 查看本次 run 的最后一轮统计。完整的每轮 ContextItem 与 `context_observed` event 都保存在 trace 内。
