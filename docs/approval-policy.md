# Approval Policy

模型产生 ToolCall 只代表“请求执行”，不代表拥有执行权限：

```text
Model ToolCall
      ↓
ApprovalPolicy
  ├─ allow            → ToolRuntime.execute
  ├─ require_approval → ToolResult(error=approval_required)
  └─ deny             → ToolResult(error=tool_denied)
```

第一版是非交互式策略：只有明确识别的文件 read/list 默认允许；文件 write、shell 和未分类工具默认要求授权；CLI 可以为当前 run 预授权。授权失败会作为普通 ToolResult 返回给模型，因此 Agent 可以解释阻塞或选择其他方法，Runtime 不会崩溃。这是 fail-closed：新增工具不会因为忘记配置风险等级而自动获得执行权。

## Approval 不等于 Sandbox

ApprovalPolicy 回答“是否同意执行”，但不能限制进程执行后能访问什么。当前已经用独立的 `SandboxRunner` 协议拆开执行后端，但唯一实现 `HostRunner` 不提供隔离；仍需容器或 OS 级 Runner 才能强制限制文件系统、网络和子进程。
