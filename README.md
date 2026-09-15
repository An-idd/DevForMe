# Verified Coding Agent Runtime

按项目规范执行编码任务，以关联代码快照的证据判断完成状态。

当前实现 P01 领域基础、P02 串行工作流和 P03 工具运行时：包含 QualityGate、
有限修复、受控文件/进程工具，以及共享的持久事件和差异工件。
尚未实现真实 Agent、CLI、工作区快照、真实验证 Evidence 或最终交付。

## 开发环境

需要 Python 3.12+。Windows / PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src/coding_agent
```

若 `python` 不在 PATH 中，首条命令使用本机 Python 3.12+ 的绝对路径。
测试不需要模型凭据。P03 使用临时文件/仓库和真实受限进程；网络拒绝测试会尝试
连接本机地址及 Unix socket，并要求操作被内核拒绝。

macOS / Linux 的项目虚拟环境命令：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy src/coding_agent
```

实际验证平台及版本见开发计划；命令示例不代表所有平台均已验证。

## 领域边界

- `core/models.py`：需求、风险、任务、范围、权限、验收与 Evidence 结构。
- `core/graph.py`：不可变 DAG；新增任务或依赖返回经过校验的新图。
- `core/state.py`：状态枚举和纯转换校验；`core/workflow/engine.py` 独占运行状态写入。
- 任务状态单独传入图查询；图不维护第二份可写状态。只有前置任务全部为
  `VERIFIED` 的 `PENDING` / `READY` 任务会被返回。
- 领域模型使用冻结对象和 tuple，拒绝未知字段。加载外部数据使用
  `model_validate` / `model_validate_json`，不能用绕过校验的 `model_construct`
  或 `model_copy(update=...)` 加载不可信数据。
- Evidence 的结果、来源、带时区时间及版本字段由调用方明确提供。
  结构合法不代表真实执行或门禁通过；P02 QualityGate 校验身份、关联、结果与版本，
  来源真实性和真实文件快照仍由后续阶段提供。
- ScopePolicy / PermissionPolicy 描述边界；P03 的纯策略与具体文件/进程后端负责执行。

## P02 工作流边界

- 从 `coding_agent.core.workflow` 导入 RunSpec、PlanApproval、WorkflowEngine、
  TaskScheduler、StateMachine、QualityGate 和结构化结果。
- `WorkflowEngine(spec, coder=..., verifier=..., reviewer=..., read_revision=..., writer=...)`
  由应用层注入受信适配器；`await engine.run(approval)` 执行一次。
  Coder 只收到不可变 TaskSpec、版本及修复反馈，无法通过返回值写入 VERIFIED。
- 所有模式核对绑定完整 RunSpec 指纹的 PlanApproval；同一计划中的任务和修复复用授权。
  计划范围、验收、权限或预算变更后旧授权不再匹配。授权记录必须由控制器依据已有用户
  授权提供，不能把 Agent 自述当成授权。提交授权与执行授权分别处理。
- FAST 仅在低风险且未声明必需审查时省略 Reviewer 调用；中风险至少 STANDARD，
  高风险至少 STRICT。各模式保留全部声明检查和 REVIEWING 状态下的门禁。
- TaskSpec.max_attempts 限制全部 Coder 尝试；RunSpec.max_review_fixes 限制其中的审查修复，
  max_total_attempts 限制本次运行的总尝试。缺失/不可用结果不会自动触发代码修复。
- `testing.py` 提供 FakeCoder/FakeVerifier/FakeReviewer/FakeEventWriter，必须明确提供
  脚本结果。它们不执行工具、不证明真实代码正确。RevisionReader 在 P02 也是受信输入。
- 工作流事件先交给 writer，再派发适配器或改变内存状态；写入失败立即停止。
  外部 asyncio 取消会记录中断结果并重新抛出 CancelledError，可查看 `engine.result`。
  适配器超时是进程内的协作式取消，不是进程隔离或强制终止保证。
- Engine 不可重复运行、恢复或重置计数。状态对外是只读快照；P12 再实现恢复核对。
- `tasks_verified` 仅表示该图各任务在各自快照上的门禁通过。它不代表最终快照、全部需求
  或交付通过；后续任务可能改变先前验证过的代码。结果中的 revision 是最后观察到的版本。
  最终集成验证与交付尚未实现，空图也不会成为需求完成证明。

完整要求见 [开发规格](CODING_AGENT_DEVELOPMENT_SPEC.md)，当前进度和检查结果见
[开发计划](DEV_PLAN.md)，仓库维护方式见 [AGENTS.md](AGENTS.md)。

## P03 工具与持久记录

- `core/tools.py` 定义 Read、Search、Patch、Shell、Git、ToolRequest、ToolResult 与
  ToolApproval；`core/tool_policy.py` 提供无 IO 的 ALLOW/ASK/DENY 决策。
- `tools.ToolRuntime` 绑定 RunSpec、任务、仓库根目录、PlanApproval、RevisionReader
  和 `session.records.JsonlJournal`。应用层调用 `await runtime.execute(request)`。
  工具调用者只提交请求；授权和后端由受信控制器提供。
- 原生文件工具只处理仓库内的 UTF-8 普通文件。Read/Search 遵循 forbidden；Patch
  还须匹配 allowed。Patch 使用当前文件 SHA256 防止覆盖已有编辑，`None` 表示只创建。
  不跟随符号链接，不操作有硬链接别名的文件或特殊文件，不自动建父目录、删除文件。
- Shell 要求绝对可执行路径和参数数组。与计划验收命令、根工作目录完全匹配时复用
  计划授权；其他命令返回 `needs_approval`。控制器可提供绑定具体命令、目录、任务、
  计划和当前版本的 ToolApproval，以新 request ID 重提；授权不能覆盖 DENY。
- 当前进程后端只在本次 macOS 宿主验证：使用 `sandbox-exec`，只读、离线且禁止 fork。
  写文件用 Patch；Shell 中的写入、网络、Unix socket、控制记录访问会被拒绝。
  `/dev/null` 是唯一写入例外。解释器/工具链额外读取目录由控制器明确指定；子进程
  不继承控制器环境或额外文件描述符。缺少后端时拒绝，不退回普通 subprocess。
- `database:test_only`、联网 shell、复杂 forbidden glob 的进程执行尚不支持。
  `database:deny` 不提供数据库服务/凭据；它不能禁止在已许可文件上做内存中的 SQL
  计算。含数据库数据的文件须列入 forbidden。需写缓存、派生子进程或测试数据库的
  测试/构建不在当前后端能力内；P09 必须解决所需权限并实际验证，不能跳过后记为通过。
- Git 当前只提供隔离的 status/diff，禁用外部 diff、textconv、hooks、fsmonitor 及
  全局配置；不支持提交、清理、外置 Git 目录或 Worktree 生命周期，后者属于 P04。
- 默认进程超时 30 秒（最多 300 秒），输出 64 KiB（最多 1 MiB），单文件 1 MiB。
  超时/取消会杀死并回收进程；输出截断显式标记。工作区执行期间要求控制器独占，
  不保证抵御另一个未受限进程同时移动目录或替换文件。
- JsonlJournal 独占新建 `events.jsonl`；工作流和工具共享 writer 的 `next_sequence`。
  先 fsync 请求再执行，随后记录真实结果和带 SHA256 的脱敏工件。仓库内记录必须位于
  `.agent/`；原生工具与 Shell 均不能修改控制记录。状态仍由 WorkflowEngine 独占。
- 日志/工件写失败立即停止；有未完成请求时不能继续工具或生命周期记录。
  `inspect_journal(path)` 只读返回事件、待核实请求和不完整尾行，不修复、不重放。
  脱敏 RunSpec 工件用于审查，不能直接当成可恢复计划；版本化计划与恢复分别留给 P07/P12。
  已知凭据应通过 `Sanitizer(secrets=...)` 注入脱敏器，不能依赖模式匹配发现所有秘密。

P03 测试见 [工具集成与故障测试](tests/test_tools.py) 和
[策略与结果约束测试](tests/test_tool_policy.py)。Windows/Linux 进程后端未实现；
原生文件工具依赖 POSIX，本次仅在 macOS 完成验收。
