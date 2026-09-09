# Context Compaction 与 Memory 风险登记

## 结论

Sliding Window、Full Summary、分层摘要和 Structured Compaction 都不是无损记忆。它们只能改变模型本轮看到的视图，不能保证模型知道自己遗漏了什么，也不能保证模型会主动核查错误摘要。

本项目采用的目标不是“让摘要成为事实”，而是：

```text
不可变原始事实 + 可丢弃派生记忆 + 风险触发核验 + 有界证据取回
```

无法被自动检测的错误仍是残余风险，必须通过任务级验证、长程评测和必要时的人类确认暴露出来。

## Provenance 能做什么，不能做什么

Provenance 是一条派生事实的来源记录。例如：

```json
{
  "claim": "并发测试已经通过",
  "source_type": "tool_result",
  "source_id": "tool_result_42",
  "artifact_id": "artifact_test_17",
  "source_hash": "sha256:...",
  "observed_at": "2026-09-09T10:00:00+08:00",
  "workspace_version": "git:abc123"
}
```

它提供“去哪里核查”和“这条结论基于哪个版本”，但不提供以下保证：

- 摘要中的 claim 一定正确；
- 模型能够意识到 claim 可疑；
- 模型会主动使用 source ID；
- 来源本身仍然代表当前环境；
- 取回来源后模型一定正确理解。

因此 provenance 必须与 Verification Policy 配合，不能只把 ID 交给模型后期待模型自行判断。

## 错误摘要未被发现时怎么办

若摘要错误、模型没有怀疑、用户也没有发现，单靠 provenance 无法自动修复。这是不可消除的残余风险。Runtime 能做的是减少它影响高风险行为的机会：

### 风险分级

- **必须核验**：会触发外部副作用、改变安全边界、决定任务完成，或声称文件/测试/部署状态的事实。
- **条件核验**：会改变实现方向的重要约束、架构决定和失败原因。
- **允许暂信**：低风险背景叙述；错误不会立即造成副作用，且稍后可以修正。

### 强制核验点

核验不依赖模型“想起来”，由 Runtime 在固定边界触发：

1. 执行不可逆或外部副作用工具前，重新读取关键参数和幂等状态。
2. `FinalAnswer` 前，由 TaskVerifier 重新检查文件、测试和业务后置条件。
3. Structured Memory 中的高风险 claim 被选入当前计划时，按 provenance 拉取来源。
4. claim 的 workspace version、文件 hash 或有效期过期时，标记为 stale，禁止直接使用。
5. 两条 claim 冲突时不静默覆盖，进入 `contradictions` 并要求回读或人工确认。

## 取回事实会让 Context 再次变大吗

会。Retrieval 不是免费恢复记忆，而是把需要的证据重新放入当前 Context。区别在于它应当是选择性、限额和短期的：

```text
全部历史长期常驻：每一轮反复支付全部 token
按需证据取回：只在相关决策点支付少量 token
```

推荐的有界取回流程：

1. 先用元数据、关键词、文件路径和 source ID 选择候选。
2. 默认返回短片段和上下文范围，不返回整个 Artifact。
3. 为一次取回设置 token 数、item 数和调用次数预算。
4. 高风险 claim 优先获得预算；低风险材料可延后或省略。
5. 证据只进入近期工作窗口，阶段结束后可从活跃 Context 移出；原始事实仍留在 State/Artifact Store。
6. 如果证据过大，先返回索引，再由模型请求更小范围。

Context 仍会增长，但增长从“随完整历史单调增加”变成“围绕当前问题短暂增加”。

## 推荐的混合 Context 结构

```text
Pinned 原文
  system、原始 task、权限、用户硬约束、验收条件

Structured Working State
  当前目标、决定、修改文件、失败尝试、阻塞、下一步

Hierarchical Checkpoints
  按阶段生成的摘要和一个短全局索引，每条 claim 带 provenance

Retrieved Evidence
  针对当前高风险决定取回的原始片段

Recent Raw Window
  最近完整模型 turn、tool batch 和 verification

External Facts
  文件系统、Git、测试结果、Trace、Artifact；不永久常驻 Context
```

Sliding Window 保留为确定性兜底；Full Summary 保留为可读基线和阶段交接；两者都不作为最终长期记忆方案。

## 分层摘要的误差控制

分层摘要解决一次把全部旧历史交给 Summarizer 的输入上限问题，但会引入摘要再摘要的误差传播。应采用以下约束：

- 阶段摘要基于不可变的原始 ContextUnit 范围，并记录范围 hash。
- 全局索引主要保存阶段位置和少量稳定 claim，不复制全部细节。
- Pinned 约束和环境事实不通过自由文本摘要更新。
- 不在原摘要上原地覆盖；生成带版本的新 checkpoint。
- 定期从原始事件和 Structured State 重新构建，而不是无限递归总结旧摘要。
- 每个层级分别设置 token、年龄、作用域和失效规则。

## 已发现问题与应对方案

| 风险 | 应对方案 | 验证方式 | 当前状态 |
|---|---|---|---|
| 摘要错误但无人意识到 | 高风险 claim 由 Runtime 强制回读，不能依赖模型自觉 | 故意注入错误摘要，断言副作用/完成前发生核验 | 未实现 |
| provenance 只有 ID、模型不主动查询 | 建立 Verification Policy 和固定核验边界 | Fake Model 不请求来源时，Runtime 仍触发 verifier | 决策 Policy 已实现；Loop enforcement 未实现 |
| 取回证据导致 Context 再增长 | 有界检索、片段读取、阶段后移出活跃窗口 | 统计 retrieval token 峰值和任务成功率 | 未实现 |
| Full Summary 输入本身超过窗口 | 按 ContextUnit 分块、阶段 checkpoint、输入硬预算 | 超长旧历史不向 Provider 发送超限请求 | 未实现 |
| 多层摘要误差累积 | 原始来源、版本化 checkpoint、周期性从原始事实重建 | 多次 compaction 后测遗漏和虚构 | 未实现 |
| 用户硬约束被摘要遗漏 | Pinned Context 原文保留 | 多轮压缩后逐字/语义检查硬约束 | 未实现 |
| 文件和测试状态过期 | 文件 hash、Git version、时间戳；使用前 read-back | 修改文件后旧 claim 自动变 stale | 未实现 |
| Tool Output 预览丢失中间证据 | 完整输出进入 Artifact Store，Context 只放索引和片段 | 从被截断中段恢复指定错误 | 未实现 |
| Prompt injection 混入历史 | 摘要器无工具、历史标为数据、schema 白名单；高风险事实仍需核验 | 对抗性历史不能触发工具或伪造 pinned claim | 部分实现 |
| 摘要输出过长或费用失控 | Provider 硬输出上限、timeout、retry、run 级 token/费用预算 | 429/timeout/超长输出和预算耗尽测试 | 部分实现 |
| token 字符估算不准 | Provider tokenizer/usage + 安全余量 | 与真实 usage 校准误差分布 | 未实现 |
| Sliding Window 回退删除关键事实 | Pinned/priority packing，而非纯时间保留 | 摘要失败时关键约束仍保留 | 未实现 |
| 缓存复用错误或过期摘要 | cache key 包含来源 hash、模型、prompt、schema、workspace version | 任一维度变化产生 cache miss | 未实现 |
| 不同记忆相互冲突 | 显式 contradiction 集合、来源优先级、禁止静默覆盖 | 冲突输入触发回读/人工确认 | 未实现 |
| 注意力偏离当前目标 | 短 Active Goal、阶段边界、重复动作和无进展检测 | 噪声历史下的目标遵循率 | 部分实现 |
| 多次压缩质量持续下降 | compaction 次数/质量阈值，必要时生成交接并开启新 Context | 不同压缩次数下的成功率曲线 | 未实现 |
| 跨任务记忆污染 | run/thread/project/user 作用域、TTL、权限和删除规则 | 不同作用域间不可见性测试 | 未实现 |
| 摘要发送到不同 Provider 造成泄露 | Provider allowlist、数据分级、脱敏和本地摘要选项 | 敏感 fixture 不离开允许边界 | 未实现 |

## 开发顺序

在优化摘要缓存之前先建立质量契约：

1. 定义哪些事实属于 pinned、verified、derived 和 stale。
2. 建立 long-horizon retention 与错误摘要注入测试。
3. 实现 Structured Working State 和 provenance schema。
4. 实现 Artifact Store 与有界 evidence retrieval。
5. 实现强制 Verification Policy 和冲突处理。
6. 再实现分层摘要、派生缓存、摘要 retry 与费用优化。
7. Provider 支持时增加 opaque/native compaction adapter，并与本地方案比较。

只有缓存命中率、token 降幅或摘要可读性不能证明 Memory 可信；必须同时报告关键事实遗漏率、虚构率、过期事实使用率、核验触发率和最终任务成功率。
