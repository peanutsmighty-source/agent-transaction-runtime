# Agent Runtime Lab — PRD / MVP / Execution Plan

> 面向 Coding Agent 的工程执行文档
> 目标：通过实现一个最小但完整的 Agent Runtime，系统理解 Agent Loop、State、Context、Tool Runtime、Compaction 与 Multi-Agent，并形成可持续迭代、可评测、可公开展示的 GitHub 项目。

---

## 1. 项目概述

### 1.1 项目名称

**Agent Runtime Lab**

建议仓库名：

```text
agent-runtime-lab
```

### 1.2 项目定位

Agent Runtime Lab 不是一个“聊天机器人框架”，也不是 LangChain / CrewAI 的替代品。

它是一个用于研究和实验 Agent 核心机制的最小运行时，重点关注：

- Agent Loop 如何驱动模型、工具与状态演进
- Agent State 与 Model Context 的区别和转换关系
- Context 如何构建、观测、裁剪与压缩
- Tool Call 如何执行、记录、截断与恢复
- Agent 如何判断继续、停止、失败与重试
- Multi-Agent 如何 spawn、调度、通信、收敛
- 不同 Runtime Policy 对 token、成功率和长期任务保持能力的影响

项目必须满足两个目标：

1. **学习价值**：所有核心机制都尽量显式实现，避免被大型框架隐藏。
2. **工程价值**：具备测试、评测、日志和清晰模块边界，可以作为 GitHub 项目长期演进。

---

# 2. 核心目标

## 2.1 当前第一阶段目标：可观测的单 Agent Coding Loop

完成一个可以执行真实 Coding Task 的最小 Agent Runtime。首个可展示成果不是
Multi-Agent，而是一个能进入小型 Python 仓库、修复简单 bug，并清楚展示每一步
状态、context、工具调用与停止原因的单 Agent：

```text
User Task
   ↓
Agent Loop
   ↓
Context Builder
   ↓
LLM
   ↓
Action
   ├── Tool Call
   │      ↓
   │   Tool Runtime
   │      ↓
   │   Observation
   │      ↓
   │   State Update
   │      ↓
   │   Next Turn
   │
   └── Final Answer
          ↓
         Stop
```

本阶段必须支持：

- 模型调用
- tool calling
- shell tool
- filesystem tool
- state 管理
- context 构建
- token/context usage 基础观测
- max step
- stop condition
- error handling
- execution trace

---

## 2.2 后续阶段：Context Engineering / Compaction

加入 **Context Engineering / Compaction**：

```text
Agent State
     ↓
Context Builder
     ↓
Context Budget
     ↓
Should Compact?
   /           \
 no            yes
 |              |
 ↓              ↓
LLM         Compaction Policy
 |              |
 └──────←───────┘
```

至少实现三种策略：

1. Full Summary
2. Sliding Window + Summary
3. Structured Compaction

并建立 benchmark 比较：

- token reduction
- task success
- constraint retention
- decision retention
- tool-result retention
- latency
- model cost

---

## 2.3 后续阶段：最小 Multi-Agent Runtime

加入最小 Multi-Agent Runtime：

```text
Parent Agent
     ↓
Scheduler
     ↓
Task Decomposition
     ↓
 ┌───────┬───────┐
 ↓       ↓       ↓
A1      A2      A3
 │       │       │
 └───────┴───────┘
         ↓
     Aggregator
         ↓
     Parent Agent
```

研究：

- spawn policy
- max agent count
- max depth
- token budget
- wait / cancel
- result merge
- agent isolation
- shared state
- task dependency

---

# 3. 非目标

MVP 阶段明确不做：

- Web UI
- IDE 插件
- VS Code Extension
- MCP Server
- RAG
- Vector Database
- 长期用户记忆
- GUI Agent
- 浏览器 Agent
- 云端部署
- 大规模分布式 Agent
- Prompt Marketplace
- CrewAI 风格 Role 系统
- 复杂工作流 DSL
- Kubernetes
- 企业权限系统

原则：

> 任何不能帮助理解 Agent Runtime 核心机制的功能，MVP 阶段都不做。

---

# 4. 技术原则

## 4.1 不使用 Agent Framework

禁止将以下框架作为核心依赖：

- LangChain
- LangGraph
- CrewAI
- AutoGen
- OpenAI Agents SDK
- Semantic Kernel

可以阅读其设计，但运行时必须自行实现。

允许使用：

- 官方模型 SDK
- HTTP client
- tokenizer
- pydantic / dataclass
- pytest
- rich
- asyncio

---

## 4.2 Runtime 必须显式

以下行为必须在代码中可见：

```text
while agent_running:
    context = context_builder.build(state)

    if compact_policy.should_compact(context):
        state = compact_policy.compact(state)

    response = model.generate(context)

    action = action_parser.parse(response)

    observation = executor.execute(action)

    state = reducer.reduce(state, action, observation)

    if stop_policy.should_stop(state):
        break
```

不得把整个 Agent Loop 隐藏在第三方库调用中。

---

## 4.3 Policy 与 Runtime 分离

运行时负责：

```text
execution mechanism
```

Policy 负责：

```text
execution decision
```

例如：

```python
class CompactPolicy:
    def should_compact(self, state, context_stats) -> bool:
        ...

    async def compact(self, state):
        ...
```

未来所有实验功能优先实现为 policy。

---

# 5. 建议技术栈

MVP 优先：

```text
Python 3.12+
asyncio
pydantic
pytest
rich
tiktoken / tokenizer abstraction
```

模型层必须抽象：

```python
class ModelClient(Protocol):
    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> ModelResponse:
        ...
```

第一版可以只实现一个 provider。

后续再扩展：

```text
OpenAI
Anthropic
DeepSeek
OpenRouter
```

不要第一天实现多 provider。

---

# 6. 总体架构

```text
┌─────────────────────────────────────────────────────┐
│                    Agent Runtime                    │
│                                                     │
│  ┌──────────────┐                                   │
│  │  AgentLoop   │                                   │
│  └──────┬───────┘                                   │
│         │                                           │
│         ▼                                           │
│  ┌──────────────┐       ┌────────────────────┐      │
│  │ Agent State  │──────▶│ Context Builder    │      │
│  └──────────────┘       └─────────┬──────────┘      │
│                                   │                 │
│                                   ▼                 │
│                          ┌────────────────────┐      │
│                          │   Model Client     │      │
│                          └─────────┬──────────┘      │
│                                   │                 │
│                                   ▼                 │
│                          ┌────────────────────┐      │
│                          │ Action Dispatcher  │      │
│                          └─────────┬──────────┘      │
│                                   │                 │
│                    ┌──────────────┴─────────────┐   │
│                    ▼                            ▼   │
│             ┌────────────┐               ┌────────┐ │
│             │ ToolRuntime│               │ Final  │ │
│             └─────┬──────┘               └────────┘ │
│                   │                                 │
│                   ▼                                 │
│            ┌──────────────┐                         │
│            │ Observation  │                         │
│            └──────┬───────┘                         │
│                   │                                 │
│                   ▼                                 │
│            ┌──────────────┐                         │
│            │   Reducer    │                         │
│            └──────┬───────┘                         │
│                   │                                 │
│                   └───────────────▶ Agent State     │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

# 7. 核心数据结构

## 7.1 AgentState

建议：

```python
class AgentState(BaseModel):
    task: str

    step: int = 0
    status: AgentStatus

    messages: list[Message]
    events: list[AgentEvent]

    working_memory: WorkingMemory
    tool_history: list[ToolExecution]

    token_usage: TokenUsage
    context_stats: ContextStats

    final_answer: str | None = None
    error: str | None = None
```

---

## 7.2 AgentStatus

```python
class AgentStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

---

## 7.3 AgentEvent

所有重要行为统一记录为 Event：

```text
UserInput
ModelRequest
ModelResponse
ToolCall
ToolResult
StateUpdate
CompactStarted
CompactFinished
AgentSpawned
AgentStopped
RuntimeError
```

建议：

```python
class AgentEvent(BaseModel):
    type: str
    timestamp: datetime
    step: int
    data: dict
```

Event 是后期：

- trace
- replay
- debug
- evaluation
- visualization

的基础。

---

# 8. Agent Loop

## 8.1 MVP Loop

实现：

```python
async def run(task: str) -> AgentResult:
    state = create_initial_state(task)

    while state.status == RUNNING:

        if state.step >= config.max_steps:
            state.status = FAILED
            state.error = "max_steps_exceeded"
            break

        context = context_builder.build(state)

        context_observer.inspect(state, context)

        if compact_policy.should_compact(state, context):
            state = await compact_policy.compact(state)
            context = context_builder.build(state)

        response = await model.generate(
            messages=context.messages,
            tools=tool_registry.schemas(),
        )

        action = action_parser.parse(response)

        if action.type == "final":
            state.final_answer = action.content
            state.status = COMPLETED
            break

        if action.type == "tool":
            observation = await tool_runtime.execute(action)
            state = reducer.reduce(
                state,
                action,
                observation,
            )

        state.step += 1

    return AgentResult.from_state(state)
```

---

## 8.2 Loop 必须处理的异常

必须覆盖：

- 模型 API error
- timeout
- malformed tool call
- unknown tool
- tool execution error
- tool timeout
- shell non-zero exit
- context overflow
- max steps
- repeated identical action
- empty model response

---

# 9. Tool Runtime

第一版只实现两个 Tool。

## 9.1 ShellTool

接口：

```text
shell(command)
```

返回：

```json
{
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "duration_ms": 123
}
```

必须支持：

- timeout
- cwd
- stdout length limit
- stderr length limit

禁止第一版提供无限输出。

---

## 9.2 FileTool

支持：

```text
read_file(path)
write_file(path, content)
list_dir(path)
```

第二版再加入：

```text
replace
grep
patch
```

---

## 9.3 Tool Registry

```python
class ToolRegistry:

    def register(self, tool):
        ...

    def get(self, name):
        ...

    def schemas(self):
        ...
```

---

# 10. Context Builder

这是整个项目最重要的模块之一。

## 10.1 Context ≠ State

明确区分：

```text
Agent State
    ↓
Context Builder
    ↓
Model Context
```

State 是 Runtime 内部事实。

Context 是某一轮发送给模型的输入。

---

## 10.2 Context Builder 输入

```text
system instructions
task
working memory
conversation
tool calls
tool results
compact summary
recent turns
```

---

## 10.3 ContextItem

所有 context 内容统一表示：

```python
class ContextItem(BaseModel):
    id: str
    type: ContextItemType
    content: str
    token_count: int
    created_at: datetime
    priority: int
```

type：

```text
SYSTEM
TASK
USER
ASSISTANT
TOOL_CALL
TOOL_RESULT
MEMORY
SUMMARY
STATE
```

---

# 11. Context Observer

MVP 必须实现。

CLI 输出示例：

```text
Agent Runtime — Step 18

Context Window
--------------------------------
System              3,202
Task                  821
Conversation        8,821
Tool Calls          4,192
Tool Results       22,181
Memory              2,091
Summary             1,287
--------------------------------
Total              42,595
Window            128,000
Usage               33.3%

Largest Context Items

1. shell_result_017    8,912 tokens
2. shell_result_012    6,281 tokens
3. assistant_015       2,174 tokens
```

必须支持：

```text
/context
```

或者 equivalent CLI debug output。

---

# 12. Context Budget

实现：

```python
class ContextBudget:
    max_tokens: int
    reserved_output_tokens: int
    compact_threshold: float
```

例如：

```text
max_tokens = 128000
reserved_output_tokens = 16000
compact_threshold = 0.75
```

有效输入 budget：

```text
112000
```

当：

```text
current_context > effective_budget * 0.75
```

触发 compact。

---

# 13. Compaction

这是 MVP v0.2 的核心实验模块。

定义：

```python
class CompactionPolicy(Protocol):

    def should_compact(
        self,
        state: AgentState,
        context: ModelContext,
    ) -> bool:
        ...

    async def compact(
        self,
        state: AgentState,
    ) -> AgentState:
        ...
```

---

# 14. Compaction Policy A — Full Summary

逻辑：

```text
old conversation
       ↓
summary prompt
       ↓
LLM summary
       ↓
replace history
```

保留：

```text
system
task
summary
last N turns
```

---

# 15. Compaction Policy B — Sliding Window

逻辑：

```text
history

old 70%
   ↓
summary

recent 30%
   ↓
raw
```

组合：

```text
summary(old history)
+
recent raw turns
```

---

# 16. Compaction Policy C — Structured Compaction

目标不是“总结聊天”。

而是提取 Agent State。

输出建议：

```json
{
  "task": "...",

  "constraints": [
    "..."
  ],

  "decisions": [
    "..."
  ],

  "modified_files": [
    {
      "path": "...",
      "changes": "..."
    }
  ],

  "tool_findings": [
    "..."
  ],

  "failed_attempts": [
    "..."
  ],

  "open_questions": [
    "..."
  ],

  "next_steps": [
    "..."
  ]
}
```

这部分以后可以独立成为：

```text
StructuredAgentMemory
```

---

# 17. Context Truncation

Compaction 之前应优先支持低成本 truncation。

例如：

```text
shell output 30k tokens
```

可以：

```text
head 200 lines
...
[TRUNCATED 19,821 TOKENS]
...
tail 200 lines
```

策略：

```python
class ToolOutputPolicy:
    max_tokens_per_result
    head_tokens
    tail_tokens
```

必须记录：

```text
original_tokens
retained_tokens
truncated_tokens
```

---

# 18. Stop Policy

独立实现：

```python
class StopPolicy:
    def should_stop(self, state, action) -> StopDecision:
        ...
```

MVP：

```text
final answer
max steps
fatal error
cancel
```

v0.2：

```text
repeated tool call
loop detection
no-progress detection
```

---

# 19. Loop Detection

定义 fingerprint：

```text
tool_name
+
normalized_arguments
+
recent state
```

若连续 N 次重复：

```text
shell("pytest")
shell("pytest")
shell("pytest")
shell("pytest")
```

则输出：

```text
possible_loop_detected
```

MVP 只 warning。

后期可以触发：

```text
reflection
retry strategy
stop
```

---

# 20. Logging / Trace

每次 Agent Run 生成：

```text
.runs/
└── 2026-08-27-001/
    ├── metadata.json
    ├── events.jsonl
    ├── context.jsonl
    ├── tools.jsonl
    └── result.json
```

events.jsonl 示例：

```json
{"step": 1, "type": "model_request", "...": "..."}
{"step": 1, "type": "tool_call", "...": "..."}
{"step": 1, "type": "tool_result", "...": "..."}
```

---

# 21. Replay

v0.2 支持：

```bash
agent-runtime replay .runs/2026-08-27-001
```

用途：

- debug
- offline analysis
- context visualization
- benchmark

Replay 第一版不需要重新执行模型。

只重放 event。

---

# 22. Evaluation Framework

项目不能只看：

```text
"Agent跑通了"
```

必须建立评测。

---

## 22.1 Eval 类型

### Task Success

```text
task completed?
tests pass?
output correct?
```

### Context Efficiency

```text
input tokens
output tokens
compact count
tool output tokens
retained tokens
```

### Long-Horizon Retention

检查 Agent 是否记得：

```text
constraints
decisions
file changes
previous failures
task objective
```

### Runtime Stability

```text
tool errors
model retries
loop count
max-step failure
```

---

# 23. Compaction Benchmark

创建：

```text
evals/compaction/
```

至少 10 个测试。

例：

## Case 1

步骤 3：

```text
Constraint:
Do not modify config.yaml
```

步骤 25 触发 compact。

步骤 40 给 Agent 一个机会修改 config.yaml。

检查：

```text
constraint_retained = true / false
```

---

## Case 2

步骤 5：

```text
Decision:
Use SQLite instead of PostgreSQL
```

compact 后询问架构。

检查：

```text
decision_retained
```

---

## Case 3

步骤 8：

```text
pytest failed because port 8080 occupied
```

compact 后再次运行。

检查是否重复相同错误。

---

# 24. Benchmark 输出

生成：

```text
results/
└── compact-benchmark.json
```

最终 README 里展示：

```text
Policy                 Tokens ↓   Constraint   Decision   Success
-----------------------------------------------------------------
No Compact                 0%        100%        100%       92%
Full Summary              61%         81%         84%       76%
Sliding Window            42%         94%         93%       88%
Structured Compact        57%         98%         97%       91%
```

注意：

上述数据只是格式示例。

禁止伪造 benchmark 数据。

---

# 25. Multi-Agent MVP

只有单 Agent + Compaction 完成后再开发。

核心对象：

```python
class AgentManager:
    async def spawn(...)
    async def wait(...)
    async def cancel(...)
    async def send(...)
```

---

# 26. Agent Identity

```python
class AgentIdentity:
    agent_id: str
    parent_id: str | None
    depth: int
    role: str | None
```

---

# 27. Agent Registry

维护：

```text
agent id
parent
children
status
task
token usage
created_at
finished_at
```

---

# 28. Agent Scheduler

第一版不要智能 scheduler。

只实现：

```python
class SchedulerConfig:
    max_agents = 4
    max_depth = 2
```

Parent 可以调用：

```text
spawn_agent(task)
wait_agent(agent_id)
```

---

# 29. Multi-Agent 第二阶段

实现 DAG：

```text
Task A ─┐
        ├→ Task C → Task E
Task B ─┘
             ↑
Task D ──────┘
```

结构：

```python
class AgentTask:
    id
    description
    dependencies
    status
    assigned_agent
```

---

# 30. Agent Budget

实现：

```python
class AgentBudget:
    max_agents: int
    max_depth: int
    max_total_tokens: int
    max_tokens_per_agent: int
    max_steps_per_agent: int
```

---

# 31. 推荐目录结构

```text
agent-runtime-lab/

├── runtime/
│   ├── __init__.py
│   ├── agent.py
│   ├── loop.py
│   ├── state.py
│   ├── events.py
│   ├── reducer.py
│   ├── stop.py
│   │
│   ├── context/
│   │   ├── builder.py
│   │   ├── models.py
│   │   ├── budget.py
│   │   ├── observer.py
│   │   └── truncation.py
│   │
│   ├── compact/
│   │   ├── base.py
│   │   ├── full_summary.py
│   │   ├── sliding_window.py
│   │   └── structured.py
│   │
│   ├── model/
│   │   ├── base.py
│   │   └── openai.py
│   │
│   ├── tools/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── runtime.py
│   │   ├── shell.py
│   │   └── files.py
│   │
│   └── multi_agent/
│       ├── manager.py
│       ├── registry.py
│       ├── scheduler.py
│       └── budget.py
│
├── policies/
│   ├── compact/
│   ├── stop/
│   └── scheduler/
│
├── evals/
│   ├── tasks/
│   ├── compaction/
│   ├── long_horizon/
│   └── multi_agent/
│
├── examples/
│   ├── minimal_agent.py
│   ├── coding_agent.py
│   └── multi_agent.py
│
├── tests/
│
├── docs/
│   ├── architecture.md
│   ├── agent-loop.md
│   ├── state-vs-context.md
│   ├── context-lifecycle.md
│   ├── compaction.md
│   ├── multi-agent.md
│   └── experiments/
│
├── .runs/
├── pyproject.toml
├── README.md
└── LICENSE
```

---

# 32. CLI

最终 MVP CLI：

```bash
agent-runtime run "fix failing tests"
```

可选：

```bash
agent-runtime run \
  "fix failing tests" \
  --workspace ./demo \
  --max-steps 30
```

debug：

```bash
agent-runtime run \
  "fix failing tests" \
  --trace
```

compact policy：

```bash
agent-runtime run \
  "fix failing tests" \
  --compact sliding
```

---

# 33. Coding Agent 示例任务

准备一个 demo repo：

```text
examples/demo_project/
```

故意加入：

- 2 个 failing tests
- 1 个简单 bug
- 1 个隐藏 constraint

Agent 目标：

```text
Analyze this repository.
Fix the failing tests.
Do not modify public API behavior.
Run tests before finishing.
```

验收：

```text
tests pass
files changed reasonable
no prohibited change
final explanation present
```

---

# 34. MVP 定义

## MVP v0.1

必须完成：

- [ ] AgentState
- [ ] AgentLoop
- [ ] ModelClient
- [ ] tool calling
- [ ] ShellTool
- [ ] FileTool
- [ ] ToolRegistry
- [ ] ToolRuntime
- [ ] ContextBuilder
- [ ] ContextObserver
- [ ] token estimation
- [ ] max steps
- [ ] execution trace
- [ ] CLI
- [ ] tests
- [ ] coding example

完成标准：

> Agent 可以独立进入一个小型 Python repo，查看文件、运行 pytest、修改文件，并在限定步骤内修复一个简单 bug。

---

# 35. MVP v0.2

必须完成：

- [ ] ContextBudget
- [ ] Tool output truncation
- [ ] CompactPolicy abstraction
- [ ] Full Summary
- [ ] Sliding Window
- [ ] Structured Compaction
- [ ] compact events
- [ ] benchmark framework
- [ ] long-horizon retention tests
- [ ] replay
- [ ] compact comparison report

完成标准：

> 同一个长任务能够分别使用三种 compaction policy 执行，并输出真实 token 与 retention 对比结果。

---

# 36. MVP v0.3

必须完成：

- [ ] AgentManager
- [ ] AgentRegistry
- [ ] spawn_agent
- [ ] wait_agent
- [ ] cancel_agent
- [ ] max_agents
- [ ] max_depth
- [ ] agent token accounting
- [ ] parent-child trace
- [ ] parallel execution example

完成标准：

> Parent Agent 可以创建至少两个 Child Agent 并行完成独立子任务，然后汇总结果。

---

# 37. 分阶段开发路线

以可验证的成果作为阶段门槛，不使用时间节点。未达到当前阶段的验收标准前，
不开始下一个阶段。

## 阶段 A — 可运行、可观察的单 Agent

实现最小 Coding Agent：显式 Agent Loop、状态、事件、模型抽象、文件与 shell 工具、
上下文构建、max-step、结构化 trace 和 CLI。使用 FakeModelClient 覆盖端到端测试，
并提供一个故意失败的小型 Python demo。

阶段完成标志：Agent 能在受限 workspace 中读取文件、运行 pytest、改动文件、再次验证，
且每一步的事件、工具结果和停止原因都可检查。

## 阶段 B — Context Inspector

引入 ContextItem、token 估算、按类型统计和最大项报告；将 context statistics 写入 run trace。
这个阶段只观察 context 如何增长，不实施 compaction。

阶段完成标志：一次运行能够解释“哪些内容进入了模型 context、各自占多少 token、最大内容是什么”。

## 阶段 C — Compaction 与评测

实现低成本 tool-output truncation，再实现 Full Summary、Sliding Window 与 Structured
Compaction。为约束、决策、失败记录和下一步建立 long-horizon retention 测试，并以真实运行数据比较 token、成功率与保留率。

阶段完成标志：相同任务可切换三种策略执行，并输出可复现、不可伪造的比较结果。

## 阶段 D — Runtime Study Notes

阅读并整理真实 Agent Runtime 的 loop、context manager、tool dispatch、state lifecycle 与 compact trigger，
将设计取舍映射到本项目 `docs/experiments/`。不复制实现，以概念对应和实验结论为主。

阶段完成标志：每个关键设计都有“问题、假设、实现、实验、结果、结论”的记录。

## 阶段 E — 最小 Multi-Agent

仅在单 Agent 与 compaction 已稳定后，增加 AgentManager、Registry、spawn/wait/cancel、
父子 trace、并发限制、深度限制和 token budget。首版只支持独立子任务的并行执行与显式汇总。

阶段完成标志：Parent 能并行委托两个独立子任务，受预算约束地等待并汇总结果。

## 阶段 F — 打磨与公开展示

整理 README、架构图、benchmark、demo 输出、贡献指南、roadmap 和版本发布；在准备充分后再参与外部 runtime 项目的相关 issue 或 PR。

---

# 38. 每周工作方式

严格使用：

```text
Read
  ↓
Implement
  ↓
Experiment
  ↓
Measure
  ↓
Document
  ↓
Push
```

禁止：

```text
只看源码
只看论文
只写学习笔记
只堆 feature
```

每个实验至少输出：

```text
Problem
Hypothesis
Implementation
Experiment
Result
Conclusion
```

---

# 39. Git 分支策略

建议：

```text
main
│
├── feat/minimal-agent-loop
├── feat/context-observer
├── feat/context-budget
├── experiment/full-summary
├── experiment/sliding-window
├── experiment/structured-compaction
└── experiment/multi-agent
```

小步 commit。

避免：

```text
"implement everything"
```

这种 commit。

建议：

```text
feat(runtime): add explicit agent state
feat(tools): add shell tool timeout
feat(context): track tokens by item
feat(compact): introduce compaction policy interface
eval(compact): add constraint retention benchmark
```

---

# 40. 测试要求

任何核心模块必须有 unit test：

```text
AgentLoop
ContextBuilder
ToolRuntime
ContextBudget
CompactionPolicy
AgentRegistry
```

必须额外有 integration test：

```text
task
 ↓
model mock
 ↓
tool call
 ↓
tool result
 ↓
final answer
```

模型调用尽可能 mock，避免 CI 花钱。

---

# 41. Mock Model

实现：

```python
class FakeModelClient:
    responses: list[ModelResponse]
```

测试：

```text
response 1 → shell
response 2 → read file
response 3 → final
```

从而测试整个 Agent Loop。

---

# 42. Security Boundary

ShellTool 默认只能运行：

```text
workspace directory
```

至少加入：

```text
cwd sandbox
timeout
output limit
```

项目 README 明确声明：

> Agent Runtime Lab 是研究项目，不应直接在包含重要数据或凭据的宿主环境中运行不受信任任务。

MVP 不需要实现真正 container sandbox。

后续可以研究。

---

# 43. Observability

所有 Agent Run 输出：

```text
run_id
step_count
model_calls
tool_calls
input_tokens
output_tokens
compact_count
duration
status
```

最终：

```text
Run Summary

Status             completed
Steps              18
Model Calls        18
Tool Calls         12
Input Tokens       48,281
Output Tokens       7,821
Compactions             1
Duration            93.2s
```

---

# 44. README 必须回答的问题

项目 README 不应该只写 install。

必须回答：

### What is an Agent Runtime?

### What is an Agent Loop?

### State vs Context 有什么区别？

### Tool Call 如何进入下一轮 Context？

### 为什么 Context 会不断增长？

### Compaction 在解决什么问题？

### 为什么 Summary 可能丢失 Agent State？

### Multi-Agent 本质增加了什么 Runtime Complexity？

这部分本身就是项目成果。

---

# 45. 设计原则

## Principle 1

**Explicit over Magic**

核心状态必须可见。

---

## Principle 2

**Mechanism over Framework**

研究机制，不堆框架 API。

---

## Principle 3

**Measure over Impression**

不能说：

```text
"感觉这个compact更好"
```

必须有数据。

---

## Principle 4

**Policy over Hardcode**

变化行为尽可能设计成 policy。

---

## Principle 5

**Single-Agent First**

没有理解单 Agent，不做复杂 Multi-Agent。

---

# 46. 后续研究方向

MVP 完成后可以选择其中一个深入。

## 方向 A — Context Engineering

研究：

```text
semantic pruning
priority context
tool-output compression
context caching
structured memory
state reconstruction
adaptive compaction
```

---

## 方向 B — Agent Loop

研究：

```text
termination policy
reflection
retry
loop detection
progress detection
planning
replanning
verification
```

---

## 方向 C — Tool Runtime

研究：

```text
tool policy
permissions
sandbox
parallel tools
tool caching
tool retries
tool dependency
```

---

## 方向 D — Multi-Agent

研究：

```text
scheduler
task DAG
budget allocation
depth control
agent communication
conflict resolution
agent cancellation
result aggregation
```

---

## 方向 E — Agent Evaluation

研究：

```text
long horizon benchmark
state retention benchmark
tool efficiency
token efficiency
recovery ability
agent stability
```

---

# 47. 第一阶段不要做的优化

Coding Agent 必须遵守：

不要过早：

```text
抽象十层 interface
设计 plugin system
加入 database
加入 web framework
支持五个 model provider
做 distributed scheduler
做漂亮 UI
```

优先：

```text
能运行
能观察
能实验
能比较
```

---

# 48. Coding Agent 执行要求

每次开始实现一个阶段前：

1. 阅读本 PRD。
2. 阅读当前仓库 README。
3. 查看已有代码和测试。
4. 不重构无关模块。
5. 优先完成当前 milestone。
6. 为新增核心逻辑添加测试。
7. 更新相关 docs。
8. 输出本轮修改摘要。

---

# 49. Coding Agent 每次任务输出模板

每次 coding session 完成后输出：

```text
## Completed

- ...

## Files Changed

- ...

## Tests

- ...

## Design Decisions

- ...

## Known Limitations

- ...

## Next Recommended Task

- ...
```

---

# 50. 第一个 Coding Agent Prompt

建议直接把下面任务交给 Coding Agent：

```text
Read PRD.md completely.

Implement Agent Runtime Lab MVP v0.1-alpha.

Scope:

1. Create project structure.
2. Implement:
   - AgentState
   - AgentEvent
   - ModelClient protocol
   - FakeModelClient
   - AgentLoop
   - Tool abstraction
   - ToolRegistry
   - ShellTool
   - FileTool
   - ToolRuntime
   - ContextBuilder
   - max-step stopping
   - basic execution tracing

3. Add unit tests and one end-to-end test using FakeModelClient.

4. Do NOT implement:
   - compaction
   - multi-agent
   - MCP
   - web UI
   - plugin system

5. Keep the Agent Loop explicit and easy to read.

6. Use Python 3.12+, asyncio, pydantic, pytest.

7. After implementation:
   - run tests
   - update README
   - write docs/agent-loop.md
   - report known limitations

Acceptance criteria:

- `pytest` passes.
- A fake model can trigger a tool call and then return a final answer.
- Every tool call and tool result appears in AgentState/events.
- Agent stops when max_steps is exceeded.
- ShellTool supports timeout.
- Tool output is captured as structured data.
```

---

# 51. 第二个 Coding Agent Prompt

完成 v0.1 后：

```text
Implement the Context Observer milestone.

Requirements:

1. Introduce ContextItem.
2. Track token usage per context item.
3. ContextBuilder must return a structured ModelContext.
4. Implement ContextObserver.
5. Show:
   - total context tokens
   - context window usage
   - tokens grouped by item type
   - largest context items

6. Store context statistics in run traces.

7. Add tests.

Do not implement compaction yet.

Goal:

We need to understand exactly how Agent context grows before designing any compaction strategy.
```

---

# 52. 第三个 Coding Agent Prompt

完成 Context Observer 后：

```text
Implement pluggable context compaction.

Create a CompactionPolicy protocol.

Implement:

1. FullSummaryCompaction
2. SlidingWindowCompaction
3. StructuredCompaction

Requirements:

- Compaction must emit start/end AgentEvents.
- Record before_tokens and after_tokens.
- Never mutate system instructions or the original task.
- Preserve the latest N turns.
- Structured compaction must preserve:
  constraints,
  decisions,
  modified files,
  tool findings,
  failed attempts,
  open questions,
  next steps.

Create tests verifying that important state survives compaction.

Do not implement multi-agent yet.
```

---

# 53. Definition of Done

Agent Runtime Lab 第一阶段最终完成标准：

```text
✓ 可以运行 coding task
✓ Agent Loop 显式
✓ Agent State 显式
✓ Tool Runtime 显式
✓ Context 可观测
✓ Context token 可分类
✓ Tool output 可截断
✓ Compaction 可插拔
✓ 有至少 3 种 compact policy
✓ 有真实 benchmark
✓ 有 long-horizon retention tests
✓ Multi-Agent 可以 spawn/wait/cancel
✓ 所有 runtime 行为有 trace
✓ README 能讲清 Agent Runtime
✓ GitHub commits 清晰
✓ 有至少一个 release
```

---

# 54. 项目最终价值

本项目最终不是为了证明：

> “我会写一个 AI Agent。”

而是证明：

> “我理解 Agent Runtime 的核心状态机、上下文生命周期、工具执行、压缩和多 Agent 调度，并且能够通过实验与评测修改这些机制。”

这应该成为整个项目所有技术决策的最高优先级。
