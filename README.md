# Verified Coding Agent Runtime

按项目规范执行编码任务，以关联代码快照的证据判断完成状态。

当前实现 P01 领域基础、P02 串行工作流、P03 工具运行时、P04 会话工作区、
P05 模型适配及 P06 项目初始化 CLI。包含 QualityGate、有限修复、受控工具、持久事件、
源码快照、Worktree、OpenAI Responses 适配和有来源的项目知识。
P05/P06 模型行为通过离线模拟测试，另有实验性 CodexCoder 接入本地 Codex 编码循环；
真实账号/API 冒烟待完成。plan/run CLI、计划上下文集成、真实验证 Evidence 与最终交付
尚未实现；跨平台复验及阶段状态见开发计划。

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
连接本机地址及平台支持的 Unix socket，并要求操作被内核拒绝。测试夹具和 P04 控制器工作区管理需要安装 Git；
Windows 产品中的只读 Git helper 使用安装时自动加入的 Dulwich。

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

## P06 项目初始化

安装后使用虚拟环境的 `agent` 命令；也可用 `python -m coding_agent`：

```powershell
.\.venv\Scripts\agent.exe init 'D:\Projects\Example'
.\.venv\Scripts\agent.exe init 'D:\Projects\Example' --refresh
.\.venv\Scripts\agent.exe init 'D:\Projects\Example' --rule '新增库存写入必须经过 InventoryService。'
.\.venv\Scripts\agent.exe init 'D:\Projects\Example' --refresh --focus src/service.py --json
```

默认离线读取已有 AGENTS.md、CONTRIBUTING.md、配置和代表性源码；记录事实、明确规则、
假设、源码引用、验证命令与覆盖缺口。嵌套规范保留目录作用域。`--focus` 指定文件/目录，
`--forbid 'private/**'` 排除路径，均可重复。刷新时应沿用原来的 focus/forbid；
改变参数会改变探索范围。命令发现不会安装依赖、执行仓库程序或生成通过证据。

如需模型解释模块职责和调用关系，显式配置 `OPENAI_API_KEY`，使用
`agent init PATH --refresh --model MODEL`。该选项经现有 OpenAI Provider 发起一次调用，
无执行工具，输出上限 8192 tokens、超时 60 秒；费用和限额由账号配置决定。
本地 Codex adapter 尚未接入 Explorer，真实 API 的初始化效果尚未验证。

产物均在目标仓库 `.agent/`：

- `project.md`：生成摘要与用户补充。编辑内容放在
  `<!-- coding-agent:generated:start -->` / `<!-- coding-agent:generated:end -->` 标记之外。
  已有无标记的 project.md 会作为用户内容保留；支持 Windows CRLF。
- `project.json`：当前修订及来源指纹索引，不作为另一份可编辑规则。
- `knowledge-<revision>.json`：不可变历史修订，保存结构化摘要、来源引用和脱敏后的源码摘录。
  指纹对应原始文件；摘录可能截断，不能用摘录重建整个仓库。
- `init-<id>/events.jsonl`：探索、模型调用和发布的请求/结果；失败或未知结果保留。
  `init.lock` 防止并发初始化，结果未落盘时保留锁以供核查。

重复初始化复用原修订；来源或用户内容变化时返回 `stale`，需明确 `--refresh`。
刷新保留用户区和来源未变的知识，发布前重查输入。生成区人工改写、元数据不一致或
发布中断时会拒绝覆盖，并提示检查日志和历史修订；自动恢复留待 P12。
有必要问题时返回 `blocked`：在规范或 `--rule` 中补充答案，再通过模型刷新重评估。
模型须保留问题身份，并逐字引用规范或用户来源中的答案；省略问题或降低 required 标志
不能清除阻塞。答案来源变化会重新打开问题。离线刷新保留这些问题，不自行判断答案语义。
`stale`、`blocked` 和错误的退出码为 2，成功为 0，中断为 130。

当前探索是有界文本分析：最多枚举 10,000 项，默认选取 12 个来源；
既有来源、明确 focus 和规范可扩展到 24 个。每文件最多读取 1 MiB，
每仓库来源保留 32 KiB 摘录，总仓库摘录预算 192 KiB，用户补充另限 32 KiB；
超大规范拒绝初始化，普通源码截断会标注。
默认排除控制目录、常见依赖/生成物及凭据路径，并脱敏已知秘密和常见凭据格式；
额外敏感目录应通过 `--forbid` 排除。源码关系只表示代表性观察，不是完整调用图。
未读文件的内容变化不构成已读来源的变化，任务涉及它时必须通过 focus 补充探索。

应用入口为 `await coding_agent.application.initialize(Path(...), ...)`；
返回状态、知识修订、变化来源、问题与日志位置，供 P07/P08 的首次 plan/run 流程复用。
初始化修订没有计划版本，不能作为任务 VERIFIED 或 P04 全工作区快照使用。

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
  P04 提供真实文件快照；执行证据来源真实性由 P09 继续落实。
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
  ToolApproval；`core/tool_policy.py` 提供无 IO 的 ALLOW/ASK/DENY 决策，
  `core/paths.py` 提供原生工具与隔离 Git helper 共用的路径规则。
- `tools.ToolRuntime` 绑定 RunSpec、任务、仓库根目录、PlanApproval、RevisionReader
  和 `session.records.JsonlJournal`。应用层调用 `await runtime.execute(request)`。
  工具调用者只提交请求；授权和后端由受信控制器提供。
- 原生文件工具只处理仓库内的 UTF-8 普通文件。Read/Search 遵循 forbidden；Patch
  还须匹配 allowed。Patch 使用当前文件 SHA256 防止覆盖已有编辑，`None` 表示只创建。
  不跟随符号链接，不操作有硬链接别名的文件或特殊文件，不自动建父目录、删除文件。
- Shell 要求绝对可执行路径和参数数组。与计划验收命令、根工作目录完全匹配时复用
  计划授权；其他命令返回 `needs_approval`。控制器可提供绑定具体命令、目录、任务、
  计划和当前版本的 ToolApproval，以新 request ID 重提；授权不能覆盖 DENY。
- macOS 进程后端使用 `sandbox-exec`，只读、离线且禁止 fork。
  写文件用 Patch；Shell 中的写入、网络、Unix socket、控制记录访问会被拒绝。
  `/dev/null` 是唯一写入例外。解释器/工具链额外读取目录由控制器明确指定；子进程
  不继承控制器环境或额外文件描述符。缺少后端时拒绝，不退回普通 subprocess。
- `database:test_only`、联网 shell 尚不支持；macOS 进程后端还拒绝复杂 forbidden glob。
  `database:deny` 不提供数据库服务/凭据；它不能禁止在已许可文件上做内存中的 SQL
  计算。含数据库数据的文件须列入 forbidden。需写缓存、派生子进程或测试数据库的
  测试/构建不在当前后端能力内；P09 必须解决所需权限并实际验证，不能跳过后记为通过。
- 未绑定 P04 工作区时，Git 工具只提供隔离的 status/diff，禁用外部 diff、textconv、
  hooks、fsmonitor 及全局配置；不支持提交、清理或任意外置 Git 目录。
  P04 工作区的 Git 状态/差异改由原生快照读取，生命周期仅对控制器开放。
  diff 先在同一沙箱中列出索引路径，再按原生读取策略筛选并使用字面路径生成差异，
  覆盖已删除的 forbidden 文件及控制目录；禁用重命名推断和彩色输出。
  索引清单受输出大小限制，失败、截断或无法完整解码时停止；两次调用共用超时预算。
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
  多行凭据同时按完整值和各非空行脱敏，覆盖 diff 前缀及搜索结果中的行片段；相同的
  普通文本行也可能被保守脱敏，实际写入文件和前后内容哈希不受影响。

### Windows 后端

- 原生 Read/Search/Patch 和 JSONL 日志使用 Windows 无跟随句柄；仅接受本地固定 NTFS
  卷，拒绝连接点、其他 reparse point、硬链接、设备名、备用数据流、尾点/空格和 8.3 别名。
  路径字段保留原始字符，不自动去空格。Patch 保留目标 DACL，内容 fsync 后以
  MoveFileExW WRITE_THROUGH 安装；Windows 不使用 POSIX 目录 fsync。
- `WindowsReadOnlyProcess` 使用每次新建的 LPAC 身份和 Job Object。运行前复制允许的
  仓库输入及受信工具链，对执行副本只授予读取/执行权限，不修改原目录 ACL。禁止联网和
  创建子进程，限制进程内存 512 MiB，禁用 Win32k 系统调用，只继承标准输入/输出句柄。
  Windows 自身的必要系统资源仍由 LPAC 能力控制；这不等同于完整虚拟机。
  Job 中的未处理原生异常直接退出并保留错误码，不等待崩溃弹窗确认。
- Windows 会提供该次 AppContainer 私有可写存储；正常结束、失败、超时和取消都清理
  profile 与执行副本。原仓库、控制记录及宿主用户文件不授予访问。机器断电后的孤儿
  profile/副本核对和清理仍属 P12，不能声称已具备崩溃恢复。
- Windows 默认启用该后端；Shell 工具链目录须由控制器通过
  `WindowsReadOnlyProcess(runtime_roots=(Path(r"C:\Tools\Python312"),))`
  明确配置。使用可独立运行的精简解释器；依赖创建子进程的 venv 启动器不在当前支持范围内。
  单独的绝对仓库路径参数会映射到副本；嵌入脚本字符串中的原仓库绝对路径不会改写。
  工作区与日志不得位于工具链目录中。
- 执行准备也计入超时；副本上限为 20,000 项、1 GiB。副本不是 P04 的代码快照。
  不适合当前单进程/只读约束的构建和测试仍需在 P09 扩展后端。
- 未绑定 P04 时，Windows Git status/diff 在同一 LPAC 内运行 Dulwich 1.2.14 只读 helper。
  Git for Windows 的路径规范化在该隔离环境内不可用，详见
  [微软项目的问题记录](https://github.com/microsoft/mxc/issues/694)。
  helper 不执行系统 Git，也不放宽全局 ACL；不读取全局配置、运行 hooks 或外部过滤器。
  支持普通索引文件、暂存/未暂存及未跟踪状态、文件差异和 core.autocrlf；
  Git attributes、配置 include/filter、core.filemode=true、冲突、子模块、符号链接、
  特殊索引标志与外部对象库
  暂不支持，遇到这些输入明确失败。输出通过现有脱敏和记录流程。
- API 最低要求 Windows 10 1809；本次实测 Windows 11 build 26200 / Python 3.12.14 /
  NTFS。其他版本需复验；缺少能力时拒绝执行，不回退为普通 subprocess。

P03 测试见 [工具集成与故障测试](tests/test_tools.py)、
[策略与结果约束测试](tests/test_tool_policy.py)、
[Git 范围与脱敏测试](tests/test_tool_security.py) 和
[Windows 真实集成测试](tests/test_windows.py)。
Linux 进程后端尚未实现；当前修改后的 macOS 沙箱及 POSIX 日志集成复验仍待完成，
实际检查结果见 [开发记录](DEV_PLAN.md)。


## P04 工作区与真实快照

- `runtime.workspace.Workspace(source, directory, task.scope)` 以当前源文件建立会话基线。
  `directory` 必须尚不存在、父目录已存在且在源目录之外，也不能位于
  `.agent/.agents/.codex/.git` 下；日志目录与会话目录分开。
- 源仓库保持原样，包括暂存区和未跟踪文件。独立会话 Git 仓库只保存当前基线，
  不复制原分支历史；其 detached Worktree 是后续 Read/Patch/Shell 的工作目录。
- `workspace.revision_reader(plan_version=..., context_revision=...)` 实时计算源代码版本。
  建立 RunSpec 时读取一次，绑定 ToolRuntime 后自动使用同一实际快照读取器。
  WorkflowEngine 也应注入该读取器。准备前源码改变会拒绝旧计划的 prepare。
- 文件身份包括路径、原始字节和 POSIX 执行位；默认排除控制目录、forbidden 和常见
  Python 缓存。其他生成路径在构造 Workspace 时用 `excluded` 明确配置；
  选择规则本身也进入快照身份。`.gitignore` 不会把相关输入排除出证据版本。
- 同一会话中的串行任务可有不同 allowed 写范围，但 forbidden 读取边界必须完全一致。
  写入始终按当前已批准 TaskSpec 检查；reset 后共享该工作区的运行时会跟随新的活动路径。
- 控制器调用六项操作；Agent 的 `runtime.execute()` 无权调用生命周期操作：

```python
from coding_agent.core.workspace import WorkspaceOperation

# runtime 已绑定 workspace、与实际快照对应的 spec、已有计划授权和 journal。
prepared = await runtime.workspace_operation(
    "prepare-1", WorkspaceOperation(operation="prepare")
)
# 检查 prepared.status == "succeeded" 后才能开始工具执行。
saved = await runtime.workspace_operation(
    "snapshot-1", WorkspaceOperation(operation="snapshot")
)
baseline = workspace.baseline
assert baseline is not None

# reset 保留当前树及手工修改，从保存的基线新建一棵树，并更新 runtime.root。
restored = await runtime.workspace_operation(
    "reset-1",
    WorkspaceOperation(
        operation="reset",
        expected_revision=workspace.snapshot().revision,
        target_revision=baseline.revision,
    ),
)
```

- `status/diff` 区分基线与本次变化；文本、二进制、删除/新增和模式变化都可检查。
  `snapshot` 保存受保护的原始内容及清单，展示工件另做脱敏和截断标记。
- `cleanup` 同样要求 `expected_revision`；只移除与基线一致且没有未知文件/目录的
  活动树。之前 reset 保留的树、原始快照和日志继续保留。脏树清理被拒绝。
- 所有副作用先有请求记录；固定 Git 管理命令与退出码也记录。主仓库配置、hooks、
  全局配置及模型给定的 Git 命令不会被执行。任意仓库程序仍使用 P03 进程沙箱。
- 单文件 1 MiB、总内容 64 MiB、20,000 项、清单 8 MiB；超限、读取失败、链接或
  不支持的路径会失败，不能用部分清单宣称成功。现有会话目录不自动接管；
  部分创建或结果丢失后保留现场，跨重启恢复与归档回收属于 P12。
- 当前 Windows 已进行真实 Worktree 与 LPAC 集成验证；macOS/POSIX 实机复验待完成。
  详见 [P04 回归](tests/test_workspace.py) 与 [阶段开发记录](DEV_PLAN.md)。

## P05 模型适配

- `core/provider.py` 定义供应商无关的 Message、ToolSchema、ModelResponse、TokenUsage、
  ProviderError 和 ModelProvider。结构化结果是待处理的数据，不能改变任务状态或生成成功 Evidence。
- `providers.openai.OpenAIProvider` 使用固定版本的官方 SDK（openai 3.14.1），连接
  OpenAI Responses API。模型必须通过 `GenerationSettings(model=...)` 明确指定；
  API key 从 `OPENAI_API_KEY` 或构造参数 `SecretStr` 注入。当前不使用 `OPENAI_BASE_URL`，
  不实现其他 Provider 或兼容服务。通过 `async with OpenAIProvider(settings)` 管理连接。
- 应用层使用 `providers.runtime.ModelRuntime(provider, journal=..., read_revision=..., task_id=...)`，
  调用 `await runtime.generate(messages, tools=..., response_schema=...)`。messages/tools 使用 tuple。
  请求与工具共用 JSONL 顺序和排他调用边界；请求先落盘，失败则不访问 API。
  记录配置、上下文指纹、实际返回模型/响应 ID、用量和脱敏后的可见输出；不保存完整输入或推理续接数据。
  结果写入失败会阻止后续模型及工具调用；待核实请求由 `inspect_journal` 返回。
- 输出 schema 使用拒绝未知字段的 Pydantic 对象模型；返回后再次做严格解析。
  RequirementContract、ReviewResult 已有离线覆盖；PlanDraft 在 P07 定义后接入同一接口。
  未声明的工具、参数错误、重复 call ID、孤立或遗漏的工具结果、拒答和不完整响应均不能当作成功结果。
- `runtime_tools()` 仅暴露 read/search/patch/shell/git 的请求结构。
  控制器用 `tool_request(call, local_request_id)` 转换后交给 ToolRuntime；
  `tool_output(call, request, result)` 将实际结果关联到供应商 call ID。
  模型不持有工具执行器、审批权或工作区生命周期接口，现阶段也没有自动 Agent 循环。
- API 使用 `store=False`，不截断输入，关闭 SDK 自动重试及并行工具调用。
  推理模型的加密续接数据由适配器封装、检查并在下一轮回传，业务代码不解析供应商输出项。
  最多 1,000 条消息、32 个工具定义，上下文消息/响应各限 2 MiB；
  默认输出上限 2,048 tokens、超时 60 秒，可显式配置，最大为 65,536 tokens / 300 秒。
- 错误区分配置、认证、权限、请求、限流、服务、传输、超时、结构错误、拒答和不完整响应。
  retryable 与 retry-after 仅供后续控制器决策，不自动发起重试；记录 API 实际提供的输入、
  输出、缓存及推理 token 数。没有 usage 时保持未知，不编造零消耗或估算费用。
  本地取消会向上传播 CancelledError 并记录 interrupted，不能据此断言服务端没有执行或计费。
- 模型 API 是受信控制器按配置发出的独立网络请求；任务的 `network: false` 仍约束工具沙箱。
  模型不能通过工具任意联网或读取控制器凭据。P06/P08 后续负责选择任务上下文和循环预算。

### 真实 API 冒烟

先在本机环境安全配置 `OPENAI_API_KEY` 和支持 Responses、Structured Outputs、function calling
的 `OPENAI_MODEL`，不把密钥写进源码或命令示例。显式启用：

```powershell
$env:CODING_AGENT_LIVE_SMOKE = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_provider_live.py -q -s -rs
Remove-Item Env:CODING_AGENT_LIVE_SMOKE
```

macOS / Linux 在已配置 key/model 的环境执行：

```bash
CODING_AGENT_LIVE_SMOKE=1 .venv/bin/python -m pytest tests/test_provider_live.py -q -s -rs
```

最多 3 次请求，每次输出上限 1,024 tokens、超时 30 秒且无自动重试；
仅发送固定提示和临时目录中的合成文件内容。检查结构化输出及一次真实工具读取往返，
输出实际模型/用量和临时记录目录。默认测试跳过此项；显式启用但缺少配置会失败。
跳过、超时或输出不完整都不代表真实接入通过。

接口依据：[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、
[Function Calling](https://developers.openai.com/api/docs/guides/function-calling) 和
[Responses Python reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
（2026-09-16 核对；真实服务兼容性仍需上述冒烟确认）。


## 本地 coding 引擎 adapter

工作流、权限、证据和 QualityGate 使用本项目的 Python/Pydantic/asyncio 实现；
编码循环优先复用本地引擎。目前提供 [CodexCoder](src/coding_agent/executors/codex.py)，
实现既有 Coder 接口。Claude Code、Pi 尚未实现，也未引入新的 Agent 框架。

| 路径 | 用途 | 当前验证 |
| --- | --- | --- |
| OpenAIProvider + ModelRuntime | 直接模型推理、结构化输出 | 模拟 HTTP 通过；真实 API 待验证 |
| CodexCoder | 本地 Codex 编码循环 | Windows 真实 CLI + 本地假模型通过；真实账号、macOS 待验证 |
| Claude Code / Pi adapter | 后续替换编码引擎 | 规划中 |

首版只支持原生 Codex CLI **0.154.0-alpha.6.2**；协议使用实验性接口，其他版本拒绝执行。
Windows 需真正的 codex.exe，不能使用 .cmd/.bat 包装器。运行时关闭 Codex 原生环境访问，
通过动态工具将项目读写、命令和 Git 交给 Tool Runtime；模型不能写任务状态或 Evidence。
Windows Job/POSIX 进程组用于管理生命周期，工具沙箱仍由 P03 后端负责。

### 应用内接入

调用方先准备经过批准的 ToolRuntime，再将 adapter 作为 Coder 传给工作流：

```python
from pathlib import Path
from coding_agent.executors.codex import CodexCoder, CodexSettings

coder = CodexCoder(
    CodexSettings(
        executable=Path(native_codex_path).resolve(),
        home=Path(dedicated_codex_home).resolve(),
        model=selected_model,
        timeout_seconds=60,
        max_tool_calls=20,
    ),
    runtime=approved_tool_runtime,
)
```

executable 与 home 都必须在项目和权威日志目录之外，避免被任务工具改写。
home 使用专用目录；首次调用写入固定配置，
遇到其他配置会拒绝覆盖。先在独立终端把 CODEX_HOME 指向这个专用目录，再运行
codex -c cli_auth_credentials_store=file login，确保使用 adapter 对应的文件凭据存储；
登录与刷新由 Codex 管理。不会自动使用或复制日常全局配置、插件和凭据。
遗留的 .verified-runtime.lock 需先核对进程和日志再处理，不能删锁后盲目重跑。

默认每次尝试最多 20 次 runtime 工具操作、60 秒，且受工作流时限约束；
取消后等待进程清理。CLI 内部可能有有限传输重试，一条模型记录不等于一次 HTTP 请求。
此协议未提供可强制的单次输出 token 上限，对应记录为 null；最终累计用量来自 Codex，
中间段与未报告的用量保持未知。OpenAI API 路径仍要求明确输出上限。

### 验证

无需账号的协议/权限测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_codex.py -q
```

安装兼容的原生 CLI 后，测试让真实 Codex 连接本机假模型；未安装 CLI 时只跳过对应集成测试。
这与真实账号调用分开统计。

真实冒烟需先在专用目录完成登录，并安全配置 CODING_AGENT_CODEX_HOME、
CODING_AGENT_CODEX_MODEL，然后显式开启：

```powershell
$env:CODING_AGENT_CODEX_LIVE = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_codex_live.py -q -s -rs
Remove-Item Env:CODING_AGENT_CODEX_LIVE
```

冒烟只操作临时合成文件，最多 4 次 runtime 工具调用、60 秒，没有可承诺的硬 token 上限。
缺少配置时显式失败；默认跳过不计为通过。当前只完成离线验证，P05/P08 均未标记 DONE。

协议依据：[Codex App Server](https://learn.chatgpt.com/docs/app-server)、
[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)；
另核对了本机 CLI 导出的 schema。实施范围和验收记录见 [开发计划](DEV_PLAN.md)。
