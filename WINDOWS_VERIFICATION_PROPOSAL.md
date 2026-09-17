# Windows 完整验证环境方案（TODO，已暂停）

2026-09-17：用户要求先暂停 Windows 工作。本文件仅保留候选方案与已知阻塞，
当前不再等待路线选择，不继续诊断、后端开发或系统组件安装。
仅在用户明确恢复 Windows 工作后重启评估；任务清单见 [DEV_PLAN.md 第 9.1 节](DEV_PLAN.md#91-todowindows-验证适配已暂停)。

当前 LPAC 后端适用于它已经验证的受限命令，但尚不能支撑 AutoResearch 的完整 Python 测试：
asyncio 导入需要创建套接字，Python 0700 目录使用不继承父目录权限的 ACL。
不采用给整个宿主目录放权、允许外网、修改 Python 标准库或跳过业务测试的方式放行。

## 建议：增加显式选择的 Docker 验证后端

此方案尚未实现或实测，不作为当前可用能力。保留原 LPAC 后端。
Windows 主进程仍负责编码、计划、日志、证据和门禁；只有验收命令进入 Linux 容器。

拟定运行边界：

- 只允许控制器配置的本地 Docker Desktop 和固定 digest 镜像，不接受模型提供的镜像/挂载/启动参数。
- 只将按 scope 过滤后的临时源码副本只读挂载；不挂载原项目、日志、凭据、Docker socket 或宿主用户目录。
- 容器使用非 root 用户、只读根文件系统、临时 /tmp、无新增权限、丢弃 capabilities、内存/PID/时间上限。
- 隔离网络栈，禁用外网和宿主连接；镜像准备时的下载单独进行并记录，验收期不联网。
- 退出码和标准输出由控制器从 Docker 进程与容器状态取得，不能使用测试程序可改写的“成功文件”。
- 取消/超时必须停止并移除本次容器；不能因为 Docker CLI 退出而误认所有子进程结束。
- 仍执行原 pytest、Ruff、compileall、secret_scan，并测试宿主/控制目录不可读写及网络不可达。
- Linux 容器通过只证明该 Linux 工具链结果；Windows 原生兼容性仍独立标为未通过，不声称等价。

注意：Docker 的 network none 仍有容器自己的 loopback；必须在实现前明确当前 network=False
要求是“禁止宿主/外部网络”还是“禁止任何 socket/IPC”，不能静默改变规格。
如要求连容器内 socket 都禁止，则 asyncio 能否运行需要单独能力验证。

## 恢复后再决定本机准备

本机没有 Docker 命令，WSL 尚未安装，也没有 Windows Sandbox 入口。
采用该方案需安装 Docker Desktop，并满足其 WSL 2/虚拟化前置条件；可能需要管理员操作和重启。
不自动重启、不更改日常 Codex 配置，也不在未选择方案前安装系统组件。

选择该路线后，先确认系统条件与隔离语义，再完成可审阅的后端实现及离线检查，
最后进行系统安装确认和真实隔离验收。依赖准备与验收运行分别记录。

也可继续只使用原生 LPAC；这种选择保留当前两个能力阻塞，全链路评估暂时无法通过。

官方资料（2026-09-17 核对）：

- [Windows 安装要求](https://docs.docker.com/desktop/setup/install/windows-install/)
- [容器运行与限制参数](https://docs.docker.com/reference/cli/docker/container/run/)
- [None 网络仍保留 loopback](https://docs.docker.com/engine/network/drivers/none/)
- [CPython 3.12.10 mkdir 实现](https://github.com/python/cpython/blob/v3.12.10/Modules/posixmodule.c)

本轮原生 ACL 探针额外遇到 ctypes DLL 初始化失败、MinGW 探针 WinError 623；
它们未成功执行 ACL 对照，不能把对照结果写成已验证。
