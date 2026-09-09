# Memory/Retention Contract

## 它解决什么问题

摘要中的一句话可能被模型用于不同目的：作为低风险背景说明，或者作为技术选型、代码修改和外部副作用的依据。只保存 provenance ID 不能保证模型主动回查，也不能阻止错误摘要驱动决策。

Memory/Retention Contract 先把两件事分开：

- **Retention**：这条 claim 应当多努力地留在模型可见 Context 中。
- **Trust**：Runtime 当前有多少理由相信这条 claim。

`pinned` 不等于 `verified`。例如“数据库支持 partial index”可能因为影响架构而被 pinned，但如果它来自 LLM 摘要，它仍然是 derived，不能直接作为技术选型依据。

## 数据模型

`MemoryClaim` 是一条显式、可追溯的事实声明：

```text
MemoryClaim
├── id
├── text
├── retention: pinned | working | episodic
├── trust: verified | derived | stale | contradicted
├── risk: low | medium | high
└── provenance[]
    ├── source_id / source_type
    ├── content_hash
    ├── workspace_version
    └── artifact_id
```

当前要求每一条 claim 至少有一个 `ProvenanceRef`。这只建立可追溯性，不证明 claim 正确。

## 使用场景

Policy 区分 claim 将被用于：

- `reference`：低风险背景引用；
- `planning`：制定执行计划；
- `technical_decision`：技术选型或架构判断；
- `code_change`：据此修改代码；
- `external_side_effect`：发送邮件、创建 Issue、部署等；
- `completion`：据此宣布任务完成。

只要 derived claim 将进入 planning、technical decision、code change、side effect 或 completion，Policy 就返回 `must_verify=true` 和 `block_use_until_verified=true`。这回答了“错误摘要参与技术选型怎么办”：不能等待模型自己怀疑，Runtime 应在派生事实进入可执行决定时阻断。

stale 或 contradicted claim 即使只作为 reference 也会被阻断。verified 的高风险 claim 在高影响边界仍要求重新核验，以防环境已经变化但 staleness 尚未被探测。

## 当前数据流

```text
MemoryClaim + ClaimUse
          ↓
MemoryRetentionPolicy.evaluate()
          ↓
MemoryVerificationDecision
├── must_verify
├── block_use_until_verified
├── reasons
└── provenance to read back
```

这是一个纯决策 Policy，已可确定性测试和序列化进 trace。当前尚未把 MemoryClaim 接入 AgentState/Context，也没有 Artifact Reader，因此它不会自动执行 read-back。后续 Verification Runtime 必须消费这个 Decision，按 provenance 取回证据，验证成功后生成新的 verified claim 或终止当前决定。

## “用完后移出活跃 Context”在哪里发生

当前没有 Evidence Retrieval 生命周期，所以还没有对应实现。现有 `AgentLoop` 每一步从完整 State 重建 Context，随后 Compaction 只替换该次请求使用的局部 `context` 变量；这只能让被压缩内容不进入本轮模型请求，不能表达“临时取回证据并在阶段结束后驱逐”。

计划中的实现需要给检索结果增加生命周期字段，例如：

```text
scope: current_decision
expires_after_step: 12
token_budget: 1500
```

`ContextAssembler` 只在生命周期有效时装入证据；过期后不再装入下一轮 ModelContext。Artifact 和原始 State 不删除。

## 取舍和边界

当前实现完成的是“什么时候必须核验”的合同，不是核验执行器：

- 已实现：类型、输入校验、技术决策等 actionable use 的阻断规则、trace-friendly 输出和单元测试。
- 未实现：claim 提取、AgentState 存储、版本失效检测、冲突发现、Artifact Store、证据取回、验证成功后的状态转换和 Loop 强制执行。
- 残余风险：错误 claim 如果从未被分类或没有进入受控使用边界，Policy 无法看到它；因此仍需要错误摘要注入和 long-horizon 任务测试。

## 面试短答

> Provenance 只告诉 Runtime 去哪里找证据，不能保证模型主动查询。我把记忆保留等级和事实可信状态拆成两个维度，并按 claim 的使用场景做确定性决策：derived claim 一旦进入计划、技术选型、代码修改、副作用或完成判断，就先阻断并要求 read-back。当前完成的是 Policy 合同；真正的 Artifact Retrieval 和 Loop enforcement 是下一层。
