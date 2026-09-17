# Verified Coding Agent Runtime

按项目规范执行编码任务，以关联代码快照的证据判断完成状态。

当前实现 P01 领域基础、P02 串行工作流、P03 工具运行时、P04 会话工作区、
P05 模型适配、P06 项目初始化及 P07 计划 CLI。包含 QualityGate、有限修复、受控工具、持久事件、
源码快照、Worktree、OpenAI Responses 适配和有来源的项目知识。
P05–P07 模型行为通过离线模拟测试，另有实验性 CodexCoder 接入本地 Codex 编码循环；
P08 已接入任务上下文、run CLI、独立工作区和 diff/history。
P09 已接入版本化验证 Evidence；真实账号/API 冒烟、P10 审查及最终交付仍待完成；跨平台复验及阶段状态见开发计划。

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

## 使用 .env 配置模型

本地配置为仓库根目录的 .env（Git 已忽略）；.env.example 是可提交模板。
填入 CODING_AGENT_API_KEY 后，在已安装项目虚拟环境的终端运行：

~~~dotenv
CODING_AGENT_PROVIDER=zhipu
CODING_AGENT_API_URL=https://open.bigmodel.cn/api/coding/paas/v4/chat/completions
CODING_AGENT_MODEL=glm-5.3
CODING_AGENT_API_KEY=
~~~

~~~bash
agent init PATH --refresh --env-file .env
agent plan "实现需求" --path PATH --env-file .env
~~~

--env-file 显式启用模型，路径相对于终端当前目录，不向父目录搜索。
无该选项时保留原离线行为；原 --model MODEL + OPENAI_API_KEY 用法仍使用 OpenAI。
优先级：--model > 同名环境变量 > 文件。空环境变量也覆盖文件并导致缺配置错误。
前缀统一为 CODING_AGENT_，旧前缀不再读取；四个键均需提供，可由同名环境变量补齐。
CODING_AGENT_CODEX_* 属于独立配置，dotenv 模型读取器忽略这些键，不自动导入。
支持 UTF-8/BOM、LF/CRLF、空行、整行 # 注释、整值单/双引号；
不执行变量替换、转义、命令或 export，不支持行尾注释。重复键、未知 CODING_AGENT_ 键和缺失项报错。
文件值不写入进程环境，密钥注入现有脱敏器；.env 不进入项目探索和执行快照。

智谱适配仅用于 Explorer/Planner 的只读结构化调用：JSON object 模式配合本地严格 Schema 校验。
超时、截断、无效输出或工具请求均失败；不自动重试、不跟随重定向、不保存原始推理。
复用现有 OpenAI SDK，无新增依赖。agent run 继续使用独立的本地 Codex 配置
CODING_AGENT_CODEX_HOME / CODING_AGENT_CODEX_MODEL。
如需通过文件使用 OpenAI，设置 CODING_AGENT_PROVIDER=openai、
CODING_AGENT_API_URL=https://api.openai.com/v1/responses 以及相应 MODEL/API_KEY。

已核对[智谱 Coding 端点](https://docs.bigmodel.cn/cn/guide/develop/gork)及
[对话补全参数](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)。
glm-5.3 按用户指定保留；尚未验证账号上的模型可用性或执行真实智谱请求。

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
返回状态、知识修订、变化来源、问题与日志位置；P07 首次 plan 和 P08 缺少计划时的 run 已复用。
初始化修订没有计划版本，不能作为任务 VERIFIED 或 P04 全工作区快照使用。

## P07 计划、导入与查看

只生成计划，不执行仓库程序或修改业务代码：

~~~powershell
.\.venv\Scripts\agent.exe plan '抽取库存接口并保持现有行为' --path 'D:\Projects\Example' --model MODEL --focus src/service.py
.\.venv\Scripts\agent.exe plan --path 'D:\Projects\Example' --requirement requirement.json --model MODEL
.\.venv\Scripts\agent.exe plan --path 'D:\Projects\Example' --draft draft.json --refresh
.\.venv\Scripts\agent.exe status --path 'D:\Projects\Example'
.\.venv\Scripts\agent.exe graph --path 'D:\Projects\Example'
~~~

首次 plan 复用初始化；知识过期时需 --refresh，必要问题未解决时返回 blocked。
--model 显式调用一次 OpenAI Planner（需 OPENAI_API_KEY，16,384 输出 tokens、60 秒上限）。
离线 --draft 接收 [PlanDraft](src/coding_agent/core/planning.py) JSON；
--requirement 接收 RequirementContract JSON。输入文件路径相对于目标仓库。
未提供模型或草稿时明确提示缺少输入，不自动编造验收任务。

计划保留原始目标、复杂度的四个维度及源码依据、独立风险、规则 ID、验收/检查 ID、
当前任务依赖和后续里程碑。任务写入范围必须列出具体文件，不能使用通配符；
重叠写入必须有依赖顺序。--allow 可限定提案的最大写入范围，--forbid 禁止读取相关路径，
--mode 选择工作流模式，--max-attempts 限制当前任务的尝试分配；Small 不能降低高风险检查。
现有目标源码尚未读取时保存 blocked 提案，提示通过 --focus 补充探索。
这些结构检查不等于证明模型理解完整或验收充分，提案仍需用户审阅。

产物位于 .agent/：

- plan-<id>-v<N>.json：不可变 PlanVersion，包含需求、知识修订、代码内容清单/指纹、
  设置、草稿和阻塞原因。起始快照覆盖探索范围内未提交、未跟踪及未被 Explorer 摘录的文件。
- 同名 .md：便于审阅的范围、风险、验收、基线检查与待办摘要。
- plan.json：当前计划索引；不是 PlanDraft，也不表示已授权执行。
- plan-<attempt>/events.jsonl：本次规划工具/模型请求和结果，与 init 共用独占锁。
- imported-plan-<digest>.json：导入时保留的原始 PlanVersion。

再次生成同一需求的版本时必须提供 --reason，旧文件保留。--new 建立独立提案历史；
它不实现运行中的重规划或预算重置。当前阶段保守拒绝删除/重写旧验收、改变授权上限、
移除原任务/里程碑或解除没有明确答案的必要问题；P11 再接入带执行证据的变更协调。
大型重构必须声明行为不变量与基线检查；未来任务只留在 pending 里程碑中，不可调度。

使用 --import .agent/plan-<id>-v<N>.json 导入保存的版本文件；它会重查结构、来源、
知识/代码新鲜度及当前设置，保留原始导入记录，并保存新的本地版本。
包含已识别未脱敏凭据的版本会被拒绝导入，原文件保留。
源文件或用户规则变化会让旧计划失效；Git HEAD 相同也不会跳过检查。
导入不接受模型自述的审批，原审批必须匹配新的完整 RunSpec 指纹才适用。

status/graph 只读，不初始化、不调用模型、不生成日志；可加 --json。
它们显示当前提案与阻塞项，执行状态为 not_tracked，不推测任务完成。
proposed 退出 0，blocked/stale/输入错误退出 2；真实模型/平台验证与自动执行仍待后续阶段。

应用入口：application.planning.plan、inspect_plan；run_spec 构造供运行时使用的提案。
RunSpec 绑定整个 PlanVersion 的摘要指纹，并将禁止读取范围传给任务；
WorkflowEngine 仍要求匹配的 PlanApproval。批次结果保留 pending_milestones，
不能据此宣称整个需求、最终集成检查或交付已完成。


## P09 验证与证据查询

run 接入基线、任务检查和最终快照检查。为验证命令提供项目外、由你维护的运行时目录；
重复 --verification-runtime 可添加多个目录，预览与审批执行时参数须相同：

~~~powershell
.\.venv\Scripts\agent.exe run --path 'D:\Projects\Example' --workspace 'D:\AgentWork\example-run' --codex-home 'D:\AgentProfiles\coding-agent' --model MODEL --verification-runtime 'D:\AgentRuntimes\python312'
# 核对预览后，使用同样参数追加 --approve FINGERPRINT
.\.venv\Scripts\agent.exe evidence --path 'D:\Projects\Example'
.\.venv\Scripts\agent.exe evidence --path 'D:\Projects\Example' --session run-PLAN_ID --json
~~~

运行时目录须已有验证工具及依赖；不会自动安装依赖或使用终端的 PATH。
命令名仅在配置目录及其 bin/Scripts 中解析，绝对可执行路径也必须位于这些目录中。
运行时不能与源项目、工作区或 Codex 登录目录重叠。Windows 将运行时复制进 LPAC：
Python 目录需包含可独立运行的 python.exe、DLL、Lib/DLLs 与所需 site-packages；
依赖外部 base Python 的普通 venv 不能视为独立运行时。不提供目录时，验证记录 unavailable。

- 检查只执行已批准计划声明的命令；项目初始化发现的命令仍需在计划中映射到稳定 criterion/check ID。
  每项先探测工具版本，再执行检查；逻辑命令及后端实际启动参数/工作目录、时间、退出码和脱敏输出引用均持久记录。
  探测与检查都消耗原有会话工具预算，不在修复、基线或最终重跑时重置。
- test 类型支持 pytest / python -m pytest，以及 python -m unittest 的标准文本报告。
  零测试为 inconclusive；跳过、预期失败或 deselected 均不能作为完整必需覆盖；
  截断、超时、未知包装器输出也不能通过。其他声明的 lint/static_analysis/build/command
  记录实际退出码；语义覆盖仍由明确验收与后续独立审查约束。
- 重构或显式基线计划先在原始 Worktree 快照运行 baseline_check_ids；
  缺失、失败、不可用或无有效测试均在 Coder 修改前阻塞，保留已有失败。
  任务图通过后，在最终快照保守重跑所有任务和需求级非审查检查。
  最终检查失败时 run 返回 blocked；已记录的任务状态是历史状态，不代表最终交付认证。
- Windows 验证沙箱允许独立临时目录写入和最多 16 个 Job 内进程；
  源码/运行时只读、控制记录不可访问、网络仍禁止。等待全部子进程，超时或取消终止整个 Job；
  单进程内存上限 512 MiB，Job 总计 1 GiB。Coder 原有进程能力保持单进程只读。
  Windows 普通临时文件及继承 ACL 的目录可创建、写入和删除；Python 3.12.4+ 的
  TemporaryDirectory/mkdtemp 使用受保护 ACL，目前与 LPAC 不兼容，可能重试到超时。
  依赖此能力的检查不能通过；保留严格 xfail 能力测试，P09 尚未完成。
  macOS 验证临时目录可写，仍禁止 process-fork；需要子进程的检查尚不支持，实机复验待完成。
  未提供隔离测试数据库；网络、数据库、源码目录构建写入等能力不可用时不放宽权限。
- evidence 只读校验记录工件摘要、原始 ToolRequest/ToolResult 和验收关联，
  显示 baseline/task/final、计划/知识/实际源码修订、历史状态与当前快照是否匹配。
  --json 包含完整检查与输出引用；没有记录不等于通过。修改代码、规则或计划后不会复用旧通过。
  当前实现不认证仓库外部任意人伪造的整套控制历史，也不以 Git HEAD 代替实际源码快照。

P10 审查、P11 跨批次协调及 P12 交付仍待完成；requirement_complete 始终为 false。
Windows 验证使用临时测试项目和离线 Coder；真实账号与 macOS/POSIX 的待验收项见 DEV_PLAN。

### Windows 私有临时目录前置检查

如果计划中的测试或构建依赖 Python 私有临时目录，可把
[单次能力探测模板](examples/python-private-temp-check.json) 纳入首次计划草稿：

1. 把模板作为一项检查加入需求级 acceptance.checks。
2. 把 python-private-temp 加入相关 criterion 的 required_check_ids，保留原有业务检查。
3. 把同一 ID 加入 baseline_check_ids；普通功能开发也支持基线，不必标为 refactor。

模板是单个 AcceptanceCheck，不是可直接传给 --draft 的完整 PlanDraft。
请在规划阶段合入完整草稿并审阅；已有计划的验收变更仍受 P11 未实现的限制，
不能用 --new 绕过正在执行计划的授权或预算。

批准运行后，控制器先在实际验证沙箱探测工具版本，再执行一次 os.mkdir(0o700)，
以及目录内文件读写和清理。直接调用底层 mkdir 可暴露拒绝访问，避免 tempfile 的大量重试。
失败会记录绑定版本的 unavailable 证据，并在调用 Coder 前阻塞；run 的原因包含检查 ID、
状态与原因，可用 agent evidence 查看原始退出码和输出。该探测沿用既有权限与工具预算，
不是权限授予，也不能替代行为测试；最终快照仍需重跑必需检查。

本机 Python 3.12.14 的单次探测实际被 LPAC 拒绝；未声明探测的命令仍可能超时。
这是已复现的 [CPython 上游问题 #134587](https://github.com/python/cpython/issues/134587)，
[修复 PR #148804](https://github.com/python/cpython/pull/148804) 于 2026-09-17 核对时尚未合并。
未安装补丁解释器、改写标准库或放宽沙箱权限。

## P08 执行与记录查看

先通过 agent plan 保存并审阅计划，再使用已登录的专用 Codex 目录。
原生 CLI 版本、独立登录配置和权限边界见下方「本地 coding 引擎 adapter」。

~~~powershell
# 工作区目录必须尚不存在，其父目录须存在，并位于项目之外。
.\.venv\Scripts\agent.exe run --path 'D:\Projects\Example' --workspace 'D:\AgentWork\example-run' --codex-home 'D:\AgentProfiles\coding-agent' --model MODEL

# 核对提案和上述预览，再传入预览给出的精确指纹；其他参数保持一致。
.\.venv\Scripts\agent.exe run --path 'D:\Projects\Example' --workspace 'D:\AgentWork\example-run' --codex-home 'D:\AgentProfiles\coding-agent' --model MODEL --approve FINGERPRINT

.\.venv\Scripts\agent.exe status --path 'D:\Projects\Example'
.\.venv\Scripts\agent.exe diff --path 'D:\Projects\Example'
.\.venv\Scripts\agent.exe history --path 'D:\Projects\Example'
~~~

可用 --codex 指定原生可执行文件；专用目录/模型也可通过
CODING_AGENT_CODEX_HOME、CODING_AGENT_CODEX_MODEL 提供。全部命令支持 --json。
无审批时只做预览，输出完整 RunSpec 和匹配指纹（文本模式显示指纹与预算）；
不启动 Codex 或创建 Worktree。首次 run 缺少知识/计划时复用初始化，再提示先保存计划。
执行指纹绑定完整计划、权限、预算、起始快照、执行器配置及工作区位置；改变这些输入需重新核对。

- [任务上下文](src/coding_agent/context/coder.py) 包含需求、当前任务、计划/知识/代码修订、
  复杂度、全局验收、里程碑、规则 ID、来源与未知问题。原知识摘录与运行时重新读取的源码分开保留；
  不同任务使用新的上下文。每段当前源码最多 32 KiB，整个上下文最多 512 KiB；
  必需上下文超限会阻塞，不静默删除规则。
- 所有项目动作经过 Tool Runtime，进程仍受 P03 后端限制。整次执行默认最多 30 个
  Agent/上下文/验证工具请求及 31 个模型分段；达到工具额度后的拒绝请求仍留痕。
  预算由同一日志派生，不随 Coder 实例/尝试重置；任务尝试次数与工作流总尝试限制同时生效。
  Codex 每次尝试默认另限 20 个工具调用、60 秒。分段数不代表 HTTP 请求数或硬 token 上限。
- 原项目代码保持原样；修改保存在指定目录下的 Worktree，返回实际路径。
  运行时记录修改前后快照、实际改动路径、工具/命令及结果，模型总结独立标为 draft。
  新调用方、共享状态、依赖、范围或验证困难需要结构化 replan 请求，停止当前调度。
- P09 已接入，未配置验证运行时会记录 unavailable 并阻塞；P10 尚未接入，必需审查仍阻塞。
  重构或显式基线计划在基线未通过时于 Coder 修改前阻塞。模型所说的“测试通过”不能将状态变为 VERIFIED。
- 会话记录位于 .agent/run-<plan-id>/，与 init/plan 共用独占锁。同一计划各版本共用一次执行身份；
  已有目录拒绝重跑，不重置预算。不自动恢复、删除修改、回写源项目或提交 Git；
  新建独立提案不代表恢复旧会话，恢复/跨批次协调仍属 P11/P12。
- 每次调用前检查源项目、用户规则和计划；Worktree 内的规范变化也停止后续动作。
  取消保留修改和中断记录；请求有记录而结果缺失时保留锁，先检查真实状态。
- status 默认优先显示当前计划已有执行的记录状态；无执行时仍显示 P07 提案。
  status/diff/history 可用 --session run-<plan-id> 指定旧会话，均只读。
  diff 为最近一次记录的整体 Diff，截断会标明；不包含随后人工编辑的变化。
  不完整日志和缺失 Diff 明确显示，不能由模型总结补成成功。

run 的 approval_required、blocked、stale、replan_required、failed 退出码为 2，
中断为 130；读取完整历史成功为 0，不代表任务成功。所有结果的 requirement_complete 仍为 false。
完整 API 为 application.execution.run；直接调用 CodexCoder 的旧 API 仍可用于底层协议测试，
应用执行路径始终构建并校验 TaskContextPack。

新增 P08 完整应用账号冒烟（默认跳过；只发送临时合成文件，单次 Codex 尝试最多 3 个工具调用、
60 秒，另有两个上下文读取）：

~~~powershell
$env:CODING_AGENT_RUN_LIVE = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_execution_live.py -q -s -rs
Remove-Item Env:CODING_AGENT_RUN_LIVE
~~~

需要前述专用登录及模型配置。断言真实修改、源文件保留、上下文/日志和“缺少证据仍阻塞”；
不将真实编码冒烟等同于 P09 验证通过。

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
  测试/构建须使用上文 P09 验证模式；普通 Coder Shell 保持原有限制，缺失能力不能计为通过。
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
  普通 Coder Shell 保持单进程只读；P09 验证模式的 scratch、子进程和已知限制见上文。
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
  RequirementContract、ReviewResult 和 P07 PlanDraft 已有离线覆盖。
  未声明的工具、参数错误、重复 call ID、孤立或遗漏的工具结果、拒答和不完整响应均不能当作成功结果。
- `runtime_tools()` 仅暴露 read/search/patch/shell/git 的请求结构。
  控制器用 `tool_request(call, local_request_id)` 转换后交给 ToolRuntime；
  `tool_output(call, request, result)` 将实际结果关联到供应商 call ID。
  模型不持有工具执行器、审批权或工作区生命周期接口，API 路径没有自研编码循环；本地编码由下述 Codex adapter 承担。
- API 使用 `store=False`，不截断输入，关闭 SDK 自动重试及并行工具调用。
  推理模型的加密续接数据由适配器封装、检查并在下一轮回传，业务代码不解析供应商输出项。
  最多 1,000 条消息、32 个工具定义，上下文消息/响应各限 2 MiB；
  默认输出上限 2,048 tokens、超时 60 秒，可显式配置，最大为 65,536 tokens / 300 秒。
- 错误区分配置、认证、权限、请求、限流、服务、传输、超时、结构错误、拒答和不完整响应。
  retryable 与 retry-after 仅供后续控制器决策，不自动发起重试；记录 API 实际提供的输入、
  输出、缓存及推理 token 数。没有 usage 时保持未知，不编造零消耗或估算费用。
  本地取消会向上传播 CancelledError 并记录 interrupted，不能据此断言服务端没有执行或计费。
- 模型 API 是受信控制器按配置发出的独立网络请求；任务的 `network: false` 仍约束工具沙箱。
  模型不能通过工具任意联网或读取控制器凭据。P06/P08 已接入项目知识、任务上下文和本次执行的调用预算。

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
