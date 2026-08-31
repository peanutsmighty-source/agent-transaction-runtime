# Sandbox Runner

## 它解决什么问题

模型只产生“运行这条命令”的请求，真正决定命令在哪里、以什么权限运行的，不应该是 AgentLoop，也不应该写死在 ShellTool 中。否则从本机进程升级到 Docker、操作系统沙箱或云端 VM 时，需要修改工具协议和 loop。

`cwd` 只决定命令从哪个目录开始执行，不构成安全边界。未隔离的进程仍能通过绝对路径访问当前用户可访问的文件，也可能访问网络。

## 当前数据流

```text
ToolCall(shell)
      ↓
ApprovalPolicy
      ↓
ShellTool：校验参数、限制 cwd、格式化输出
      ↓ CommandRequest
SandboxRunner
      ↓
HostRunner：当前用户权限下创建本机进程
      ↓ CommandExecution
ToolResult：包含 runner 与 isolation 元数据
```

`SandboxRunner` 是协议，不承诺每个实现都安全。每个实现必须声明 `IsolationKind`。`HostRunner` 明确返回 `none`，目的是保留开发基线并防止把“有一个 Runner 类”误解成“已经有沙箱”。`DockerSandboxRunner` 返回 `container`，通过 Docker 创建短生命周期容器。

## 为什么这样设计

成熟 Coding Agent 通常把授权策略、工具协议和执行环境分开：授权策略决定是否同意，执行环境通过 OS sandbox、容器或 VM 限制进程实际能做什么。分层后，同一个 ToolCall 可以在 HostRunner 和后续 DockerSandboxRunner 上运行并对比结果，AgentLoop 不需要知道 Docker 命令或挂载细节。

## Docker 后端的安全基线

`DockerSandboxRunner` 使用参数数组调用 Docker CLI，而不是把 Docker 命令拼成另一段 shell 字符串。容器默认：

- 使用 `--network none` 关闭网络；必须显式设置才能使用 bridge 网络。
- 根文件系统只读，只把 workspace 挂载到 `/workspace` 并允许写入。
- 删除 Linux capabilities，并设置 `no-new-privileges`。
- 使用非 root 用户，限制 CPU、内存和进程数。
- `/tmp` 是容量受限的临时文件系统，`HOME` 指向 `/tmp`。
- 使用 `--pull never`，不会在执行 Agent 命令时隐式下载镜像。
- 超时或 Agent 任务被取消后，强制删除以唯一名称创建的容器。
- 不向容器转发宿主机环境变量或 Git/SSH 凭证。

CLI 使用 `--shell-runner docker` 显式选择容器后端；如果 Docker 不存在或镜像未准备好，返回失败，不会退回宿主机执行。

## 当前限制

- `HostRunner` 没有文件系统或网络隔离。
- HostRunner 超时会终止直接创建的 shell 进程，但当前版本尚未承诺清理完整进程树。
- Docker 容器共享宿主机内核，不等于虚拟机。
- workspace 是读写挂载，Agent 仍然可以删除或破坏项目内文件。
- 开启网络后尚无域名级代理或 allowlist。
- 单元测试可以验证参数策略和缺失 Docker 时的 fail-closed 行为；真实隔离强度仍需在安装 Docker 的环境中做逃逸、网络和资源限制集成测试。

即使 ToolResult 中出现 `runner=host`，也只表示执行后端可观测，不表示安全。
