# Session 学习笔记：Agent Runtime、Context 与可恢复长任务

这份笔记整理本轮连续讨论中的核心概念。它不是产品宣传，而是按三种状态区分：

- **已实现并有测试**：仓库里已有代码和自动化证据。
- **已设计但未接线**：接口或独立原型存在，主 `AgentLoop` 还不会使用。
- **计划中**：只有问题分析和建议路线，不能在面试中说成已完成。

当前完整离线测试基线是 `112 passed, 6 skipped`。6 个 skip 是默认关闭的真实 Provider smoke。

## 1. 先建立整体模型

LLM 本身只完成一次“输入 Context，生成响应”。Agent Runtime 才负责不断循环：

```text
用户任务
  -> 组装本轮 Context
  -> 调用模型
  -> 解析文字 / tool call / final answer
  -> 执行工具并记录 observation
  -> 更新任务状态
  -> 验证是否真的完成
  -> 未完成则进入下一步
```

因此，Agent 不是“会调用工具的模型”这么简单，而是模型、工具、状态、策略、验证和停止条件组成的系统。

### Task、Run、Session 和 Agent 实例

- **Task**：用户当前要求完成的工作，例如“修复登录失败”。它通常从接收要求开始，到完成、失败或取消结束；不是整个聊天永久生命周期。
- **Run**：Runtime 对一个 Task 的一次执行尝试。失败后重试或从旧状态分叉，可以形成新 Run。
- **Session**：承载一次执行的活动容器，通常拥有自己的模型 Context、取消状态、工具权限和临时资源。
- **Agent 实例**：某套模型、提示词、工具和 Runtime 配置在一个 Session 中的实际运行实例。同一配置可启动多个实例，但不意味着它们共享记忆。
- **Lineage**：一次任务从某个起点演化出来的逻辑历史分支，用来回答“当前状态属于哪条演进路线”。

## 2. State、Context、Memory、Checkpoint 不是一回事

| 概念 | 保存什么 | 主要用途 | 是否应成为事实源 |
|---|---|---|---|
| State | 当前 Run 的消息、工具结果、状态转换等完整事实 | 运行 Agent | 是当前执行事实之一 |
| Context | 从 State 和外部证据中挑选出的本轮模型输入 | 让模型决策 | 否，只是临时视图 |
| Memory | 跨任务仍值得保留的偏好、约束或知识 | 长期复用 | 需要来源、可信度和更新规则 |
| Compaction Summary | 对旧 Context 的压缩表示 | 控制 token | 否，是派生信息 |
| Checkpoint | 某条 Lineage 在某个 event sequence 的结构化 Task State 快照 | 快速恢复 | 是可校验快照，但仍需与环境对账 |
| Event Log | 任务状态已经发生变化的顺序事实 | 重放与审计 | 是恢复的权威历史 |
| Trace | 模型延迟、token、重试、Context 等调试信息 | 观测和诊断 | 通常不是恢复合同 |

文件被模型读取后，相关内容确实进入了当轮 Context；关键在于它不必永久留在以后每一轮。Runtime 可以在需要时读取相关片段，使用后让它退出活动窗口，原文件仍是可重新读取的外部事实源。

## 3. Agent Loop 当前如何被测试

没有真实 API 时仍能测试 Loop 的确定性控制逻辑，因为测试给模型接口注入预先编排的响应：第一轮要求工具、工具执行后第二轮给出最终答案。这样能验证状态转换、工具往返、最大步数、取消和 verifier 反馈。

但 Fake 不能证明真实供应商协议可用，所以项目采用分层证据：

1. **Fake Model 单元测试**：稳定覆盖 Loop 分支和停止条件。
2. **Mock HTTP/SSE**：模拟网络字节分片、连接中断、429、500、timeout 和协议映射。
3. **Mock 端到端 Loop**：经过真实 SDK/HTTP 适配层完成工具往返。
4. **DeepSeek smoke**：确认真实服务能接受序列化协议并完成工具闭环。
5. **Benchmark（尚未完成）**：比较更长任务上的成功率、成本、延迟和记忆保持率。

真实 API 成功只证明“集成能跑”，不能证明 Agent 在复杂任务中稳定有效；所以用户提出“先保证框架能用，再写 benchmark”是合理的阶段顺序，但框架可用仍要靠复杂失败注入而非单次 happy path。

### 流式响应是什么

服务不会等整份回答生成完再返回，而会通过 SSE 连续发送小事件。Provider 必须累计文本、reasoning 和 tool-call 参数，直到收到合法终止事件才提交一份完整模型响应。

- 分片只是传输边界，不等于语义边界。
- 终止事件之前断线，不能把半个 tool call 当成成功。
- 429/500/timeout 只能在“该次响应尚未提交”时按策略重试。
- 取消要向下关闭网络流；否则 UI 显示取消，后台仍可能运行和计费。
- 当前未实现 SSE 断点续传；中途断线会丢弃未提交的部分响应，然后按安全重试规则重新请求。

### 为什么多个 Tool Call 不能随意交错

一次模型响应可能同时产生 `call1` 和 `call2`。某些 Responses-compatible Provider 要求先保留完整模型响应批次，再提供对应输出：

```text
call1, call2, output1, output2
```

如果 Runtime 改写成 `call1, output1, call2, output2`，就篡改了原响应结构，DeepSeek 的真实实验曾因此返回 HTTP 400。Fake 没暴露这个约束，说明 Fake、Mock、真实 Provider 三层不能互相替代。

## 4. Context Budget 与 Tool Output Truncation

**Context Budget** 是发送模型前的输入配额检查。当前实现用近似 token 数估算 System、Task、消息和工具结果是否超过限制。

**Tool Output Truncation** 是在模型视图里把超长工具结果缩成头部、标记和尾部，防止一份日志挤掉全部任务信息。它不是完整 Context Compaction，也不一定每轮触发，只有工具输出超过阈值才触发。完整结果仍保留在 State/Trace。

头尾截断可能丢失中间的关键报错。更完整的路线是：

- 工具优先返回结构化关键字段，而不是先制造巨量文本。
- 完整输出存入 Artifact Store，Context 只放句柄、统计和小预览。
- 按错误位置、行号或查询条件有界取回局部证据。
- LLM 可以辅助语义提取，但不能销毁原始结果或成为唯一事实源。

## 5. Context Compaction 的选项与限制

| 方案 | 做法 | 优点 | 主要风险 |
|---|---|---|---|
| Sliding Window | 保留 System/Task 和最近若干 ContextUnit | 简单、便宜、确定性 | 遗忘早期约束 |
| Full Summary | 用模型把较早历史压成一段摘要 | 语义覆盖比纯窗口好 | 遗漏、幻觉、反复摘要漂移 |
| Rolling Summary | 旧摘要与新历史再次合并 | 每轮成本较稳定 | 错误逐代累积且难追根 |
| Structured Checkpoint | 按 goal、plan、完成项、证据等字段保存任务状态 | 可校验、适合恢复 | schema 不可能预见所有语义 |
| Layered Summary | 保存阶段摘要，再逐层合并或按需检索 | 支持更长跨度和局部读取 | 层级、检索和一致性更复杂 |
| Provider-native Compaction | 使用供应商原生压缩能力 | 接入方便，可能利用专有优化 | 可解释性、可迁移性和可测性较弱 |

Layered Summary 是“如何组织多份摘要”；Structured Checkpoint 是“用哪些明确字段表示可恢复任务状态”。二者可以组合，但不能互相替代。

### 当前仓库实际路线

- 已实现：Sliding Window、Full Summary、tool call/result 原子 ContextUnit、摘要失败回退。
- 已实现但未接主 Loop：结构化 Durable Task State、Event Log、Checkpoint 和恢复协调层。
- 尚未实现：Layered Summary、Artifact Store、有界 Evidence Retrieval、摘要准确率 benchmark、Provider-native 对比。

没有完美 Compaction。摘要准确性不能靠一句“让模型认真总结”保证。推荐的超长任务路线不是无限优化一份摘要，而是：

```text
短活动窗口
+ 结构化任务状态
+ 权威 Event Log / Checkpoint
+ workspace 与测试等外部事实
+ 按决策点有界取证
+ Verifier
+ 必要时开启新的短 Session
```

里程碑生成新 checkpoint/阶段摘要通常比固定轮数更有语义，但不能完全代替容量阈值：一个里程碑内部也可能超长。因此应使用“里程碑优先 + token 硬阈值兜底”，并对摘要进行抽样或风险驱动核验，而不是机械地“压缩两次就全量检查”。

## 6. 摘要失真、幻觉和注意力偏移如何处理

摘要错误而模型与用户都没发现时，provenance 本身不会自动纠错。它只回答“可以去哪里核验”。真正降低风险需要 Runtime 主动执行以下机制：

1. 摘要始终标记为 derived，不升级成 verified fact。
2. 技术选型、代码修改、外部副作用和宣布完成等决策点，根据风险强制 read-back。
3. 只取回与当前 claim 相关的有限片段，避免把整段历史重新塞回 Context。
4. 原文件、Event Log、测试结果和外部 API 回读作为权威证据。
5. 冲突、过期和高风险 claim 阻断执行并要求重新计划。
6. 用错误摘要注入和 long-horizon retention benchmark 量化漏检率。

“读回 500～2,000 token 后 Context 又变大”是事实，但它只是决策点的临时、有界增长；下一轮可从活动 Context 移除，外部证据没有被删除。当前仓库还没有实现这条 Artifact Retrieval/临时证据淘汰链路，只完成了 Memory/Retention 决策合同。

## 7. MemoryClaim 为什么出现在 Compaction 讨论里

`MemoryClaim` 把一条可能进入 Context 或长期记忆的信息表示为：内容、来源、可信状态、保留等级、风险和有效期。它的目的不是自动把所有候选写入长期 Memory，而是防止“摘要里的一句话”被 Runtime 无条件当作真相使用。

- `pinned` 只表示保留优先级高，不表示正确。
- `expires_after_step` 让只对某阶段有效的临时事实不会永久污染后续决策。
- 项目级偏好（例如“解释以面试学习为目的”）适合项目 Memory；一次任务的当前步骤属于 Task State；一次命令结果属于 evidence/receipt。
- 字段不能无限细化。稳定、可枚举、影响执行安全的内容适合结构化；开放语义仍由文本、Artifact 和检索承载。

它与 Compaction 的联系是“摘要产出的 claim 怎样被安全消费”，而不是 MemoryClaim 本身能解决摘要准确性。

## 8. Verifier、Observation、Receipt 与 Provenance

- **Observation**：Agent 从工具看到的结果，例如命令 stdout 或 API 响应。
- **Receipt**：工具或 verifier 产生的持久化“机器收据”，记录动作、目标、状态、时间、幂等键、外部资源 ID 和证据位置。
- **Provenance**：某条 claim 来源于哪个事件、文件片段、receipt 或模型输出的指针。
- **Verifier**：根据最终业务后置条件独立判断任务是否成功。

可以把 Receipt 理解成“一种结构化、可追溯的 Observation”，但两者不完全相同：Observation 面向模型当下决策，Receipt 面向恢复、审计、幂等和完成验收；Receipt 中包含 provenance，但 provenance 也可指向原文件或 Domain Event。

通用 Agent 不可能预先写死所有成功条件。可行的分层是：Runtime 提供 verifier 协议与常见 catalog；Planner 为任务生成可审查的 acceptance criteria；领域工具提供 API 回读；不可机器观察的目标明确降级为人工确认。比如“发送邮件”只能验证服务接受、Sent 中存在且字段正确，不能声称收件人已阅读。

当前仓库已有 File、Command、Composite verifier；Receipt 仍只是 plan 节点里的字符串 ID，尚无 typed Receipt/Store。

## 9. Domain Event、Reducer、Checkpoint 与恢复

### Domain Event 与 Trace

Domain Event 记录会改变任务状态、恢复时不能丢的事实，如 `PlanAccepted`、`TaskNodeCompleted`。Telemetry/Trace 记录延迟、token、SSE、retry 等观测信息。前者回答“任务状态发生了什么”，后者回答“系统当时怎么运行”。

### Reducer 做什么

Reducer 是纯函数：

```text
旧 DurableTaskState + 下一条 DomainEvent -> 新 DurableTaskState
```

reduce 前是一串离散事件；reduce 后是可以直接查询的当前计划、已完成节点、blocker 和 next action。纯函数与连续 `sequence` 使同一事件流能确定性重放，也使非法状态转换在落盘前被拒绝。

### Checkpoint 里有什么

Checkpoint 不只是 metadata，也包含完整结构化 `task_state`，其中有 goal、plan/依赖、完成步骤、当前步骤、blocker、next action 和 receipt ID。Metadata 的 `run_id`、`sequence`、schema version、workspace revision、checksum 等描述这份快照，而不是描述原始对话。

恢复时选择请求指定 Run/Lineage 下最新且兼容的 committed checkpoint，校验 checksum/schema/workspace，再从 `event_offset + 1` 重放事件。最后还要回读 Git、文件、测试和外部系统，因为 checkpoint 正确不代表外部世界没变化。

### sequence、checksum、fsync 与 WAL

- `sequence` 是一条 Lineage 内严格递增的位置号，用来发现事件丢失、乱序或重复。
- checksum 对 checkpoint 的规范化内容求摘要；加载时重算，不一致说明文件被改动或损坏。它检测完整性，不证明业务内容正确。
- `fsync` 请求操作系统把单个文件内容刷向稳定存储，降低断电前只停留在缓存中的风险。
- 仅有 `fsync` 仍不能保证“外部副作用与事件记录同时成功”，也不解决多进程并发、目录项持久化和日志尾部修复。
- 数据库 WAL（Write-Ahead Log）先记录即将提交的变更，再发布事务状态，可提供更系统的崩溃恢复；当前 JSONL Store 只是单 writer 教学基线。

已实现并测试：Domain Event、纯 Reducer、JSONL Store、Checkpoint、checksum、snapshot + delta replay、`DurableTaskSession` 的 persist-before-publish 和崩溃恢复。尚未实现：主 Loop 接线、schema migration、workspace reconcile、typed Receipt、Resume CLI 和 Lineage。

## 10. 多 Session 为什么要隔离，上下文之间如何联系

多个独立 Session **默认必须隔离活动 Context**。即使它们由完全相同的 Agent 配置启动，也不应自动看见彼此的聊天、tool output 或未确认推理。否则会出现任务污染、权限串用、token 无界增长和难以复现的问题。

Session 之间的联系不应靠“共享一个巨大聊天窗口”，而应靠显式接口：

```text
Coordinator / Task Graph
       |
       +-- Session A（隔离 Context + 隔离 workspace）
       |       -> diff / artifact / receipt / status
       |
       +-- Session B（隔离 Context + 隔离 workspace）
               -> diff / artifact / receipt / status
       |
       -> 校验、选择、合并并写入权威任务历史
```

可能共享的是：同一个父任务 ID、只读起点 checkpoint、任务图中的依赖关系、受命名空间保护的 Artifact/Receipt Store，以及经协调器批准的结果。可能隔离的是：活动模型 Context、取消信号、临时文件、写权限、未提交变更和私有 trace。

两种常见关系：

- **竞争路线**：A/B 从同一起点尝试不同方案，各自形成不同 Lineage，最后只选一条合并。
- **流水线协作**：A 调研、B 实现、C 验证；B 只消费 A 的结构化 handoff 和证据，不继承 A 的全部对话。

## 11. 多 Agent 如何协作在同一条 Lineage

“同一 Lineage”表示它们的被接受结果共同形成一条权威任务演进历史，**不表示多个模型并发修改同一个 Context 或直接抢写同一个 Event Log**。

一个安全的最小流程是：

1. Coordinator 持有 Lineage、TaskGraph 和唯一 event sequence 分配权。
2. 它把互不冲突的节点连同有界 Context 分给 Worker A/B。
3. Worker 在独立 worktree/沙箱执行，返回候选 diff、artifact、receipt 和验证结果。
4. Coordinator 检查依赖、版本和冲突，按顺序合并一个候选结果。
5. 只有 Coordinator 把已接受结果追加为该 Lineage 的 Domain Event。
6. 合并后统一运行 verifier，再更新 checkpoint 和后续任务节点。

例如 A 修改后端、B 编写独立测试，只要二者都服务于同一已接受计划，经过协调器串行合并后可以属于同一 Lineage。若 A/B 是互斥架构方案，则应从共同 checkpoint 分叉成两个 Lineage，不能假装是一条无冲突历史。

隔离 workspace 可使用 Git worktree、容器/VM 或 copy-on-write 文件系统；再配合文件所有权、乐观版本检查和合并验证。当前项目没有实现 Multi-Agent 或 Lineage，上述内容是推荐设计，不是现有能力。

## 12. 本轮最容易混淆的认知点

1. Context 不是 State；缩短 Context 不等于删除历史。
2. Summary 不是事实；provenance 也不是正确性证明。
3. Checkpoint 不是对话摘要，而是结构化任务状态快照。
4. Event Log 用来恢复；Trace 主要用来观测。
5. Receipt 不等于所有 Observation，但它可以保存可审计 Observation 及来源。
6. Verifier 没有万能实现；成功标准必须来自任务契约和可观察后置条件。
7. Fake 测试证明控制逻辑，不证明真实协议；真实 smoke 也不证明复杂任务质量。
8. 多 Agent 不必共享 Context；共享的是经过定义和验证的工作产品。
9. Lineage 表达历史归属，不是通信通道，也不是 Git branch 的同义词。
10. fsync 降低单文件丢失概率，不提供跨系统事务。

## 13. 面试问题与回答方向

### Agent Loop 与状态

**Q：LLM 和 Agent 的区别是什么？**

A：LLM 是一次生成调用；Agent Runtime 把模型放进带工具、状态、策略、验证和停止条件的循环。

**Q：一轮对话等于一次 Agent Run 吗？**

A：不一定。一次 Task/Run 可以包含多次模型轮次和多次工具调用；下一条用户要求通常启动新的 Task 或继续已有任务，具体由产品会话协议决定。

**Q：为什么分开 State 和 Context？**

A：State 保存事实，Context 是按预算派生的模型视图。这样压缩不会销毁审计与重放依据。

**Q：什么是状态机或状态转换？**

A：状态机定义允许的阶段和转换，例如 running -> waiting_tool -> running -> completed；非法转换被 Runtime 拒绝，避免只靠散乱布尔值维护生命周期。

### Provider 与测试

**Q：没接真实 API 怎么测试 Agent Loop？**

A：通过可注入 Fake 响应确定性测试控制流；再用 Mock HTTP 测传输协议、真实 smoke 测供应商集成，最后用 benchmark 测任务质量。

**Q：SSE 中途断线后怎么处理？**

A：未收到完整终止事件就不提交半成品；释放连接，并只在没有已提交副作用时按 Runtime 策略重试。当前项目没有断点续传。

**Q：为什么 SDK retry 和 Runtime retry 要分开？**

A：Runtime 知道响应是否提交、工具是否执行和预算；双层重试会放大次数并可能重复副作用，所以当前关闭 SDK 内部重试，由 Runtime 统一控制模型请求。

**Q：为什么一次模型响应里的多个 Tool Call 要保持批次？**

A：它们属于同一个协议响应项；交错插入 output 会改变供应商要求的输入顺序，真实 DeepSeek 曾返回 400。

### Context 与 Memory

**Q：Sliding Window、Full Summary、Structured Checkpoint 怎么选？**

A：短任务先用窗口；需要语义保留时可加摘要；需要长任务恢复时必须引入结构化任务状态和权威事件。它们解决的问题不同，通常组合使用。

**Q：摘要准确性如何保证？**

A：不能绝对保证。把摘要标记为派生信息，保留原始证据，在高风险决策点强制回读，并用错误注入与 retention benchmark 测漏失和漂移。

**Q：定期重做摘要还是里程碑摘要？**

A：里程碑更符合任务语义，但不能覆盖里程碑内部爆长，因此采用里程碑优先、token 阈值兜底，并避免摘要反复摘要摘要。

**Q：为什么取回证据不会重新导致 Context 无限长？**

A：只取与当前 claim 相关的有限片段，并设 token/item/call/TTL 边界；用完从后续活动视图移除，事实仍留在外部存储。

**Q：MemoryClaim 的作用是什么？**

A：表示一条信息的来源、可信度、保留级别、风险与有效期，让 Runtime 决定能否直接用于执行；它不是自动长期记忆写入器。

### 验证与证据

**Q：模型说完成为什么还要 Verifier？**

A：FinalAnswer 是主观声明，Verifier 检查文件、测试或外部 API 的可观察后置条件，通过后才改变任务完成状态。

**Q：通用 Agent 如何验证任意工具任务？**

A：没有万能 verifier。使用统一协议、领域 catalog、任务级 acceptance criteria、API 回读和必要的人工确认组合，并诚实限制不可观察目标。

**Q：Observation、Receipt、Provenance 有何区别？**

A：Observation 是当下看到的结果；Receipt 是为审计、恢复和幂等持久化的动作证据；Provenance 是 claim 指向这些原始证据的位置关系。

### Durability 与恢复

**Q：Domain Event 和 Trace 的区别？**

A：Domain Event 是恢复任务状态所需的版本化事实；Trace 是面向诊断的高维运行细节，保留策略和稳定性合同不同。

**Q：Reducer 前后发生了什么？**

A：前面是有序事件，后面是可查询的当前 Task State；纯 Reducer 保证相同输入得到相同状态。

**Q：Checkpoint 为什么要包含 Plan？**

A：恢复不只要知道做过什么，也要知道未完成节点、依赖和下一动作；否则快照无法继续任务。

**Q：如何选择“最近且兼容”的 Checkpoint？**

A：先限定 project/run/task/lineage，再筛 committed，按 sequence 倒序；随后校验 checksum、schema、workspace revision，不兼容就回退旧快照或完整 replay/reconcile。

**Q：checksum 能防止什么？**

A：能发现快照内容意外变化或损坏，不能防恶意重算，也不能证明业务事实正确；强安全还需要签名/权限控制。

**Q：fsync 后为什么仍可能不一致？**

A：它只增强单个文件落盘，不会把“发邮件”和“写事件”变成一个原子事务，也不自动解决并发 writer、目录同步和尾部损坏。

**Q：Lineage 什么时候发挥作用？**

A：在恢复、重试、回滚后重规划、方案分叉和合并时标识状态属于哪条演进路线，避免把互斥分支的事件混在一起。

### Multi-Agent

**Q：相同 Agent 配置启动多个 Session，需要上下文隔离吗？**

A：需要，默认隔离活动 Context、权限和未提交 workspace；通过 TaskGraph、Artifact、Receipt 和结构化 handoff 显式联系。

**Q：多个 Agent 如何协作在同一 Lineage？**

A：Worker 各自在隔离环境执行任务节点，Coordinator 校验并串行提交被接受结果，统一分配 event sequence；共享逻辑历史，不共享可变 Context。

**Q：多路线并行一定是 Multi-Agent 吗？**

A：不一定。一个 Agent 可以顺序探索多条 Lineage；多个 Agent 也可并行探索。Lineage 是状态历史概念，Multi-Agent 是执行主体数量概念。

**Q：如何隔离多个 Worker？**

A：常见方法是 Git worktree、容器/VM、copy-on-write workspace，加上文件所有权、版本检查、权限边界和合并后 verifier。

## 14. 下一步开发优先级

1. 定义最小 `Plan`、typed `Receipt` 和 `ResumeRequest` 合同，避免用字符串 ID 或伪一节点计划冒充恢复。
2. 将 `DurableTaskSession` 接入 `AgentLoop` 的真实生命周期，产生可恢复 Domain Event。
3. 加入崩溃点、重复提交、工具副作用和 workspace 漂移的失败注入测试。
4. 实现 Resume CLI、兼容性检查、workspace reconcile 和 schema migration 边界。
5. 再实现 Artifact Store、有界 Evidence Retrieval 与摘要失真 benchmark。
6. 单 Agent 恢复语义稳定后，才实现 Lineage 分叉与最小 Multi-Agent Coordinator。

面试时最重要的表达不是列出类名，而是说明：每个机制防止什么失败、事实源在哪里、怎样测试、还有哪些边界没有解决。
