# Agent Runtime Lab — Todo（按依赖和优先级排序）

## 当前结论

核心架构骨架已经形成：显式 Agent Loop、State/Context 分离、模型 Provider、工具、审批、Trace、Context Budget 和两种 Compaction 基线都可运行。

但当前只能证明“最小集成闭环可用”，还不能证明“复杂 Coding Agent Runtime 可用”。在 P0 完成前，不开始完整 benchmark，也不进入 Multi-Agent。

## P0：先证明单 Agent Loop 能完成真实 Coding Task

这些是当前最高优先级，是“框架能用”的验收门槛。

- [x] 定义任务完成契约 `TaskVerifier`：模型说“完成”不是成功；由文件状态、测试退出码或组合断言独立验收；失败反馈给下一轮继续修正。
- [x] 建立确定性的 Loop 场景矩阵：
  - [x] 一轮多个 ToolCall，验证顺序、call_id 配对和部分失败。
  - [x] 工具参数错误后，模型修正参数并继续。
  - [x] 文件不存在、shell 非零退出、tool timeout 后恢复。
  - [x] 写文件或 shell 被 Approval Policy 拒绝后的行为。
  - [x] 重复失败、max_steps 和无最终答案等停止分支。
  - [x] 模型文字、ReasoningSummary 和 ToolCall 混合输出的顺序。
- [x] 建立真实 Coding Task 验收场景，而不是大规模 benchmark：
  - [x] 只读多文件诊断（Fake Model + 真实文件后置条件）。
  - [x] 单文件 bug 修复并运行测试（真实子进程与独立 verifier）。
  - [x] 第一次修复失败后读取错误并二次修复。
  - [x] 多文件实现与测试同步修改。
  - [x] 真实模型场景重复运行，记录成功率和失败原因：
    - [x] DeepSeek 单文件修复重复 3 次，独立测试验收 3/3 通过。
    - [x] DeepSeek 只读多文件诊断重复 3 次，trace 移出 workspace 后 3/3 通过。
    - [x] DeepSeek 多文件实现重复 3 次，独立测试验收 3/3 通过。
    - [x] DeepSeek 首次验证失败后二次修复故障注入场景重复 3 次，3/3 通过。
- [x] 实现取消生命周期基线：asyncio 取消传播到模型流和工具 Runner，清理后写入 `CANCELLED` 状态与 trace；产品 cancel handle、Windows 任意孙进程和 Docker 实机清理仍属后续边界。
- [x] 实现 Provider 无关的高层 Model Retry Policy：区分 retryable 请求失败、流中断和工具副作用；支持最大尝试、指数退避、估算输入 token 预算和 trace。失败请求真实费用、Retry-After、jitter 与 Tool Retry 仍为后续边界。

## P1：让长任务中的 Context 和调试能力可信

P0 证明短任务 Loop 后，再验证长任务不会因 Context 管理失真。

- [x] 定义 Memory/Retention Contract：将 pinned/working/episodic 保留等级与 verified/derived/stale/contradicted 可信状态分离；`MemoryRetentionPolicy` 对技术选型等 actionable use 返回强制核验与阻断决定。接入 State、Artifact read-back 和 Loop enforcement 仍属后续项。
- [ ] 建立错误摘要注入与 long-horizon retention 测试：测量关键事实遗漏、虚构、过期事实使用、核验触发和多次压缩后的任务成功率。
- [ ] 实现 Structured Working State 与 provenance schema：来源 ID 只是可追溯指针，高风险 claim 还必须有版本/hash 和核验策略。
- [ ] 实现 Artifact Store 与有界 Evidence Retrieval：完整工具输出留在 Context 外，只按 token/item/call 预算取回当前所需片段。
- [ ] 实现 Verification Policy：副作用前、完成前、claim 过期或冲突时由 Runtime 强制 read-back；不能等待模型自行怀疑摘要。
- [ ] 完成真实 Full Summary 运行链路：
  - [x] 通过隔离、无工具的模型轮次接入 Responses Summarizer，并支持 CLI 独立 `--summary-model`。
  - [ ] 为摘要调用实现 timeout、有限重试和费用边界。
  - [ ] 在质量契约和错误摘要测试建立后，实现以原始历史与配置为键的派生摘要缓存，不把摘要变成事实来源。
- [ ] 实现分层 Structured Compaction：显式保存约束、决定、失败经验、修改文件和下一步；阶段 checkpoint 可版本化并定期从原始事实重建，避免无限摘要旧摘要。
- [ ] 对同一历史比较 Sliding Window、Full Summary 和 Structured Compaction，记录遗漏、幻觉、token、延迟和费用。
- [ ] 实现 Trace Replay：用已记录的模型响应和工具 observation 重放 Loop，复现失败而不再次付费。

## P2：工程成熟度和正式评测

- [ ] 在 P0/P1 场景稳定后建立 Coding Task benchmark；区分任务成功率、工具效率、token、延迟和恢复能力。
- [ ] 增加交互式 Approval UI 和单次操作级授权，而不只是 run-level 预授权。
- [ ] 用 provider tokenizer 或真实 usage 校准 Context Budget，保留当前字符估算作为离线基线。
- [ ] 增加生产网络与限流测试：长连接、代理、DNS、真实 429、服务抖动和长任务稳定性。

## P3：单 Agent 稳定后再进入 Multi-Agent

- [ ] 定义 spawn、wait、cancel、结果回收和父子 trace 协议。
- [ ] 处理并发 Context、预算分配、子任务失败传播和重复工作。
- [ ] 用同一任务比较单 Agent 与 Multi-Agent 的成功率、成本和延迟。

## 暂缓：Docker Sandbox 实机验收

原因：当前机器启动 Docker Desktop 时内存不足导致系统崩溃，等待更换内存后继续。

- [x] 将 Docker Desktop 安装到非系统盘。
- [x] 将 Docker WSL 数据目录配置到非系统盘。
- [x] 实现 `DockerSandboxRunner` 及参数级单元测试。
- [ ] 更换内存后确认 Docker daemon 稳定启动。
- [ ] 确认镜像、容器和 WSL 虚拟磁盘实际写入 E 盘。
- [ ] 准备并固定 Sandbox 测试镜像，不使用浮动 tag。
- [ ] 验证 workspace 外文件不可读、默认网络不可达、非 root、只读 rootfs。
- [ ] 验证 CPU、内存、进程数、超时和取消后的容器清理。
- [ ] 将真实验收结果写入 `docs/experiments/`。

在以上项目完成前，不把 DockerSandboxRunner 标记为经过真实安全验收。

## 已完成基线

- [x] 显式 Agent Loop、AgentState、事件和结构化 Trace。
- [x] FileTool、ShellTool、ToolApprovalPolicy 和重复失败告警。
- [x] Context Inspector、Context Budget 和 Tool Output Truncation。
- [x] Sliding Window、ContextUnit 和 Full Summary + Fake Summarizer 基线。
- [x] Responses-compatible 流式 Provider 和结构化 tool call/result 往返。
- [x] Mock HTTP/SSE：分片、断线、429、500、timeout 和错误诊断。
- [x] DeepSeek `deepseek-v4-flash` 最小响应和真实 FileTool Loop smoke。
- [x] Responses Provider 通用 CLI、密钥来源和旧 OpenAI 名称兼容。
