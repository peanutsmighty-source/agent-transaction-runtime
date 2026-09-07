# Coding Task 验收场景

## 目的

这些场景检查 Agent Runtime 是否能在真实临时文件、真实 Python 进程和真实测试退出码之间完成闭环。它们不是模型 benchmark：模型决策使用可复现的 `FakeModelClient`，因此只证明 Runtime 控制流，不证明某个大模型能自主解决任务。

## 场景

1. 只读多文件诊断：读取配置和客户端实现，最终答案必须指出跨文件原因，并且不能写文件。
2. 单文件修复：读取并修改错误函数，通过 ShellTool 运行测试，最终再由独立 CommandTaskVerifier 重跑测试。
3. 二次修复：第一次实现和测试失败，模型仍错误地声明完成；Verifier 拒绝后，失败输出进入下一轮 Context，第二次修复才通过。
4. 多文件修改：同一任务修改实现和仓库测试，隐藏验收测试与文件后置条件组合判定结果。

## 两层证据

```text
Fake Model + 真实文件/进程/退出码
    -> 证明 Runtime 的确定性控制流和验收反馈

真实 Provider + 相同任务重复运行
    -> 评估模型成功率、波动、token、延迟和失败原因
```

第二层尚未完成，因此不能把这些测试描述为“真实模型 Coding benchmark”。

真实 Provider 的首个重复实验入口是
`test_real_responses_single_file_coding_task`。它只给模型 FileTool，避免开放任意 Shell；
模型完成后由 Runtime 所有者配置的固定 unittest 命令独立验收。该测试默认跳过，只有显式提供真实 API 环境变量时才运行。

## DeepSeek 单文件修复重复实验（2026-09-06）

- Provider：DeepSeek Responses API
- Model：`deepseek-v4-flash`
- 重复次数：3；每次由 pytest 创建全新的临时 workspace
- 工具权限：允许 FileTool 读取和修改 workspace；不向模型提供 ShellTool
- 任务：修复 `calculator.add`，同时覆盖正数和负数
- 独立验收：测试文件位于 Agent workspace 外，Runtime 使用固定 unittest 命令执行；模型无法通过 FileTool 读取或修改这些验收条件
- 最终结果：协议修复后 3/3 run 进入 `COMPLETED`，最终 verification 均通过；单次测试耗时分别为 6.76s、6.99s、6.95s

第一次把 acceptance test 移出 workspace 后重复运行时结果为 2/3。失败 run 的 trace 显示：模型第一轮同时产生两个 FileTool 调用，Runtime 第二轮把 input 交错序列化为 `call1/output1/call2/output2`，DeepSeek 返回 HTTP 400。修复后 Runtime 保留响应批次边界，改为 `call1/call2/output1/output2`，并将整批调用与结果作为不可拆分 ContextUnit；新增 Mock 回归后再进行上述 3 次真实运行，全部通过。

这个过程同时暴露出原错误 trace 只有 HTTP 状态码。Provider error 现额外保留供应商结构化错误 detail，便于区分协议字段错误、限流和服务故障，但不会记录请求头或 API key。

密钥从仓库外文件读入进程环境，没有写入 trace 或仓库。本结果只说明这个固定小任务在三次样本中成功，样本量很小，也没有覆盖错误修复重试、多文件理解、Shell 工具选择、长 Context 或网络抖动。后续场景必须分别重复，不能用本结果外推总体成功率。

另外两个 opt-in 入口分别是 `test_real_responses_read_only_multi_file_diagnosis`
和 `test_real_responses_multi_file_implementation`。前者用候选答案断言与“没有写操作”
共同验收诊断任务；后者用 workspace 外的 unittest 同时检查两个模块的行为。

多文件场景首次重复时，只读诊断为 2/3，多文件实现为 3/3。失败的诊断 run
已经正确读取两个源码，却继续枚举位于 workspace 内的 `.runs` 并读取自己的
`context.jsonl`，最终触发 `max_steps`。因此真实验收现把 trace root 放在 Agent
workspace 外：trace 是 Runtime 的调试数据，不应伪装成用户项目文件干扰模型。

调整后只读多文件诊断重新重复 3 次，结果 3/3 通过，单次耗时分别为
7.67s、8.27s、8.30s。多文件实现首次三次运行均通过隐藏测试，为 3/3。
仍未完成的真实模型场景是“第一次实现被真实隐藏测试拒绝，再根据失败信息二次修复”；
不能用人为无条件拒绝第一次答案来伪造这个证据。

该场景采用显式故障注入：系统要求真实模型第一次写入 `abs(a) + abs(b)` 的错误实现，
外部 unittest 因负数用例真实失败；失败详情进入 `VERIFICATION` Context 后，模型必须
再次写文件并通过相同测试。这个实验验证“失败反馈恢复链路”，不用于统计模型自然犯错率。

真实 DeepSeek 重复结果为 3/3，单次耗时分别为 10.74s、10.89s、13.78s。
每次 verification history 均严格为 `[False, True]`，并至少发生两次对
`calculator.py` 的写入。至此 P0 的四类 Coding Task 都已有确定性场景，其中单文件、
只读多文件、多文件实现和故障注入恢复也都完成了三次真实 Provider 重复。

## 验证边界

测试通过只能证明被断言的行为。模型可见的仓库测试不能作为唯一验收，因为模型可能错误修改测试；场景使用独立的 acceptance test，并可通过 CompositeTaskVerifier 叠加文件契约。生产系统还需要隔离执行、控制不稳定测试，并保护隐藏验收条件。
