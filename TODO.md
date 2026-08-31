# Agent Runtime Lab — Todo

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

## 当前开发主线

- [x] Context Inspector 与 Context Budget 触发信号。
- [x] Tool output truncation 基线。
- [x] Sliding Window Compaction 基线。
- [x] ContextUnit：保证单次 tool call/result 在压缩时不可拆分。
- [ ] Full Summary Compaction。
- [ ] Structured Compaction 与 long-horizon retention 评测。
- [ ] 真实模型 provider adapter。
