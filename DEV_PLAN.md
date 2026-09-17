# Verified Coding Agent — 开发计划

> 版本：V1 / 2026-09-16 / P08 应用集成已通过 Windows 离线验证，真实冒烟待验证
> 依据：[开发规格](CODING_AGENT_DEVELOPMENT_SPEC.md)，重点参考 §16、§24、§29–31、§35–39、§42、§44–48。
> 当前状态：P01/P02 已完成；P03 安全修复与 Windows 适配已完成本机验证；macOS 集成复验待完成，状态为 IN_PROGRESS。用户明确授权继续 P04；P04 功能与 Windows 验收已完成，macOS 实机复验待完成，保持 IN_PROGRESS；用户明确授权继续 P05，模型适配已完成离线实现，真实 API 冒烟待验证，P05 保持 IN_PROGRESS；用户另行授权本地 coding adapter，P08 已实现 Codex 接入、任务上下文及 run/diff/history，并通过 Windows 离线验证；真实账号/跨平台复验待完成，保持 IN_PROGRESS；用户授权继续 P06，初始化 CLI 与知识刷新已完成 Windows 离线验证，真实模型与 macOS/POSIX 复验待完成，保持 IN_PROGRESS；用户授权继续 P07，Planner、版本化计划和 CLI 已实现并通过 Windows 离线验证，真实模型与 macOS/POSIX 复验待完成，保持 IN_PROGRESS；P09 已获用户授权并开始开发；P10 已接入独立 Reviewer、规则来源及最终快照重审，真实审查质量待验收；P11–P12 尚未开始。

## 1. 目标与推进方式

交付一个可在已有代码库中完成以下流程的本地运行时：

```text
init / 复用项目知识 → 需求与计划 → 按授权执行
→ 验证 → Review / 修复 → 最终门禁 → 交付与结果报告
```

每次执行都能关联项目规则、计划版本、实际动作、代码快照和验收证据；中断后能检查真实状态并继续或交还成果。

- 保留规格的 12 个开发阶段，按依赖顺序推进；每阶段先满足验收条件再集成下阶段。
- 默认首次实现范围为 **P01**。若用户明确授权多个阶段，按授权范围连续推进，无需为每个阶段重复确认。
- 本文件是维护本项目的开发计划；产品内部的可执行 TaskGraph 在 P07 落地。
- 首批端到端样本选择已有 pytest 测试的 Python 仓库，先证明闭环。其他语言的识别结果与实际验证支持范围分别报告。
- P01 首个宿主验证环境为 Windows / PowerShell；P02/P03 在 macOS 验证。
  P03 增加 Windows 原生文件/日志与 LPAC 后端；Linux 进程后端未实现，不回退为无隔离执行。
- 不安排未经验证的日历工期；通过阶段交付物和检查结果衡量进度。

## 2. 范围与实现默认值

| 项目 | 开发约定 |
| --- | --- |
| 技术栈 | Python 3.12+、Pydantic v2、asyncio、Git CLI、subprocess；Windows 只读 Git 使用 Dulwich；P06 CLI 使用标准库 argparse；检查使用 pytest、ruff、mypy |
| 源码布局 | 使用 `src/coding_agent/` 包；在包内保持规格要求的 core、agents、runtime、tools、verification、context、session 等职责边界 |
| 建目录方式 | 当前阶段需要时再建立文件和目录，不预建规格 §33 的全部目录树 |
| 执行方式 | V1 同时运行一个业务任务；不实现并行调度或远程 Worker |
| 模型 | 默认 OpenAI；用户追加智谱只读 API 配置（§8.11）；本地 coding 引擎通过 Coder adapter 接入，首个为 Codex；Claude Code/Pi 后续评估。离线验证不依赖账号 |
| 持久化 | 先使用规格中的 JSON 状态快照、版本化计划、JSONL 事件/证据及工件目录；有明确需求时再引入 SQLite，避免两份可写状态真相 |
| 验证策略 | 先支持显式配置和已识别的仓库命令；自动发现不能把未执行命令当作通过 |
| 复杂度 | 分别记录 small/medium/large 与风险等级；通过 Explorer/Planner 评估范围、耦合、未知项和验证难度，决定单任务、任务图或分阶段执行 |
| 质量承诺 | `VERIFIED` 必须有适用于当前计划及代码快照的必需证据；实现结束和交付成功分别记录 |

不实现 Web UI、多用户、复杂 RAG、AST/LSP 图谱、Agent 团队、分布式调度、自动 PR 或复杂 MCP 生态。执行后端集成和竞品评测不构成重建这些产品的要求。

大型任务先验证单仓库、多模块、具有回归测试的重构。以明确的行为不变量作为验收条件，建立基线，再执行有界批次与最终集成验证。后续里程碑作为版本化计划中的待完成范围保留；当前批次完成不能代表整个需求完成。复杂度元数据在 P07 加入计划，不提前扩充 P01 或新增独立分类 Agent。

## 3. 开工前需要统一的规格事项

以下保留统一事项的原问题、结论与后续待办，不静默覆盖原规格。D01、D02 已在 P01 解决，D03 已在 P02 解决，D05 已在 P03 解决，D04 在本次 P04 统一；其他事项在对应阶段先记录结论并同步冲突章节，再实现相关模型或行为。能由现有需求确定的实现选择自行处理；只有影响用户目标、授权或交付范围的未决问题才需要用户回答。

| 编号 | 最迟阶段 | 现有问题 | 建议与完成条件 |
| --- | --- | --- | --- |
| D01 | P01 / 已解决 | §10 将 Tests/Review 画为后续任务，§11 又要求每任务验证后才能解锁下游 | 已同步 §10–11：业务任务包含实现、必需测试和适用审查；交付前另做全局集成验证。仅 VERIFIED 依赖解锁下游；图不持有可写运行状态 |
| D02 | P01 / 已解决 | §7、§9、§29 的 Risk 示例结构不同；AcceptanceSpec 缺少统一模型 | 已同步 §7、§9.1、§23–24、§29.1：统一 RiskProfile，总等级覆盖已评估维度；AcceptanceSpec 使用稳定 criterion/check ID；Evidence 显式记录来源、结果、时间和计划/上下文/工作区版本 |
| D03 | P02 / 已解决 | FAST 的精简流程与总体 Review、§39 两次审批要求关系不清 | 已同步 §11–12、§25–28、§31、§37、§39：所有模式保留声明检查及计划/交付授权边界；FAST 仅低风险且未声明审查时省略 Reviewer，仍经过 REVIEWING 和门禁；中/高风险分别至少 STANDARD/STRICT；完整 RunSpec 指纹匹配的授权在任务/修复间复用，交付授权单独处理 |
| D04 | P04 / 已解决 | §18、§37 Phase 4、§49 对 Worktree 所属版本有不同表述 | 已统一 §18–19、§20、§49：V1/P04 纳入串行会话 Worktree；主仓库只读，会话独立 Git 基线包含当前未提交/未跟踪输入；重置创建新树并保留旧树，清理拒绝未知改动；并行、交付合并及重启协调保持后续范围 |
| D05 | P03 / 已解决 | `network: false`、受限 shell 和控制记录保护缺少具体执行边界 | 已同步规格 §20–21.1、§30–31：原生文件工具使用 POSIX 目录描述符或 Windows 无跟随句柄；进程使用 macOS 只读沙箱或 Windows LPAC/Job 及受控执行副本，写入通过 Patch；不支持的权限/平台明确拒绝。数据库权限按资源访问定义，测试数据库与需写入/子进程的验证后端仍是 P09 前置条件；不能用命令前缀或 Worktree 声称隔离 |
| D06 | P05 / 接口选择已确定，真实验证待完成 | 尚未选择真实 Provider 与可用凭据 | 保留 OpenAI Responses API 与官方 Python SDK。用户追加本地 coding 引擎需求：Codex adapter 复用现有 Coder 协议，Claude Code/Pi 后续接入；工作流、权限和证据仍由本项目控制。用户追加 .env 与智谱只读适配（§8.11）；API 与本地账号真实冒烟分别验收 |

## 4. 阶段总览

状态仅使用 `NOT_STARTED`、`IN_PROGRESS`、`BLOCKED`、`DONE`。P01/P02 已完成；P03/P04 保留 macOS 实机复验待办，状态为 IN_PROGRESS；`DONE` 必须附交付物与检查证据，见 §8 的阶段验收记录。

| 阶段 | 依赖 | 主要交付 | 状态 |
| --- | --- | --- | --- |
| P01 领域基础 | 无 | 领域模型、DAG 校验、状态转换约束、基础工程配置 | DONE |
| P02 工作流引擎 | P01 | 单任务调度、状态机、QualityGate、Fake 完整生命周期 | DONE |
| P03 工具与执行记录 | P02 | PolicyEngine、Tool Runtime、持久事件、工件记录 | IN_PROGRESS |
| P04 工作区 | P03 | Git 状态/差异/快照、Worktree、受控恢复与清理 | IN_PROGRESS |
| P05 模型适配 | P04 | Provider 接口与一个真实实现 | IN_PROGRESS |
| P06 Explorer / init | P05 | 项目梳理、用户规范、项目知识与增量刷新、初始化 CLI | IN_PROGRESS |
| P07 Planner | P06 | 复杂度评估、PlanDraft 校验/编译、版本化分阶段计划、规则引用、规划 CLI | IN_PROGRESS |
| P08 Coder | P07 | Codex adapter、版本化上下文、受约束执行与 run/diff/history | IN_PROGRESS |
| P09 Verification | P08 | 测试/构建/静态检查、结果解析、版本化 Evidence | IN_PROGRESS |
| P10 Reviewer | P09 | 独立上下文审查、结构化问题、规范与文档检查 | IN_PROGRESS |
| P11 修复与重规划 | P10 | 有界修复循环、复杂度重评估、计划变更和证据失效处理 | NOT_STARTED |
| P12 恢复与 V1 验收 | P11 | Checkpoint/Resume、交付、结果报告、异常场景及对照评测 | NOT_STARTED |

## 5. 分阶段任务与验收

### P01 — 领域基础

- [x] 处理 D01、D02，明确业务任务粒度、Risk、Acceptance 和 Evidence 的一致模型。
- [x] 建立最小 Python 包、`pyproject.toml`、开发依赖与 pytest/ruff/mypy 配置。
- [x] 实现 RequirementContract、RiskProfile、TaskSpec、TaskState、TaskGraph、AcceptanceSpec、ScopePolicy、PermissionPolicy、Evidence。
- [x] 实现重复 ID、缺失依赖、环检测、就绪任务计算和显式状态转换校验。
- [x] 校验 criterion ID、Evidence 状态及计划/上下文/工作区版本字段；来源和时间等运行结果不由模型虚构默认值。

**验收：** 测试覆盖空图、独立任务、菱形依赖、缺失依赖、自环/多节点环、未 VERIFIED 依赖、非法状态转换及无效 Evidence；pytest、ruff、mypy 通过。领域代码不导入 LLM、CLI、subprocess 或工作区执行实现。

**范围：** 不实现 Agent、CLI、调度执行或真实工具调用。

### P02 — 工作流引擎

- [x] 处理 D03，形成明确的完成、验证、审批和重试策略表。
- [x] 实现 TaskScheduler、StateMachine、WorkflowEngine、QualityGate；并发固定为 1。
- [x] 接入 FakeCoder、FakeVerifier、FakeReviewer，覆盖成功、验证失败、审查修复、阻塞和取消。
- [x] 定义运行事件及 Fake writer，关联任务、计划版本与状态变更。

**验收：** Agent 无法自行写入 VERIFIED；缺失、非通过、过期证据均不能过门禁；前置任务失败时下游不运行；失败循环有终止路径；生命周期事件顺序可核查。

### P03 — 工具运行时、权限与持久留痕

- [x] 处理 D05；实现 ToolRequest、ToolResult、PolicyEngine 的 ALLOW/ASK/DENY 行为。
- [x] 实现当前需要的 read/search/patch/shell/git 工具及受控进程执行，限定路径、工作目录、超时和输出大小。
- [x] 实现单会话 JSONL writer；动作前落盘请求，动作后记录真实结果、关联 ID、退出码或错误及工件引用。
- [x] 控制计划、事件、证据、状态等记录的写入归属，覆盖 shell 间接修改路径；日志中处理凭据和输出截断。
- [x] 为请求有记录、结果缺失的情况保留“待核实”语义；记录失败时停止新副作用操作。
- [x] 修复 Git diff 的 forbidden 索引内容泄露及已知多行凭据脱敏遗漏，补充跨平台和真实后端回归用例。
- [x] 完成 Windows 原生文件/日志、LPAC 进程与隔离 Git helper 的真实验收及完整检查。
- [ ] 在 macOS 重跑本次安全修复后的完整检查及真实沙箱/POSIX 日志集成测试，完成 P03 复验。

**验收：** 在临时目录测试越界路径、链接越界、拒绝/待审批请求、超时和写日志失败；拒绝的动作没有执行副作用。不能强制的权限限制必须显式拒绝。真实 Coder 启用前完成事件持久化。

### P04 — 工作区与代码快照

- [x] 处理 D04，明确主仓库、会话工作区和 Worktree 的使用及清理规则。
- [x] 实现 prepare/status/diff/snapshot/reset/cleanup；区分用户原有修改与本次任务改动。
- [x] 快照包含相关未提交和未跟踪输入；排除执行日志及验证生成物造成的自我失效。
- [x] 使用临时 Git 仓库验证 Worktree 和恢复；文件删除或重置前核实目标与归属。

**验收：** 快照能发现实际源代码变化；失败、恢复、清理不丢失用户修改；保留可检查的差异工件。Worktree 只代表代码工作区隔离，不等同于进程或网络隔离。

### P05 — 模型适配

- [x] 处理 D06，实现 ModelProvider 接口与 OpenAI Responses 适配；真实服务验收单独保留待办。
- [x] 支持结构化输出、工具请求/响应关联、超时、错误分类和可用的用量数据。
- [x] 使用 Fake/模拟响应验证无效结构、工具调用参数错误、限流、超时和中断。
- [ ] 有可用凭据时执行一次有界真实冒烟，记录模型与配置；将其与离线测试结果分开报告。

**阶段边界统一：** 规格 Phase 5 原列出 PlanDraft，但具体模型属于 P07；本阶段验证通用 schema 接口和已有 RequirementContract/ReviewResult，P07 接入实际 PlanDraft，不提前实现 Planner。

**验收：** 无效模型结果不能直接进入工作流状态或绕过工具权限；业务层不依赖某个 Provider 的原始响应结构。真实接入未验证时不能宣称该交付已完成。

### P06 — Explorer 与项目初始化

- [x] 保持 Explorer 只读；输出 RepoSummary、重要模块/调用关系、验证命令及来源。
- [x] 针对当前任务补充范围、耦合、关键未知项和验证环境信息，为复杂度评估提供源码依据。
- [x] 区分代码事实、明确规则和未确认假设，收集用户补充的开发约定和模块交互约束。
- [x] 控制器保存 `.agent/project.md` 与修订元数据；复用已有 AGENTS.md 等资料。
- [x] 提供 `agent init`、`agent init --refresh` 和首次 plan/run 共用的初始化流程。
- [x] 刷新变动来源，保留用户内容；非交互场景遇到必要决策时返回可操作的说明。
- [ ] 用真实模型验证代表性项目的模块解释、冲突/问题和来源质量。
- [ ] 在 macOS/POSIX 实机复验初始化的文件、锁和发布故障路径。

**验收：** 能解释代表性模块关系并指向源码；重复 init 不覆盖用户规则；来源变化可检测；未执行的验证命令没有“通过”记录；无需完整调用图即可完成初始化。

**当前边界：** 离线模式提供有来源的代表性观察；可选 OpenAI 或显式 .env 智谱模型解释模块职责/关系，
其行为通过 Fake 与 SDK mock 验证。首次 plan/run 共用 API 已提供；P07 已接入 plan，P08 已接入 run 的缺少计划初始化路径。
真实模型探索与 macOS/POSIX 初始化尚待实机验证，P06 保持 IN_PROGRESS。

### P07 — Planner 与可执行计划

- [x] 编译 RequirementContract + RepoSummary + 项目知识为 PlanDraft，再校验为 TaskGraph。
- [x] 在计划中记录 complexity、reasons、unknowns、execution_strategy 及支持来源，复杂度与 RiskProfile 分开处理。
- [x] 为 Large 任务保留整体里程碑及待完成范围，详细编译当前批次；每个业务任务包含自己的验收，后续不确定任务不能直接调度。
- [x] 校验依赖、任务范围、验收完整性与规则引用；启发式判断和确定性结构校验分别处理。
- [x] 保存计划版本、稳定任务/criterion ID、起始工作区和上下文修订；显示可读范围与验证摘要。
- [x] 提供 `agent plan`、保存计划导入校验，以及 graph/status 的当前阶段能力。
- [ ] 用真实模型在代表性项目上验证复杂度判断、需求/规则覆盖与计划可执行性。
- [ ] 在 macOS/POSIX 实机复验计划文件、锁、导入与发布故障路径。

**验收：** 非法图不能进入执行；计划先保存再执行；旧计划不被覆盖；导入计划检查来源变更与适用授权。缺少必需信息时产生明确待决项。覆盖低复杂度/高风险组合、未知依赖与分批计划；不得因 Small 降低必要验证，也不得因当前批次全部 VERIFIED 就忽略后续必需范围。

### P08 — Coder 与首次真实修改

- [x] 用户追加授权：实现 CodexCoder，复用既有 Coder/CoderResult 和 Tool Runtime；完成 Fake 与真实 CLI + 本地假模型验证。
- [x] 集成 P06/P07 上下文及应用流程，复用外部引擎的编码循环，统一执行预算；确有需求时才实现直接 API 编码循环。
- [ ] 使用专用 Codex 登录目录完成真实账号冒烟；之后评估 Claude Code、Pi 的同等权限与留痕接入。
- [x] 使用当前任务、计划和知识修订构建上下文；全部动作经过 Tool Runtime。
- [x] 返回 ImplementationResult 或显式 replan 请求；真实文件列表和命令记录由运行时产生。
- [x] 发现额外调用方、共享状态或范围扩大时提交复杂度重评估请求，不等连续失败后才纠正计划。
- [x] 提供 `agent run` 与 diff/history 的可用进度查看；在受控样本仓库完成一次实际修改。

- [ ] 在 macOS/POSIX 实机复验应用运行、上下文、锁和中断留痕。

**阶段边界：** P08 已复用 P06/P07 知识与计划并完成应用接线。真实账号/跨平台复验仍待完成；
P09 已接入必需检查与基线；未配置验证环境或必需 P10 审查不可用时仍阻塞。重构在基线未通过时于修改前阻塞。
本次执行预算由日志派生；跨重规划/恢复的累计预算协调仍属 P11/P12，P08 不实现恢复或自动交付。

**验收：** 所有动作能关联任务和计划；模型自述与真实结果不符时采用真实记录；达到边界时停止调度并保留工件。此阶段尚不能把缺少后续验证/审查的任务标为 VERIFIED。

### P09 — Verification 与 Evidence

- [ ] 核实验证命令所需的写入、子进程与测试数据库能力；扩展并验证 P03 后端，能力不可用时阻塞，不能跳过必需检查或静默放宽权限。
- [x] 执行适用的 test/lint/type-check/build 命令，记录工作目录、退出码、工具版本和输出引用。
- [x] 映射到稳定 criterion/check ID，区分 passed、failed、skipped、unavailable、inconclusive。
- [x] 绑定计划、上下文和实际代码快照；实现必需证据缺失及过期检查。
- [x] 提供 `agent evidence`；初版用保守重跑完成最终快照的验证。
- [x] 为重构在结构性实现修改前执行基线检查，记录已有失败与行为不变量；每批检查后保留最终集成验证要求。

**能力边界：** Windows 普通 scratch 文件/目录及 Job 子进程已实测；Python 私有临时目录存在已复现的 LPAC 兼容失败（严格 xfail，见 §8.12）；macOS 子进程、测试数据库及原地构建写入仍未支持，第一项保持未完成。缺失能力明确阻塞。

**验收：** 覆盖无测试收集、跳过、环境不可用、失败与验证后代码变化；它们不能被错误计作通过。检查从哪版代码得到、覆盖哪个条件均可查询。

### P10 — 独立 Reviewer

已接入固定 OCR 规则来源、独立模型审查与 run 调度（§8.13–8.14）。以下实现已通过离线验证，真实模型质量与 macOS 原生验收仍待完成，保持 IN_PROGRESS。

- [x] Reviewer 使用需求、任务、Diff、相关源码、规则和证据，不接收完整 Coder 对话。
- [x] 输出结构化 blocking/major/minor 问题及结论，并绑定计划/代码版本。
- [x] 检查规则遵循、需求遗漏和文档维护需求；区分审查判断与真实测试结果。
- [x] 审查重构是否保持约定接口、结果、错误语义和相关副作用，不能仅以文件移动完成或新增测试通过判断成功。

**验收：** 必需审查缺失、无效或有阻塞问题时门禁拒绝；正常 Review 可追溯来源；审查报告不能生成虚假的测试成功记录。

### P11 — 修复与重规划

- [ ] 按验证/审查结果执行有限次数修复，记录失败原因和每次尝试。
- [ ] 区分代码错误、已有失败、环境问题和无法判断的故障，避免无依据地修改无关代码。
- [ ] 重规划保留原需求、历史任务、计划变更原因和适用授权；代码变化使相关旧证据失效。
- [ ] 实现整个 Session 的预算/重规划上限，避免通过新建任务重置重试预算。
- [ ] 通过既有重规划流程更新复杂度与执行策略，记录旧/新分类、理由及验证/预算影响；跨批次保留原需求和不变量。

**验收：** 修复后重新验证/审查；预算耗尽产生可恢复的结果；不得通过删验收条件、改规则或无限换计划获得通过。

### P12 — 恢复、交付与 V1 验收

- [ ] 原子保存状态与 Checkpoint，记录最后纳入的事件序号和各版本引用。
- [ ] 实现 `agent sessions`、`agent resume`、`agent result`，完善 status/graph/diff/history/evidence。
- [ ] 核对不确定工具结果与用户手动修改，保留部分成果；处理中断与不完整 JSONL 尾记录。
- [ ] 在适用授权下创建 Git commit；核对最终提交内容与被验证快照一致，记录实际交付结果。
- [ ] 输出需求级结果、未完成项、文档更新、证据、用量和恢复说明。
- [ ] 运行规格 §42.2 的行为场景，完成 §48 的端到端验收，记录首批对照实验。
- [ ] 加入一个单仓库多模块重构案例：基线、分批迁移、复杂度升级、待完成里程碑保留和最终行为回归验证。

**验收：** 修改已落地但结果未记下、用户手改工作区、缺失/过期证据、重规划和规则冲突等情况均有明确行为；不会盲目重复副作用或丢失用户成果。未实测的 Provider、平台或竞品表现必须标为未验证。

## 6. 里程碑与评测节奏

| 里程碑 | 阶段 | 可检查的结果 |
| --- | --- | --- |
| M1 确定性执行基础 | P01–P04 | Fake 生命周期、持久动作记录、受控工作区和证据门禁测试成立 |
| M2 项目理解与计划 | P05–P07 | 用户可 init、补充规范、查看引用规范的版本化计划 |
| M3 实现与验证闭环 | P08–P11 | 样本任务完成真实修改、验证、审查和有界修复 |
| M4 可恢复交付 | P12 | 用户能恢复中断任务，获得与最终代码对应的验收报告 |

- P02 开始使用固定失败场景验证门禁；P04 加入临时 Git 仓库与副作用场景。
- P06 加入规则覆盖、初始化刷新和来源过期样本；P08 起保存真实执行样本。
- P09–P12 固定独立验收条件，保留成功、失败、中断和误报完成的案例到 `evals/`。
- 增加低复杂度高风险、局部任务升级及分批重构样本；按初始/最终复杂度和风险分别报告结果，记录基线与行为保持检查。
- 对照至少覆盖直接 Agent/简单循环以及配置明确的 Spec Kit、GSD 工作流；完整实验前固定版本、模型、权限、预算和运行环境。
- 记录独立验收成功率、错误完成率、用户复核/恢复时间、初始化开销、耗时和费用；按规格 §42 使用可比较设置并报告限制。
- 本阶段验收通过与优于竞品是不同结论；比较尚未执行时报告“未测”，不填写推测胜率。

## 7. 开发验证约定

P01 已建立依赖和检查配置。在项目虚拟环境中使用以下基础检查；之后每个阶段仍需记录自己的实际执行结果：

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy src/coding_agent
```

先运行受影响的测试，再执行阶段要求的完整检查；若检查失败，定位原因后再扩大范围。新建领域逻辑、状态/权限/门禁/恢复路径必须有有意义的测试，文档或简单低影响修改不机械增加测试。

所有 Git/进程/文件测试在临时仓库或目录中执行，不写全局 Git 配置，不使用用户真实项目作为破坏性故障样本。真实模型冒烟与不依赖网络的测试分开记录。

## 8. 进度与开发留痕

每次实现任务开始时，将当前阶段改为 `IN_PROGRESS`，写明本次范围；完成后更新清单和记录。检查未通过时保持未完成，并记录下一步。不要用本文件手工伪造产品的事件或 Evidence。

开发记录格式：

| 日期 / 阶段 | 本次范围与实际交付 | 检查及结果 | 未决项 / 下一步 | 代码或工件引用 |
| --- | --- | --- | --- | --- |
| 2026-09-15 / 规划 | 建立 DEV_PLAN.md 与 AGENTS.md；实现尚未开始 | 相对链接、12 阶段顺序/依赖、代码块校验通过；产品检查未运行 | 下一个实现范围为 P01，先统一 D01/D02 | 本文件与 AGENTS.md |
| 2026-09-15 / 复杂度设计 | 同步复杂度分级、动态重评估与大型重构要求；实现阶段仍未开始 | 三份文档链接/结构、12 阶段依赖及 Diff 格式校验通过；产品检查未运行 | P06 提供依据、P07 评估/分批计划、P11 重评估，P09/P12 验证重构行为 | 规格 §29、§42；本文件；AGENTS.md |
| 2026-09-15 / P01 开始 | 核实目录仅有三份文档，无 Git 仓库及已有产品代码；范围为 D01/D02、领域模型、图与状态校验和基础检查配置 | 已找到本机 Python 3.12.14；测试工具尚未配置，产品检查未运行 | 建立项目虚拟环境并完成 P01 验收；不推进 P02 | 规格 §7–11、§24、§29.1、§46–47 |
| 2026-09-15 / P02 开始 | 用户授权 P02；初始工作区干净，核对现有领域模型与 D03；实施模式策略、串行工作流、证据门禁、Fake 组件与事件 | 当前宿主 macOS，已有 Python 3.12.10；P02 检查尚未运行。uv 清单读取被沙箱阻止，经审批后读取成功 | 完成离线生命周期和故障验证；不推进 P03 或真实模型接入 | 规格 §11–12、§24–28、§31、§39；本文件 |
| 2026-09-15 / P03 开始 | 用户授权 P03；基线为 aef0b15，初始工作区干净；核对 Tool Runtime、权限边界与共享事件序列 | macOS 可调用 sandbox-exec，最小拒绝默认权限的 echo 探针成功；完整隔离能力尚未验证 | 先解决 D05，再实现工具、持久事件及故障测试；不推进 P04 | 规格 §20–21、§30–31；本文件 |
| 2026-09-16 / P03 安全修复 | 用户授权修复审查中的两项 P1；开工基线 f858a2f、工作区干净；复用路径策略过滤 Git 索引清单，扩展多行秘密脱敏，补充回归测试；不增加依赖 | Windows 项目虚拟环境；受影响检查及完整检查见下方本次记录 | 真实 macOS 沙箱/POSIX 工件集成复验待完成，P03 保持 IN_PROGRESS；不推进 P04 | ToolRuntime、Sanitizer、tests/test_tool_security.py、tests/test_tools.py |
| 2026-09-16 / P03 Windows 适配 | 用户明确授权“适配”；保留安全修复 Diff，扩展原生文件/日志与真正隔离的 Windows 执行；风险 high、复杂度 large（Win32 ABI、ACL、进程生命周期和 Git 平台差异耦合） | 发现原生 Git 无法在 AppContainer 内完成路径规范化；增加 Windows-only Dulwich 依赖并同步 D05，不放宽全局 ACL 或隔离 | 真实 Windows 边界、超时/取消及完整检查；保留 macOS 复验待办，不推进 P04 | runtime Windows 后端、core/paths.py、tests/test_windows.py、规格 §20–21.1 |
| 2026-09-16 / P04 开始 | 用户明确指令“continue P04”；基线 4ad2458、工作区干净。按用户指令推进，保留 P03 的 macOS 复验待办；范围仅 D04、工作区、快照、恢复及工具集成。复杂度 large、风险 high：文件身份、日志耐久性、Git 管理与跨平台执行边界耦合 | 首批 P04 及故障测试、Windows Worktree/LPAC 集成已运行；完整结果见下方本次记录 | 不新增依赖；不推进 Provider、CLI、P09 验证执行或 P12 自动重启恢复 | core/workspace.py、runtime/workspace.py、runtime/_snapshots.py、tools/runtime.py、tests/test_workspace.py |
| 2026-09-16 / P05 开始 | 用户明确授权 continue P05；基线 e38a5ff、工作区干净。保留 P03/P04 macOS 待复验状态；本次仅供应商无关接口、一个真实 Provider、模型调用留痕及离线/有界真实冒烟 | 已核对 OpenAI 官方 Responses、Structured Outputs、Function Calling 文档；当前无 OpenAI/Anthropic API 凭据，未调用真实模型 | 复杂度 large、风险 high：异步请求取消、结构化数据/工具关联及共享日志边界耦合。引入官方 openai 3.14.1 SDK，避免自写网络客户端；不推进 P06–P12，不引入多 Provider 或 Agent 循环 | 规格 §32、Phase 5；本文件 |

### P01 验收记录（2026-09-15）

**实际交付：** [工程配置](pyproject.toml)、[开发说明](README.md)、
[领域模型](src/coding_agent/core/models.py)、[TaskGraph](src/coding_agent/core/graph.py)、
[状态约束](src/coding_agent/core/state.py) 及 [公开导出](src/coding_agent/core/__init__.py)。

**实现决策：**

- 统一 RiskProfile 的总等级与四个可选风险维度；未评估维度保持 `None`。
- 每个验收条件引用明确的必需 check ID；校验重复、缺失及无所属条件的检查。
- Evidence 的 `passed` 从状态派生；来源、带时区时间和三个版本字段无自动默认值。
- 使用冻结 Pydantic v2 模型与 tuple；图只保存任务定义，新增任务/依赖返回新图。
  状态快照由调用方提供，状态校验不执行调度或门禁。§10 原 `mark_*` API 已统一为 P02 引擎职责。
- DAG 环检测复用标准库 `graphlib`；祖先/后代查询使用迭代遍历。

**验证环境：** Windows / PowerShell，项目 `.venv`，Python 3.12.14、
Pydantic 2.13.5、pytest 9.1.1、Ruff 0.16.7、mypy 1.20.2。
项目以 `pip install -e '.[dev]'` 安装成功。

| 检查 | 实际结果 |
| --- | --- |
| `python -m pytest` | 262 passed；图 38、领域模型 121、状态 103 |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 9 files already formatted |
| `python -m mypy src/coding_agent` | Success: no issues found in 5 source files |
| `python -m pip check` | No broken requirements found |
| 规格与实现核对 | 13 × 13 个状态对与 §11 转换表一致；领域导入无 LLM、CLI、subprocess 或工作区实现；核心模型 JSON Schema 可生成 |
| 文档与原有文件 | Markdown 链接/代码块结构通过；原 AGENTS.md 字节保持不变；规格按开工备份审查差异 |

测试分别位于 [图测试](tests/test_graph.py)、[模型测试](tests/test_models.py)、
[状态测试](tests/test_state.py)。除阶段必需场景外，覆盖 1100 节点依赖链、
不可变更新失败后原图保留、完整状态快照、权限声明、Evidence 关联以及 JSON 往返。

**过程中的失败及处理：** 普通沙箱初始化报 `setup refresh had errors`，
首轮依赖安装停滞后中止，改用经执行审批放行的命令在项目虚拟环境完成安装与检查。
首轮 Ruff 报 B905，测试改用标准库 `pairwise` 后通过。新版 Ruff 自动格式化
Markdown Python 示例；已还原无关章节，配置排除 Markdown，文档单独检查。
这些是开发环境/本次 lint 问题，未弱化领域验收。

**未实现/未验证：** P02–P12；权限实际强制、来源真实性、快照新鲜度和
QualityGate 不是 P01 结构校验的能力。真实 Provider 与其他宿主平台未验证。
开工时无 Git 仓库；复查已出现 Git 元数据，开发验收完成时 main 尚无提交。只读审查使用
命令级 `safe.directory`，未改全局配置；本记录描述提交前的验收状态。

代码修改按逻辑单元形成可审查 Diff。在已授权范围内推进实现和验证；提交与外部发布按当前授权处理，不把“写开发计划”解释为立即实现或提交全部阶段。

### P02 验收记录（2026-09-15）

**实际交付：** [工作流契约](src/coding_agent/core/workflow/contracts.py)、
[模式策略](src/coding_agent/core/workflow/policy.py)、
[调度与状态前置条件](src/coding_agent/core/workflow/scheduling.py)、
[QualityGate](src/coding_agent/core/workflow/gate.py)、
[WorkflowEngine](src/coding_agent/core/workflow/engine.py)、
[事件契约](src/coding_agent/core/workflow/events.py)、
[公开导出](src/coding_agent/core/workflow/__init__.py)、[Fake 组件](src/coding_agent/testing.py)。
更新 README 与规格中的完成、审批和执行行为；未增加依赖。

**实现决策与 D03：**

- RunSpec 是本阶段的不可变执行输入，包含图、起始版本、模式和限制；不提前实现 P07
  PlanDraft 或版本化计划持久化。PlanApproval 绑定整个 RunSpec 指纹及会话并记录来源/时间。
- 默认 STANDARD；中风险不能使用 FAST，高风险至少 STRICT。FAST 不删除必需检查；
  不需要 Reviewer 时在 REVIEWING 下记录适用性判断，再执行门禁。所有模式保留原规格的
  执行/交付授权边界；已适用授权不重复询问，执行授权不能充当提交授权。
- WorkflowEngine 独占状态写入，TaskScheduler/StateMachine 只做查询/校验；引擎单次运行，
  不提供重置或恢复入口。首个非 VERIFIED 任务停止本次调度，未开始任务被阻塞或取消。
- 必需非审查检查须逐 criterion/check 配对，至少一个；旧证据、不匹配、重复、非通过和
  缺失记录不能过门禁。审查必须匹配任务与三个版本，blocking/major 阻止完成，minor 保留。
  声明的审查检查由控制器据 ReviewResult 生成 REVIEW Evidence；不支持带命令的审查检查，
  不会伪造其执行。Verifier 不能提供 REVIEW 来替代检查，Reviewer 不能提供测试结果。
- 任务所有 Coder 调用共用 max_attempts（默认 3），审查修复另受 max_review_fixes（默认 2）
  限制；RunSpec.max_total_attempts（默认 30）跨任务计数。只有明确失败触发代码修复；
  缺失/无效/不可用/不确定结果阻塞，版本变更或预算耗尽要求重规划。适配器默认 60 秒
  协作式超时。P11 再增加跨计划/批次的持久预算、诊断和重规划。
- 每次派发前和状态修改前写入事件，writer 失败立即抛 EventWriteError 并停止；外部取消
  记录中断结果后重新抛出 CancelledError。计数、历史失败与已知状态可检查，不自动重跑。
- tasks_verified 只表示图中任务在各自快照上的门禁通过（空图不派发任务）。它不表示整个
  需求或最终代码通过；后续任务可能使早期证据过期，最终集成/交付仍由后续阶段完成。

**验证环境：** macOS / zsh，项目 `.venv`，Python 3.12.10、Pydantic 2.13.5、
pytest 9.1.1、Ruff 0.16.7、mypy 1.20.2；`pip install -e '.[dev]'` 成功。
本次先重跑 P01 基线 262 项，再执行 P02 测试及完整检查。

| 检查 | 实际结果 |
| --- | --- |
| `python -m pytest` | 382 passed；原 P01 262、P02 工作流 81、门禁 25、调度/模式 14 |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 20 files already formatted |
| `python -m mypy src/coding_agent` | Success: no issues found in 13 source files |
| `python -m pip check` | No broken requirements found（沙箱禁止 pip 用户缓存，检查仍成功） |
| 差异检查 | `git diff --check` 通过；未提交或 push |
| 文档/领域边界 | 本地 Markdown 链接与代码块配对检查通过；11 个 P02 JSON Schema 可生成；core 无直接 OS/进程、CLI 或模型供应商导入；AGENTS.md 无改动 |

测试位于 [工作流测试](tests/test_workflow.py)、[门禁测试](tests/test_workflow_gate.py)、
[调度与模式测试](tests/test_workflow_scheduling.py)。覆盖菱形依赖、只读状态、审批指纹、
失败修复与历史保留、审查修复后再验证、共享预算、超时、取消、无效适配器结果、
快照/上下文变更，以及正常生命周期 17 个事件位置逐一模拟写入失败。

**过程中的失败及处理：** uv 读取默认缓存被沙箱拒绝，经审批后得到已安装 Python 清单；
首次 pip 安装因沙箱 DNS/网络限制失败，经联网审批后成功。首轮 Ruff 检出导入顺序和长行，
mypy 检出一处空列表缺少类型标注；已修正并通过完整检查。未删除原测试或放宽验收。

**未实现/未验证：** P03–P12；所有适配器与 writer 均为本地受信 Fake，事件/证据仅存内存。
本阶段只比较外部提供的版本值，不计算真实 Git/文件快照，也不鉴权审批或 Evidence.source。
协作式 asyncio 超时不是进程隔离保证；持久写入、真实权限控制、来源核查、恢复和最终交付
仍须后续实现。P02 在 macOS 验证，Windows 上仅保留 P01 历史验收，P02 Windows 未重跑；
未验证其他平台、真实 Provider 或竞品优势。本阶段不执行真实 Agent 工具或提交代码。

### P03 验收记录（2026-09-15）

**实际交付：** [工具契约](src/coding_agent/core/tools.py)、
[纯权限策略](src/coding_agent/core/tool_policy.py)、
[ToolRuntime](src/coding_agent/tools/runtime.py)、
[文件执行后端](src/coding_agent/runtime/filesystem.py)、
[macOS 进程后端](src/coding_agent/runtime/process.py)、
[JSONL 与工件记录](src/coding_agent/session/records.py)。工作流和 Fake writer 改为共享
writer 的 next_sequence；同步规格、README 和本计划；未增加依赖。

**实现决策与 D05：**

- 工具请求与结果使用冻结 Pydantic 模型；运行时绑定任务、计划、上下文/工作区版本及
  授权。具体操作授权绑定完整调用与版本，不能扩大计划权限；待审批请求需以新 ID 重提。
- 原生 Read/Search/Patch 使用目录描述符、不跟随链接，拒绝硬链接和特殊文件；禁止
  访问控制记录，forbidden 优先并保守处理大小写/Unicode 等价形式。写入还需 allowed。
  Patch 比较实际 SHA256，保留普通文件权限，原子替换并生成真实差异；文件冲突不覆盖。
- macOS 进程采用 default-deny 沙箱：仓库及指定工具链只读，禁止网络、Unix socket、
  Mach IPC、fork 和持久写入；只允许 `/dev/null` 写入。控制器环境和额外文件描述符不继承。
  配置值不是前缀白名单隔离；权限/后端不可用即拒绝，无普通进程执行回退。
- Shell 当前要求绝对可执行路径；未在计划中声明的命令需要具体操作授权。Git 仅提供
  status/diff 并禁用 hooks、外部 diff/textconv、fsmonitor 与全局配置。源文件写入走 Patch。
- 单会话独占创建 JSONL；原计划以脱敏工件及原始指纹登记。请求 fsync 后才能执行，结果
  关联原请求事件、任务和版本；差异/输出工件脱敏、截断标识并记录 SHA256。工作流仍独占
  状态，工具记录不构成 Verification Evidence。
- 日志或工件失败立即停止；存在未完成请求时拒绝继续工具和生命周期记录。只读检查可
  报告待核实请求与不完整尾行，拒绝中间坏行/错误关联，不修复、不重放、不自动重开会话。

**验证环境：** macOS / zsh，项目 `.venv`，Python 3.12.10、Pydantic 2.13.5、
pytest 9.1.1、Ruff 0.16.7、mypy 1.20.2。开工基线 aef0b15，工作区干净；先运行原有
382 项基线，再执行受影响测试和完整检查。以下命令均使用项目虚拟环境 Python。

| 检查 | 实际结果 |
| --- | --- |
| `python -m pytest` | 455 passed；原有 382、P03 工具/日志集成 56、策略/结果约束 17 |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 31 files already formatted |
| `python -m mypy src/coding_agent` | Success: no issues found in 22 source files |
| 差异与文档 | `git diff --check`、本地 Markdown 链接/代码块及领域导入边界检查通过；AGENTS.md 未修改；P03 未提交或 push |

测试位于 [工具集成测试](tests/test_tools.py) 和 [纯策略测试](tests/test_tool_policy.py)。
覆盖实际 read/search/patch/Git 差异、手动修改冲突、相对路径/目录越界、大小写和链接别名、
硬链接/FIFO、ALLOW/ASK/DENY、权限不可用、授权/版本不匹配、输出与文件大小限制、脱敏、
请求/结果/工件写入故障、短写、日志文件被替换、损坏记录与不完整尾行。实际受限 Python
进程验证越界读写、控制记录、网络/Unix socket、sysctl、fork、继承环境/描述符拒绝；超时和取消
会杀死并回收进程。工作流/工具共享日志测试验证事件顺序和故障停止；实际 patch 完成后，
缺少 Evidence 仍不能通过门禁。

**过程中的失败及处理：** 首批进程测试中 Python 以 -6 退出，检查宿主沙箱拒绝日志后
确认加载器需要读取根目录条目；Git 需要对 `/dev/null` 的 write-data。只增加这两项精确
启动许可后通过测试。Unix socket 夹具首次超过 macOS 路径长度上限，改为短临时路径后
实际连接被内核拒绝。Ruff 首轮检出长行/导入顺序，mypy 检出工件 tuple 类型，均已修正。
辅助检查脚本曾误判已有 PurePath 词法校验及 `git diff --no-index` 的差异返回码，修正后
领域边界与新文件格式检查通过；最终复查移除了
无需使用的通用 sysctl 读取许可，增加实际拒绝测试，Python/Git 正常启动测试仍通过。
未删除测试、削弱断言或把执行失败计为通过。

**未实现/未验证：** P04–P12；真实快照、真实 Coder/Verifier、证据来源验证、版本化计划、
恢复和最终交付仍未实现。RevisionReader 仍由受信控制器提供，单文件哈希不代表全仓库
快照。沙箱后端仅在本次 macOS 实测；Windows/Linux 后端未实现，POSIX 文件后端未在
Linux 验证。Apple 标记 sandbox-exec 为 deprecated，其他 OS 版本须重新验证。

当前 shell 禁止缓存/构建写入与子进程，不提供测试数据库；P09 必须满足所需能力再运行
必需验证，不能因此跳过检查。database:deny 的具体资源语义见规格 §21.1：不提供数据库
服务/凭据并阻止持久写入，但不禁止对已允许文件做内存 SQL 计算，数据库数据须由范围
策略保护。工具链读取目录由受信控制器配置。工作区执行期间要求独占，不保证抵御另一个
未受限进程并发移动目录或修改文件。脱敏依赖已知秘密和常见模式，不能声称自动识别所有
凭据。未验证真实 Provider 或竞品优势。

### P03 安全复核与修复（2026-09-16）

**审查发现：** 初版 Git diff 只排除 `.agent`；已入索引的 forbidden 文件被删除后，
Git 仍能从索引输出原文。原脱敏器只匹配完整秘密，多行秘密经 diff 每行添加前缀后
无法匹配。2026-09-15 的验收记录保留为历史证据，不代表本次修改已通过 macOS 复验。

**实际修复：**

- Git diff 先在现有沙箱内执行 `ls-files --cached -z`，复用 `path_permitted` 筛选完整
  索引路径，再用字面路径生成差异；保留大小写、Unicode 等价及控制目录拒绝规则。
  无可读索引路径时返回空差异；清单失败、截断或无法完整解码时停止，不退回全仓库 diff。
- 禁用重命名推断、颜色和展开子模块内容；索引与 diff 共用超时预算，第二次调用前重查
  日志可写性。清单失败保留实际退出码，不制造成功记录；未改变现有沙箱权限或平台限制。
- 已知秘密按完整值和各非空行脱敏，覆盖 Patch/Git diff、上下文行、搜索及嵌套记录。
  同值普通文本可能被保守遮盖；实际文件内容与前后 SHA256 保持真实。
- 新增 [跨平台安全回归](tests/test_tool_security.py)，扩展
  [真实工具与日志回归](tests/test_tools.py)；同步规格和 README，未增加依赖。

**本次验证：** Windows / PowerShell，项目 `.venv`，Python 3.12.14。
跨平台 Git 用例在临时仓库运行实际 Git 命令，以测试适配器替换 macOS 启动器；
它们证明命令和策略行为，不构成 macOS 沙箱验证。

| 检查 | 实际结果 |
| --- | --- |
| `python -m pytest` | 425 passed，61 skipped；新增 26 项跨平台回归通过，新增 5 项真实工具/日志用例因 Windows 平台跳过 |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 32 files already formatted |
| `python -m mypy src/coding_agent` | 13 errors in 3 files；与审查基线相同的 Windows/POSIX API 类型差异（O_NOFOLLOW、O_DIRECTORY、O_NONBLOCK、fchmod、killpg、SIGKILL），未压制或计为通过 |
| `python -m mypy --platform darwin --cache-dir .mypy_cache_darwin src/coding_agent` | Success: no issues found in 22 source files；仅目标平台静态检查，不代表运行过 macOS 后端 |

基线审查为 399 passed、56 skipped；本次新增 31 项回归，未删除测试或弱化原断言。
默认执行器和文件补丁工具因沙箱初始化错误不可用，经批准使用工作区内的 PowerShell/
虚拟环境 Python 完成编辑与检查；未改变产品进程权限。

**未验证与下一步：** 真实 macOS 后端和 POSIX 文件/日志集成测试在 Windows 跳过。
P03 保持 IN_PROGRESS；需在 macOS 对当前代码重跑完整检查，再确认 DONE。P04–P12
未开始；本次未提交或 push。

### P03 Windows 适配验收（2026-09-16）

**授权与范围：** 用户明确要求 Windows 适配；保留此前两项安全修复，扩展 P03，
不推进 P04。本次更新开发计划及 D05；产品内部的版本化计划仍待 P07 实现。
复杂度重新评为 large、风险 high：文件持久化、Win32 ABI、ACL、进程生命周期与 Git
平台语义相互影响；新增依赖用于已有 Git 工具的实际 Windows 使用者。

**实际交付：**

- [平台文件边界](src/coding_agent/runtime/_fileio.py) 与
  [Windows 无跟随句柄](src/coding_agent/runtime/_winfiles.py)：本地固定 NTFS、
  目录句柄固定、拒绝 reparse/junction/硬链接/ADS/设备名/尾点空格/8.3 别名；
  Patch 保留目标 DACL，文件 fsync 与 WRITE_THROUGH 安装；文件工具和日志复用此边界。
- [Windows 进程后端](src/coding_agent/runtime/windows_process.py) 与
  [LPAC/Job 实现](src/coding_agent/runtime/_lowbox.py)：每次复制许可输入，原目录 ACL
  不变；副本只读，限制一个进程/512 MiB，禁用 Win32k 调用，仅继承 stdio。
  只有启动必需的 lpacAppExperience/registryRead 能力，没有网络能力；
  超时、取消、Job 分配失败都会终止并确认回收进程，清理临时 profile 与副本。
  准备时间包含在超时内，复制受 20,000 项/1 GiB 限制。
- [隔离 Git helper](src/coding_agent/runtime/_windows_git.py)：Windows-only
  Dulwich 1.2.14（传递依赖 urllib3）代替无法在本机 LPAC 中运行的原生 Git，
  helper 仍处于同一隔离边界。复用库的索引/对象/状态读取与底层差异生成；
  只读解析本地安全配置，按精确路径生成差异。普通状态、二进制差异、CRLF 和索引
  可执行位已对照真实 Git；不支持的 attributes/include/filter、特殊索引标志、
  冲突、子模块、符号链接、core.filemode=true、外部对象库/Worktree 明确失败。
- [共享路径策略](src/coding_agent/core/paths.py) 同时供原生工具和 Git helper 使用；
  工具 path/cwd 字段保留原始空格，修复原 NonEmptyStr 自动去空格会重定向操作的问题。
  Windows 默认后端不依赖系统 Git；Shell 的独立工具链仍由控制器明确配置。

**验证环境：** Windows 11 10.0.26200 / AMD64 / NTFS；项目 `.venv`，
CPython 3.12.14。使用临时目录、仓库、独立 AppContainer profile；无需模型凭据。
原 61 项跳过中的 42 项文件/日志集成现在在 Windows 实际运行；
保留 17 项真实 macOS 沙箱和 2 项 POSIX 专属测试的跳过理由。

| 检查 | 实际结果 |
| --- | --- |
| `python -m pytest -q -rs` | 506 passed，19 skipped；包括当时全部 38 项 Windows 专项 |
| `python -m pytest tests/test_windows.py -k network_denied -q` | 2 passed，37 deselected；全套之后补加非回环出站用例，并重跑回环用例，两者均为 WinError 10013。当前 39 项 Windows 专项均已执行通过 |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 39 files already formatted |
| `python -m mypy src/coding_agent` | Success: no issues found in 28 source files（Windows 目标） |
| `python -m mypy --platform darwin --cache-dir .mypy_cache_darwin src/coding_agent` | Success: no issues found in 28 source files；只代表静态检查 |
| `python -m pip install -e .` / `python -m pip check` | 安装成功，Windows 条件依赖解析成功；No broken requirements found |
| 差异与文档 | `git diff --check`、本地 Markdown 链接/代码块检查通过；未提交或 push |

[Windows 回归](tests/test_windows.py) 覆盖实际隔离读、原仓库/副本/控制记录/外部文件
访问拒绝、回环与非回环网络拒绝、子进程及额外句柄拒绝、环境清理、输出截断、
准备/进程超时、取消后确认进程退出及 profile 删除、Job 分配失败不恢复子线程、
路径别名与连接点、DACL/CRLF 保留、Git 范围/脱敏及不支持输入的拒绝。
读取故障回归确保 Git helper 不把 PermissionError 当作删除或成功。

**过程中的失败及处理：**

- 原生 Git 在最终 Win32k 限制下出现 DLL 初始化失败；诊断时去掉该附加限制后，仍因
  AppContainer 的路径规范化权限失败。与
  [微软项目的问题记录](https://github.com/microsoft/mxc/issues/694) 相符。
  未更改系统对象命名空间/设备 DACL，改用隔离的库 helper；临时诊断覆盖均已移除。
- AppContainer 初始探针缺少 profile/必需加载能力而无法启动；最终仅增加上述两项
  启动能力。早期 Child Process Policy 与加载器冲突，改由恢复线程前分配的
  单进程 Job 强制限制子进程，并以真实派生尝试验证拒绝；Win32k 限制保留。
- 3 秒超时探针耗在执行副本准备，未启动进程；分别测试准备超时不启动和 15 秒预算下
  已启动进程被终止，保留 started 断言。尾空格路径测试暴露工具模型自动 trim，
  从公共字段根因修复。Git 状态输出排序/换行、冲突夹具 stdin 的 Windows CRLF
  转换均已修正；冲突夹具改为字节输入并核实实际存在三个索引阶段。
- 库的高层 diff 会把读取失败当删除，且未按 Windows core.filemode=false 保留
  索引执行位；改为显式读取、精确路径及库的底层 diff，保留真实 IO 失败。
- Ruff 导入/格式和跨平台 mypy 导入分支问题均已修复。
  安装探针 `--no-build-isolation` 因当前 venv 无 setuptools 失败；
  使用项目声明的标准隔离构建后安装成功。辅助差异检查曾错误覆盖
  core.autocrlf=false，导致 CRLF 被整文件误报；恢复仓库原有设置后检查通过。

**仍未验证/下一步：** Windows 最低 API 门槛为 10 1809，但只在上述 Windows 11
宿主实测；其他版本、非 NTFS、Linux 进程执行不宣称可用。
Windows 私有 AppContainer 存储允许该次运行写临时数据，不是完整虚拟机或绝对零写入；
正常清理已验证，机器崩溃后的孤儿 profile/副本核对属于 P12。
许多构建/测试需要的缓存写入、子进程和测试数据库仍需 P09。
当前代码的真实 macOS/POSIX 复验仍待完成，P03 保持 IN_PROGRESS；下一步先复验，
再按授权推进 P04，不将工具操作记录当作 Verification Evidence。

**后续安排（用户指令）：** 先提交当前安全修复与 Windows 适配，由用户后续验证。
P03 保留 IN_PROGRESS 及 macOS 真实集成复验待办；提交不代表阶段验收通过。

### P04 实现与验证记录（2026-09-16）

**实际交付：** [领域模型](src/coding_agent/core/workspace.py)、
[工作区运行时](src/coding_agent/runtime/workspace.py)、
[无跟随快照与恢复存储](src/coding_agent/runtime/_snapshots.py)、
[Tool Runtime 集成](src/coding_agent/tools/runtime.py)、
[临时仓库回归](tests/test_workspace.py) 与 [Windows LPAC 集成](tests/test_windows.py)。

- 主仓库只读；以当前可读输入建立独立会话 Git 基线及真实 detached Worktree，
  不修改主仓库的 staged/unstaged 状态或分支，也不复制原仓库历史。
- SHA-256 清单包含相关已修改、未跟踪及被 Git ignore 的文件、POSIX 执行位与输入排除规则。
  真实 RevisionReader 接入工具和 QualityGate；新增源文件会令已有 Evidence 过期。
- 生命周期仅由控制器经 Tool Runtime 请求；Agent 入口拒绝 prepare/reset/cleanup。
  Git 管理只操作生成的私有元数据，真实命令与结果留痕；Shell 隔离保持 P03 边界。
- Reset 保存当前快照并从已保存目标创建新 Worktree，完整保留旧树中的手工及被排除文件；
  Cleanup 仅删除身份匹配且与基线一致的活动树，拒绝未知文件/目录。
  旧树、原始内容存储及日志均保留，清理不表示自动销毁全部会话数据。
- 读取上限为 20,000 项、单文件 1 MiB、内容总量 64 MiB、清单 8 MiB；遇到链接、
  不支持的路径、读取失败或上限拒绝生成成功快照。常见缓存排除项和控制记录不引发自我失效；
  自定义生成目录须在批准计划前配置，不能动态扩大排除范围来复用证据。

**验证环境：** Windows 11 build 26200 / NTFS、Python 3.12.14、项目虚拟环境。
所有文件/Git/故障样本都在临时目录中，主仓库只接受本次源代码修改。
真实 macOS/POSIX Worktree 与进程集成仍待实机复验，P04 保留 IN_PROGRESS。

| 检查 | 实际结果 |
| --- | --- |
| `python -m pytest -q -rs` | 543 passed、21 skipped；224.44 秒。覆盖当时全部测试，包括新增 Worktree/LPAC 集成 |
| 收尾回归：`python -m pytest tests/test_workspace.py tests/test_tools.py tests/test_tool_policy.py tests/test_tool_security.py -q -rs` | 124 passed、21 skipped；27.95 秒。在全量测试后补充串行任务写范围、共享运行时重置后路径同步、无尾换行差异三个回归，并验证全部受影响工具测试 |
| `python -m pytest tests/test_windows.py -k managed_worktree -q` | 收尾后再次通过：1 passed、39 deselected；5.26 秒 |
| `python -m ruff check .` / `python -m ruff format --check .` | All checks passed；43 files already formatted |
| `python -m mypy src/coding_agent` | 31 个源文件通过（Windows 目标） |
| `python -m mypy --platform darwin --cache-dir .mypy_cache/darwin src/coding_agent` | 31 个源文件通过；仅静态检查，不替代 macOS 实测 |
| 差异与文档 | `git diff --check`、本地 Markdown 链接和代码块检查通过 |

21 项跳过为 18 项真实 macOS 集成、3 项 POSIX 专属测试；不计为通过。
本次没有新依赖或模型调用。后续用户授权提交 P04；macOS/POSIX 复验待办与阶段状态保持不变。

**过程中的失败：** Windows create-only 安装最初误用 replace 分支，触发不存在目标
DACL 的错误；恢复复制改为明确的 create-only 安装，防止覆盖已有路径。
元数据篡改测试最初使用覆盖隐藏的 `.git` 文件方式，被 Windows 拒绝；
改为已存在文件句柄写入，以实际验证身份/内容保护，不弱化断言。
macOS 目标类型检查发现 Windows-only 的 CREATE_NO_WINDOW 引用，改为明确的平台分支；
Ruff 的测试夹具别名问题已修正。全量检查后补齐共享运行时的当前路径同步，避免 reset
之后旧 ToolRuntime 使用新快照却写入保留树；新增回归并重跑全部受影响工具测试。
串行任务范围复用曾被自动审批拒绝；只读核对证明读取依据 forbidden、每次写入仍由
当前已批准任务的 PolicyEngine/SafeFiles 检查 allowed 后，复审允许实施。
最终保留 forbidden 完全一致要求，并验证越界写入拒绝。

**边界：** 当前不自动采用已有会话目录，不对缺失结果进行猜测性重试；
跨重启 reconciliation/resume 和保留数据回收属于 P12。差异工件供检查，
不宣称已经完成交付补丁应用、合并或 Verification Evidence 生成。

### P05 实现与验收记录（2026-09-16）

**实际交付：** [模型领域接口](src/coding_agent/core/provider.py)、
[OpenAI 适配器](src/coding_agent/providers/openai.py)、
[模型运行时](src/coding_agent/providers/runtime.py)、
[共享事件](src/coding_agent/core/workflow/events.py)、
[共享记录器](src/coding_agent/session/records.py)、
[SDK 离线测试](tests/test_provider.py)、
[故障与权限集成测试](tests/test_model_runtime.py) 和
[真实冒烟入口](tests/test_provider_live.py)。

- SDK 固定为 openai 3.14.1；业务不接触 SDK Response。SDK 自动重试关闭，
  模型、超时和输出预算由控制器明确配置，API key 不写入源文件或记录。
- 严格解析对象输出和函数参数，核对工具结果 ID；拒绝重复调用、未知工具、拒答和不完整响应。
  Responses 的加密推理续接信息只用于内存中下一轮调用，不写入模型输出工件。
- ModelRuntime 与 ToolRuntime 共享先记请求再执行的记录顺序和单次操作边界。
  可见草稿脱敏，输入只记指纹；记录失败或缺失结果停止新副作用。
  Fake 返回的无效结果也在这一边界拒绝，模型草稿不会成为任务状态或 Evidence。
- 超时和取消终止本地等待；远端是否处理/计费可能未知，不写零用量或自动重试。
  实际输入/输出/缓存/推理 token 数按服务返回记录，不估算价格。
- 真实冒烟仅使用合成提示及临时文件，最多 3 次调用，每次 1,024 输出 tokens / 30 秒；
  未配置 OPENAI_API_KEY、OPENAI_MODEL，当前没有运行真实模型。
  P05 保持 IN_PROGRESS；不宣称真实接入已验收，不推进 P06–P12。

**验证环境：** Windows 11 build 26200 / NTFS、Python 3.12.14、项目虚拟环境。
官方接口核对范围与有界真实冒烟命令见 [README](README.md)。

| 检查 | 实际结果 |
| --- | --- |
| P05 首批离线检查 | 62 passed、1 skipped（真实冒烟）；9.10 秒 |
| 收尾相关回归：provider/model_runtime/provider_live/workflow/tools | 188 passed、20 skipped；12.27 秒。补充非模型返回值和失败记录一致性 3 个回归后，重跑全部受影响模块 |
| 完整 `python -m pytest -q --tb=short -ra` | 608 passed、22 skipped；216.94 秒。包含真实 Windows LPAC/Worktree 回归；随后 3 项收尾新增测试由上一行覆盖 |
| Ruff / format | All checks passed；50 files already formatted |
| mypy | 35 个源文件通过 Windows 与 darwin 目标；后者不替代 macOS 实机测试 |
| 冒烟入口的离线演练 | 使用模拟 HTTP 跑通同一入口的 3 次模型调用和真实临时文件读取，未使用网络或真实凭据 |
| 依赖与差异 | `pip check` 无损坏依赖；`git diff --check`、本地 Markdown 链接与代码块检查通过 |
| 真实 API | 未执行；缺少 key/model 配置，不计为通过 |

22 项全量跳过为 18 项 macOS 集成、3 项 POSIX 专属测试和 1 项真实模型冒烟，均不计为通过。

**过程中的失败与修正：** 首批 SDK 模拟响应遗漏新版协议必填的
`cache_write_tokens`，严格解析正确拒绝了响应；补齐模拟协议字段并将其纳入可用用量。
20 ms 的超时样本可能在 SDK 请求准备时结束，尚未进入传输；改为 500 ms 的有界等待，
继续断言传输确实被取消。补齐错误 JSON、历史 call ID 重用及各项用量约束。
收尾静态检查发现 Fake 模块说明行过长，缩短后 Ruff/格式检查通过。
这些是离线测试与实现修正，不能代替真实服务兼容性验证。

### Codex adapter 扩展记录（2026-09-16）

**授权与范围调整：** 用户提出复用本地 Claude Code/Codex/Pi，并回复“继续”。
开发计划更新为本次 adapter 扩展版本：保留 P05 的一个 API Provider，提前交付 P08 的
首个 Codex adapter。复杂度 large、风险 high，原因是外部进程生命周期、协议关联、
共享日志和工具权限耦合。不新增框架或依赖，不重置任务尝试次数与修复预算。
Claude Code/Pi 仅规划；P06/P07、完整 P08 CLI 与上下文集成不计为完成。

**实际交付：** [CodexCoder](src/coding_agent/executors/codex.py)、
[stdio 与进程管理](src/coding_agent/executors/_codex_rpc.py)、
[离线与真实 CLI 协议测试](tests/test_codex.py)、
[真实账号冒烟入口](tests/test_codex_live.py)。

- Codex 负责编码循环，控制器转接动态工具并校验最终 CoderResult。
  不引入 LangGraph、Agent 团队、注册工厂或通用插件系统。
- 复用 ModelRuntime 分段请求/结果记录：Codex 提交工具请求后等待，模型结果先落盘，
  Tool Runtime 再记录并执行；下一段模型请求落盘后才发送工具结果。
  不扩展 JsonlJournal 的并发或嵌套操作规则。
- 固定已验证 CLI 0.154.0-alpha.6.2；关闭原生环境访问、shell、插件、钩子等能力。
  核对返回版本、环境、目录、指令来源和权限；不匹配则停止。项目读写、shell、Git
  均通过五个 runtime 工具。Worktree、提示词和进程分组不被当作 OS 沙箱。
- 使用项目与日志之外的专用 CODEX_HOME、固定配置及独占锁；不覆盖其他配置，
  不复制日常全局配置/凭据。由用户在专用目录运行 codex -c cli_auth_credentials_store=file login；
  凭据刷新与 CLI 私有缓存由 Codex 管理，不属于本项目权威日志。
- Windows 隐藏原生 exe，用 Job 管理生命周期/禁止子进程；POSIX 使用独立进程组。
  实际 shell 权限继续由 P03 执行。总时限、工具次数和协议大小均有限制；
  取消等待进程结束，日志失败停止新动作，已完成修改保留。
- Codex 未暴露这里可强制的单次输出 token 上限，记录 max_output_tokens=null；
  OpenAI API 适配仍要求明确上限。CLI 内部有限传输重试受总时限约束，
  一条模型记录不等于一次 HTTP 请求。仅在最终结果记录报告的累计用量，
  中间段保持未知，避免重复计数；未报告消耗不估算。
- P05/P08 保持 IN_PROGRESS。真实登录/模型调用与 macOS 实机验证未执行；
  本地假模型不使用真实凭据，不证明账号、服务或模型行为可用。

**过程中的失败与修正：** 扩大核心日志并发模型的操作被自动审批以风险过宽拒绝，
未执行；改为复用已有串行分段记录。真实 CLI 拒绝覆盖内置 openai provider 配置，
已移除无效配置，不声称关闭了其所有内部重试。测试复现 turn/start 响应前
先到达状态通知，现暂存通知，核对返回的 turn ID 后再处理。超长行测试初版缺少简短
参数 ID，造成 pytest 路径/输出异常，已补 ID；skills.read 越界回归核对实际拒绝
结果 skill package is not available，并确认项目内容未泄漏。

**最终验证（Windows 11 build 26200 / Python 3.12.14）：**

| 检查 | 实际结果 |
| --- | --- |
| adapter 初批回归 | 53 passed、1 skipped（真实账号冒烟），10.13 秒 |
| 执行器与配置路径保护补充后定向回归 | 55 passed，10.63 秒 |
| 完整 pytest | 666 passed、23 skipped，270.76 秒；包含真实 Windows LPAC、Worktree 及 Codex + 本机假模型 |
| Ruff / format | All checks passed；55 files already formatted |
| mypy | 38 个源码文件通过 Windows 与 darwin 目标；后者不替代 macOS 实机验证 |
| 真实冒烟入口的离线演练 | 使用真实 Codex 和 3 个 loopback 模拟响应，跑通 read、patch、文件内容与实时快照断言；未使用真实凭据 |
| 依赖、差异与文档 | pip check、git diff --check、UTF-8、Markdown 代码块和本地链接检查通过 |
| 真实服务与其他平台 | 两条真实模型/账号冒烟未执行；macOS 实机未复验 |

23 项跳过为 18 项 macOS 集成、3 项 POSIX 专属测试、2 项真实模型/账号冒烟，
均不计为通过。执行器路径与配置目录现在都必须在任务项目和权威日志之外，
防止任务工具改写下一次尝试的执行器。此前 P05 的检查历史保留在上一节。

### Windows 自动测试弹窗修复（2026-09-16）

用户报告自动测试弹出「python.exe - 系统错误：指定无效的句柄」。
定位到旧句柄继承测试：沙箱子进程操作未继承的句柄，以
STATUS_INVALID_HANDLE（0xC0000008）原生异常退出；旧断言仅要求非零退出，
因此先前完整测试通过不能证明执行过程没有弹窗或等待人工确认。

- [LPAC Job](src/coding_agent/runtime/_lowbox.py) 增加
  JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION。未处理原生异常结束进程并返回实际错误码，
  不修改控制进程的全局错误模式；保留文件、网络、进程数及内存限制。
- [句柄回归](tests/test_windows.py) 改为由控制进程在子线程恢复前，
  用 DuplicateHandle 检查实际句柄表，要求明确的 ERROR_INVALID_HANDLE；
  沙箱子进程本身必须正常退出，不再把任意崩溃当作隔离成功。
- 独立故障回归在临时沙箱子进程制造原来的无效句柄异常，要求直接记录
  failed / 0xC0000008、无未完成请求，且宿主错误模式不变。

**过程中的失败：** 初次改用子进程 os.get_handle_inheritable 查询仍触发同一原生异常，
已改为上述控制进程检查。诊断时尝试 ctypes 查询错误模式，_ctypes 在当前沙箱中加载失败，
因此不能据此判断异常策略是否生效；最终依据真实崩溃的退出码、超时状态与日志验证。
没有放宽沙箱约束或把这些失败计为通过。

**最终验证（Windows 11 build 26200 / Python 3.12.14，项目虚拟环境）：**

| 检查 | 实际结果 |
| --- | --- |
| 两项定向回归 | 2 passed，4.17 秒 |
| Windows 专项 | 41 passed，154.98 秒 |
| 完整 pytest | 667 passed、23 skipped，285.11 秒 |
| Ruff / format | All checks passed；55 files already formatted |
| mypy | 38 个源文件通过 Windows 目标 |
| 差异 | git diff --check 通过 |

23 项跳过仍为 18 项 macOS 集成、3 项 POSIX 专属、2 项真实模型/账号冒烟，
不计为通过。本次不新增依赖、不推进阶段；真实模型/账号与 macOS 实机待办保持不变。
随后用户授权将 P05、Codex adapter 与本次 Windows 弹窗修复一并提交；阶段状态及未验证项保持不变。

### P06 开始（2026-09-16）

用户在提交 2684e85 后要求继续，按下一执行单元推进 P06，保留 P03/P04/P05/P08 未验证项。
工作区开工时干净。范围为只读 Explorer、有来源的 RepoSummary、知识修订/刷新与 init CLI；
不实现 P07 Planner 或 P08 run 命令。复杂度 large、风险 high：控制资料保护、刷新一致性、
模型输出来源与持久记录关联需要共同验证。

实现约定：复用现有文件边界、JSONL 记录和 ModelRuntime；初始化记录使用明确的无计划修订，
不伪造业务 TaskGraph 或验收通过。项目知识发布属于控制器工具操作，Agent 工具不能调用。
project.json 只索引不可变修订、来源与指纹，用户规则保留在 project.md 和原有规范中；
刷新遇到生成区人工编辑或不完整发布时停止并保留现场，不猜测覆盖。

规格 §34 推荐的 Typer 暂不引入：当前 init/refresh 参数由 argparse 满足，后续 CLI 需求再评估。
离线探索与模型辅助探索分别标注；命令发现不执行命令，不产生验证 Evidence。
初次 plan/run 将复用本阶段 API，其 CLI 集成仍分别属于 P07/P08。

### P06 实现与验证记录（2026-09-16）

**实际交付：** [知识模型](src/coding_agent/core/knowledge.py)、
[只读 Explorer](src/coding_agent/context/explorer.py)、
[初始化控制器工具](src/coding_agent/tools/initialization.py)、
[共享应用 API](src/coding_agent/application/initialization.py)、
[CLI](src/coding_agent/cli.py) 与 [回归测试](tests/test_initialization.py)。
新增标准库 argparse 命令入口，无新增依赖；README 与规格 §16.5/§34 同步实际边界。

- 离线识别代表性源码、规范、配置、模块声明、导入/调用文本和验证命令；
  模型辅助扩充模块职责、耦合与假设，来源路径/行范围/引文必须能在已读输入中核对。
  规则只能逐字引用规范或用户来源，嵌套规范保留作用域。完整调用图和真实模型质量不计为已验证。
- 支持 init、refresh、rule、focus、forbid、JSON 输出。保留生成区外的用户内容，
  兼容 CRLF；未变化修订直接复用，过期输入返回 stale，历史修订保留。
  刷新保留来源未变的模型条目和命令；必要问题不因模型省略或 required 降级而消失。
  模型须引用明确用户/规范答案解除阻塞；答案来源变化会重新打开问题，离线模式不判断答案语义。
- project.json 仅为当前修订索引；knowledge-<revision>.json 保存结构化摘要、来源摘录与指纹。
  摘录先脱敏再存储/送模型，原始文件指纹用于过期检测；未读内容和截断部分不宣称已理解。
  发现现有 Sanitizer 未覆盖带引号的 JSON 凭据赋值，在共享入口修复并保持 JSON 语法有效。
- 控制器工具先写请求、再探索或发布、最后记录结果；无计划初始化修订不能产生任务状态或 Evidence。
  .agent/锁/初始日志建立是记录系统自身的 bootstrap，Agent 工具无权调用知识发布。
  发布前核对源码与用户输入，比较预期文件内容；异常保留旧修订/用户修改。
  未知结果保留锁，生成区/元数据冲突明确拒绝；P12 自动恢复没有提前实现。
- 限制与可见缺口：10,000 项目录枚举、默认 12/最多 24 来源、单文件读取 1 MiB、
  每摘录 32 KiB、总摘录 192 KiB。超大规范拒绝截断；需针对任务使用 focus 补充未读源码。
  当前每次 init 仍枚举可见文件并读取有界来源，增量复用的是知识条目，没有新增缓存/监听框架。

**过程中的失败及处理：**

- 两文件发布故障测试最初只改函数返回值，生成文本未变，旧元数据仍与旧/新相同文本一致；
  改为修改被引用的声明，真实覆盖生成区改变而索引未提交的异常，不放宽拒绝规则。
- CRLF 编辑导致生成区校验误报：仅对生成区换行规范化比较，用户区保持原文；
  增加回归，同时保留真正生成区改写的拒绝行为。
- pip install -e . --no-deps --no-build-isolation 因虚拟环境缺少 setuptools 构建后端失败；
  改用声明的隔离构建 pip install -e . --no-deps 后成功，未增加项目依赖。
- 仓库副本冒烟脚本最初用 Windows 默认 GBK 读取 UTF-8 JSON，检查脚本失败；
  显式 UTF-8 后完成四步验证。格式检查曾在格式化完成前启动，报告未格式化；
  串行完成格式化后重新检查通过。

**验证环境：** Windows / PowerShell，Python 3.12.14，项目虚拟环境。

| 检查 | 实际结果 |
| --- | --- |
| 初始化专项（含必要问题答案/失效回归） | 51 passed，25.04 秒；后续命令保留调整已纳入最终完整回归 |
| 初轮完整 pytest | 719 passed、23 skipped，417.35 秒；其后新增必要问题回归，不作为最终代码检查结果 |
| 最终完整 pytest | 721 passed、23 skipped，381.12 秒 |
| Ruff / format | All checks passed；64 files already formatted |
| mypy | Windows / Darwin 各 46 个源文件通过；Darwin 目标检查不代表实机验证 |
| 安装及依赖 | editable 安装、agent init --help、pip check 通过 |
| 已安装 CLI 的仓库副本冒烟 | initialized → reused → stale（退出 2）→ refreshed；51 个文件、12 个仓库来源及用户来源，74 个条目、5 个发现命令；用户区保留 |
| 差异/文档 | git diff --check、UTF-8、代码块配对及本地文档链接检查通过 |

**未完成/未验证：** P06 真实模型探索与 macOS/POSIX 实机初始化；P03/P04 的 macOS 复验、
P05 API 和 P08 Codex 账号冒烟继续保留。23 项跳过分别为 18 项 macOS 集成、3 项 POSIX、
2 项真实 API/账号，均不计为通过。P06 保持 IN_PROGRESS。P07/P08 将实际接入共享初始化 API，
Planner/run CLI、Verification 和自动恢复尚未实现。用户随后授权提交本次 P06 实现、测试及文档；
阶段状态与未验证项保持不变，未授权推送。

### P07 开始（2026-09-16）

用户在提交 5f81f99 后授权继续，按下一阶段推进 P07。开工时工作区干净；
保留 P03–P06/P08 的真实模型、账号和跨平台复验待办。范围仅只读 Planner、
计划编译/版本化、保存计划导入、plan/graph/status CLI，不执行 Coder 或仓库验证命令。
复杂度 large、风险 high：知识新鲜度、任务图、规则/验收覆盖、持久控制记录和授权指纹耦合。

实现决策：复用 P06 初始化控制器的无跟随 IO、独占锁与请求/结果记录，
规划阶段使用独立 PlanningRevision/PlanningOperation，不伪造工作流状态。
起始代码版本复用 P04 全范围文件清单/内容指纹，覆盖未提交及 Explorer 未读取的文件。
模型只返回 PlanDraft，确定性校验后保存不可变计划及可读摘要；执行授权另行核对。
默认不调用真实模型；离线输入为用户提供的结构化草稿，缺少模型/草稿时明确阻塞。
当前批次与未来里程碑分开，未细化任务不进入 TaskGraph；计划修订保留稳定 ID、验收和预算。
P11 的运行中重规划/跨批次消耗协调、P12 的恢复与交付仍保留为后续范围。


### P07 实现与验证记录（2026-09-16）

**实际交付：** [计划模型与编译](src/coding_agent/core/planning.py)、
[只读 Planner](src/coding_agent/agents/planner.py)、
[计划控制器工具](src/coding_agent/tools/planning.py)、
[应用流程](src/coding_agent/application/planning.py)、
[CLI](src/coding_agent/cli.py) 与 [回归测试](tests/test_planning.py)。
无新增依赖；README 与规格 §8.1 同步实际能力和后续阶段边界。

- PlanDraft 绑定需求、全局验收、功能需求覆盖、任务级验收、规则/知识 ID 和来源。
  复杂度单独记录范围、耦合、不确定性、验证难度、理由与策略；风险不能因 Small 而降低。
  确定性检查覆盖 DAG、具体文件写范围、重叠写入顺序、权限/预算上限与适用规则。
  尚未探索的已有目标文件阻塞提案，需用 focus 补充来源。
- 大型需求保留整体里程碑，仅编译当前批次；后续未知范围保留为 pending，不创建可调度占位任务。
  重构须声明行为不变量与基线检查；P07 不执行这些检查，也不生成 Evidence。
  必要问题必须有带来源的明确答案；删除、降级或转移问题不能解除阻塞。
- 一次只读模型调用返回结构化草稿，或读取离线草稿；没有模型/草稿时明确阻塞。
  Planner 获得脱敏知识及实际规则 ID，模型没有工具、文件写权限或状态变更能力。
  API/CLI 复用 P06 初始化和 P04 全范围文件快照，未提交、未跟踪及 Explorer 未读文件均参与失效检测。
- 保存不可变 JSON/可读摘要、前驱摘要、变更原因和稳定 ID，最后安装当前计划索引。
  原有需求、验收、待办范围、任务身份和授权上限不能在计划修订中被默默缩减或重置。
  导入重查知识/代码新鲜度，保留原版本归档，并生成新的本地版本；拒绝受保护目录逃逸、
  Windows ADS/别名和包含已识别未脱敏凭据的导入，原用户文件保留。
- PlanningRevision/PlanningOperation 明确表示执行前记录；控制器复用安全文件 IO、
  独占锁和先请求后动作的日志顺序。发布前重新核对来源、用户规则、工作区与索引，
  失败保留旧索引/用户修改；结果未知保留锁和现场，未实现自动恢复。
- RunSpec 审批指纹包含完整计划摘要与 pending_milestones，编译保留控制器禁止读取范围。
  工作流结果继续携带待完成里程碑；本批次通过不能代表整个需求完成。
  status/graph 只读，不初始化、不调用模型、不记日志；execution_status=not_tracked，
  不从提案推测执行结果，也不生成审批。agent run 与完整 Coder 上下文仍属于 P08。

**过程中的失败及处理：**

- 初批测试夹具误将 tuple 当列表，并漏传策略检查必填参数；已修正夹具，
  保留针对非法计划、权限与依赖的拒绝断言。
- 导入归档与编译接线时遗漏方法参数/导入，专项测试和静态检查发现 TypeError/NameError；
  已补齐调用链并重新验证，未删除测试或降低验收要求。
- 并发运行完整 Windows 原生测试与 P07 专项时，SDK 平台信息查询触发
  Windows 原生诊断 0x8007000e，栈位于 platform._wmi_query → OpenAI SDK get_platform。
  该进程最终 45 passed、退出 0；现象尚未定位，不能据此称为无异常运行。
  随后串行复验 SDK Schema 与凭据导入两项，2 passed、8.11 秒，未再次出现诊断。
  未修改 SDK、关闭断言或跳过测试；此现象与此前已修复的无效句柄弹窗不同。
- 中断前最后一轮回归的结果无法恢复；确认没有残留测试进程后串行重跑，并保存独立输出日志。
  无法恢复的结果不计为通过。

**验证环境：** Windows / PowerShell，Python 3.12.14，项目虚拟环境。

| 检查 | 实际结果 |
| --- | --- |
| P06 / workflow 回归 | 132 passed，40.89 秒 |
| 计划、初始化、工作流、模型记录、策略联合回归 | 211 passed，34.39 秒；其后新增凭据导入回归 |
| 初轮完整 pytest | 765 passed、23 skipped，441.07 秒；其后新增凭据导入拒绝，不作为最终代码结果 |
| 最终 P07 专项 | 45 passed，42.87 秒；包含上述未定位 WMI 诊断；串行两项复验通过 |
| 最终串行完整 pytest | 766 passed、23 skipped，189.67 秒；未再次出现 WMI 诊断 |
| Ruff / format | All checks passed；70 files already formatted |
| mypy | Windows / Darwin 各 51 个源文件通过；目标检查不替代实机验证 |
| 已安装 CLI 临时项目冒烟 | plan v1 → v2、status、graph 通过；旧版本保留；未探索文件变化后 status 为 stale、旧计划导入拒绝，均退出 2 |
| 差异/文档 | git diff --check、UTF-8、Markdown 代码块配对及本地链接检查通过 |

**未完成/未验证：** 真实模型对代表性项目的计划质量、macOS/POSIX 实机计划流程；
P03–P06/P08 既有平台/API/账号待办继续保留。23 项跳过分别为 18 项 macOS 集成、
3 项 POSIX 专属与 2 项真实 API/账号冒烟，均不计为通过。
P07 保持 IN_PROGRESS；P08 的运行集成、P09 实际验证、P11 重规划/累计预算和 P12 恢复/交付
仍未实现。用户随后授权提交本次 P07 实现、测试与文档；阶段状态与未验证项保持不变，未授权推送。


### P08 应用集成开始（2026-09-16）

用户在提交 ed34fd3 后授权继续。开工时工作区干净；本次复用现有 Codex adapter、
WorkflowEngine、P04 Worktree、P06 知识和 P07 计划，不新增编码框架或依赖。
复杂度 large、风险 high：上下文来源、审批指纹、预算、真实修改与持久记录需要共同验证。

本轮范围为任务上下文、agent run、diff/history、真实工具记录与显式重规划请求。
执行需要匹配具体提案及执行配置的授权；独立工作区保留用户源文件，同一计划只创建一次执行目录。
P09/P10 尚未接入，缺少验收/审查时必须阻塞；重构缺少基线验证时在修改前阻塞。
不提前实现恢复、自动提交、跨批次重规划或凭据登录；既有真实服务/跨平台待办继续保留。


### P08 应用实现与验证记录（2026-09-16）

**实际交付：** [TaskContextPack](src/coding_agent/context/coder.py)、
[执行应用流程](src/coding_agent/application/execution.py)、
[执行记录/只读查看](src/coding_agent/tools/execution.py)、
[CLI](src/coding_agent/cli.py)、
[应用回归](tests/test_execution.py) 与 [独立真实账号冒烟入口](tests/test_execution_live.py)。
继续使用既有 Codex 编码循环与 Python 工作流，不新增依赖、引擎工厂或并发 Agent。

- 上下文绑定当前任务、完整计划、知识与工作区修订，包含需求、复杂度、验收、规则 ID、
  来源和未来范围。原知识摘录与 Tool Runtime 重读的当前源码分开保留，模型调用前持久化。
  单段当前源码 32 KiB、整包 512 KiB，截断标注，超限阻塞；不删除必需规则来缩小输入。
- run 预览输出 RunSpec 与审批指纹；显式 --approve 才建立执行。
  指纹包含 Codex 配置、工作区位置、计划、权限与预算；缺少计划时复用初始化并提示先规划。
  审批记录先于 Worktree 创建；已有源码/用户编辑保持原样，真实修改只留在独立 Worktree。
- 同一日志累计 Agent/上下文工具请求与模型分段数，默认 30/31，上限拒绝也记录实际结果。
  Codex 另有每尝试 20 工具/60 秒，WorkflowEngine 继续限制任务与总尝试。
  新 Coder 实例不能重置累计计数；分段不等于 HTTP 请求或硬 token/费用上限。
- CoderResult 增加结构化 ReplanRequest，说明触发原因、建议复杂度与需要变化；
  WorkflowEngine 记录草稿并停止调度，应用拒绝缺少详情的 replan。
  ImplementationResult 的修改路径、前后版本及派发命令由真实快照/工具事件构建，
  命令结果和退出码继续以 ToolResult 为准，模型总结保留为 draft。
- 每次模型/工具调用前核对源项目、用户规则与计划；Worktree 中的规范改变也阻止后续动作。
  日志写失败停止新副作用；未知结果留锁。取消保留修改、中断记录及可安全生成的最终快照/Diff。
  同一计划各版本共用 run-<plan-id>，已有执行目录拒绝重跑；不隐式恢复或重置预算。
- status 优先展示已有执行记录；diff/history 支持显式旧会话且只读。
  实际状态来自 WorkflowEngine 转换，Diff 校验引用摘要并显示截断/缺失；历史快照不代表后续人工修改。
  P09/P10 尚未接入，正常修改也因缺少 Evidence 阻塞；重构先缺基线阻塞，需求完成始终为 false。

**过程中的失败及修正：**

- 首批应用回归在 Worktree 准备前阻塞：P04 构造器排序了传入排除规则，而 P07 快照
  保留原序列，导致同一输入的授权指纹不同。改为原样保留已批准的选择策略，
  通过 P07 → P04 实际创建与后续修改覆盖；未重写历史计划或放宽快照匹配。
- 后续 Patch 拒绝是测试夹具的字节错误：Windows write_text 写出 CRLF，而测试给出 LF 哈希。
  核对临时文件原始字节、不同 SHA-256 与失败 ToolResult 后，改为以 write_bytes 创建确定的 LF 合成输入。
  保留全部精确内容/哈希断言；新增 CRLF 无损复制及错误 LF 哈希拒绝覆盖测试。
- 自动审批曾拒绝“动态取哈希并改用 splitlines 断言”的拟议修改，理由是可能弱化测试；
  该修改未执行。采用上述经字节核对的夹具修正，并增加反向拒绝回归；未删除或放宽断言。
- 重构样本最初试图在既有计划修订中替换不变量，被 P07 保守校验拒绝；
  测试改为独立的新重构提案，继续验证修改前的基线阻塞，未改变版本保护规则。
- 首轮静态检查发现未使用导入、格式和 guard 参数缺少类型，已修正并重新检查。
- 最终边界核查补充了显式 baseline_check_ids 的修改前阻塞（即使 refactor 标志为 false），
  并将当前源码摘录限额改为 UTF-8 字节截断；3 项定向回归通过，8.73 秒，完整回归随后重跑。
- 已安装 CLI 冒烟脚本最初错误要求子命令 --help 包含父命令菜单的说明词；
  实际子命令帮助提供参数列表。核对输出后改为验证公开的 --session/--approve 参数，
  保留计划指纹、RunSpec 指纹、预算和预览无副作用的断言。

**验证环境：** Windows / PowerShell，Python 3.12.14，项目虚拟环境；实际结果如下。

| 检查 | 实际结果 |
| --- | --- |
| 首批应用专项 | 22 passed，25.24 秒；之后补充规则变化、路径、跨 Coder 预算与独立真实冒烟入口 |
| 应用/计划/Codex/Worktree/初始化/工作流/模型记录/策略联合回归 | 331 passed、2 skipped，104.79 秒；两项为 POSIX 执行位与 macOS Worktree |
| 应用专项与真实冒烟入口 | 27 passed、1 skipped，69.86 秒；跳过为独立 P08 账号冒烟 |
| 初轮完整 pytest | 793 passed、24 skipped，380.31 秒；随后补充显式基线与 UTF-8 字节限额检查 |
| 最终完整 pytest | 795 passed、24 skipped，250.37 秒；本轮未出现 WMI 诊断 |
| Ruff / format / mypy | All checks passed；75 files already formatted；Windows/Darwin 各 54 个源文件通过 |
| 已安装 CLI 与文档 | agent.exe plan/run 预览、PlanVersion/RunSpec 指纹、预算、无执行副作用、status、帮助入口通过；git diff --check、UTF-8、代码块配对与本地链接通过 |

**未完成/未验证：** P08 真实账号与 macOS/POSIX 应用流程；24 项跳过分别为 18 项 macOS 集成、3 项 POSIX 专属、
3 项真实 API/账号冒烟，均不计为通过。新增真实账号入口默认跳过，
不会以本机假模型代替账号/服务兼容性验收。P03–P07 的既有平台/API/模型质量待办及 Windows WMI
未定位观察继续保留。P08 保持 IN_PROGRESS；下一阶段为 P09 实际验证及所需沙箱能力，
P10/P11/P12 的审查、重规划/累计预算和恢复/交付尚未完成。本次修改尚未提交或推送。

## 8.11 用户追加：.env 与智谱只读模型接入（2026-09-16）

**范围：** 用户要求按 CODING_AGENT_PROVIDER/API_URL/MODEL/API_KEY 创建 .env 并用于模型。
明确扩展 D06 原先单个 API Provider 范围；保留本地 Codex 编码引擎和工作流权威边界。
P08 未提交修改保留，P09 未推进，不宣称任何阶段 DONE。

- [x] 本地 .env 的 API Key 留空；加入 Git 忽略规则并提供 .env.example。
- [x] init/plan 显式 --env-file；环境变量覆盖文件，--model 覆盖模型。
  无该选项时保留原离线/OpenAI 行为，不把文件值写入进程环境。
- [x] 复用现有 SDK 增加智谱 Chat Completions，使用给定完整 Coding 地址及 glm-5.3。
  JSON object 配合本地严格校验；无工具循环、隐式重试、重定向或原始推理落盘。
- [x] SecretStr 和现有脱敏器保护密钥，配置错误不输出原始值；
  沿用请求先落盘、实际 usage 和失败分类。
- [x] 更新 README、规格 §32.3 与 D06，记录用户授权例外。
- [ ] 真实智谱账号、模型可用性与探索/规划质量；没有执行真实请求。

**过程记录：** 首次 mypy 发现环境变量可空值、Pydantic 动态构造及 SDK 联合类型问题，已修正。
首轮受影响回归 173 passed、2 个夹具错误：超长参数显示名影响 Windows 临时路径；
缩短显示 ID，保留超长输入和断言。随后 175 passed、1 failed：
新增测试把 Provider 类替换为函数，导致运行时类型注解求值错误；
改用真实 Provider 子类注入 MockTransport，产品契约未改动。
新增专项最终 32 passed（4.71 秒），覆盖配置优先级、旧离线行为、OpenAI 选择、
智谱请求/Schema/实际 usage、截断/无效响应、重试/重定向拒绝、超时、工具拒绝、
密钥脱敏和完整初始化失败留痕。
文档首次写入因 PowerShell 管道编码丢失新增中文，改用 Unicode 转义 JSON 重写并核对。

**验证：** Windows 完整 pytest 为 827 passed、24 skipped（164.82 秒），退出码 0；本轮无 WMI 诊断。
24 项跳过沿用既有 18 项 macOS、3 项 POSIX 和 3 项真实 API/账号测试，不计入通过。
完整输出保留于本机临时目录 coding-agent-dotenv-final-20260916.log。
Ruff、78 文件 format、Windows/Darwin mypy（56 源文件）通过；
已安装 CLI help 包含 --env-file，Git 确认忽略 .env；UTF-8、Markdown 代码块及 git diff --check 通过。未提交或推送。

**后续命名调整：** 用户要求统一项目命名，四个模型配置键改用 CODING_AGENT_。
本地 .env 仅迁移键名，保留既有值；同步模板、读取器、CLI 脱敏来源、测试及文档。
旧前缀不再读取。CODING_AGENT_CODEX_* 命名空间在模型 dotenv 中忽略，不导入进程环境；
现有优先级测试增加同文件 Codex 键，验证两类配置互不覆盖。
命名调整后配置/初始化/规划回归 128 passed（28.74 秒）；Ruff、78 文件 format、
mypy（56 源文件）、已安装 CLI help 与 git diff --check 通过。此次仅重跑受影响回归；
前一轮完整 827 passed、24 skipped 的结果不冒充命名调整后的全量验证。未提交。

**官方核对：** [Coding 端点](https://docs.bigmodel.cn/cn/guide/develop/gork)、
[对话补全](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)；
仅核对传输与 JSON 请求格式，不以模拟结果证明 glm-5.3 账号可用。

## 8.12 P09 开发中（2026-09-16）

用户明确授权 P09；起点 4da1c05、工作区干净。复杂度 large、风险 high：
验证进程能力、请求/结果留痕、真实快照、基线/最终检查和证据查询相互关联。
保留 P03–P08 的跨平台与真实账号待验收项；不推进 P10 审查实现或 P12 最终交付。

当前实现：按批准的 criterion/check 执行版本探测和验证命令，全部经过 ToolRuntime；
共享既有会话预算，拒绝没有配置的执行环境。JSON 证据工件保留版本、命令、工作目录、
实际退出码和输出引用。pytest/unittest 解析区分无测试、跳过、失败与无法确认；
通用测试包装器没有可确认的收集报告时不计通过。
重构先执行基线，失败不调用 Coder；任务检查通过仍需适用审查；
任务图通过后在最终快照保守重跑任务与需求级检查，需求整体完成仍为 false。
新增只读 agent evidence，核对证据工件与源 ToolResult，并比较当前计划/知识/源码快照。

沙箱：Windows 验证专用模式允许独立 scratch 写入及最多 16 个 Job 内进程，
保持源码/运行时只读、无网络与无进程脱离；Job 限制总内存 1 GiB，单进程 512 MiB。
首个真实 Windows 子进程测试已证明 scratch 可写，原项目、日志、禁读文件和网络均不可访问。
macOS 增加独立 scratch 写入；process-fork 仍禁止，需子进程的 macOS 验证仍受限，待实机复验。
测试数据库、联网依赖安装和源码目录中的构建写入不支持；记录不可用/失败，不能静默降级。

首轮原有回归 192 passed、19 skipped、3 failed；失败为 P08 旧断言仍要求空证据/无基线工作区，
已按 P09 契约调整为明确 unavailable 证据、审批前无操作、审批后基线失败仍保留原始字节。
首轮新增专项 23 passed、3 failed：Review 不可用被测试误纳入“测试全通过”；
查询把工作区父目录当成实际 Worktree；模拟证据写失败未毒化 writer。
分别修正测试分类、从已记录 prepare 结果读取实际路径、失败时停止后续操作并保留锁。
第二轮验证/执行联合回归 57 passed（83.09 秒）。真实 Windows unittest 成功和跳过先通过，
空收集实际退出码为 5，解析器原先会分类为 failed；改为先识别零测试并记录 inconclusive，
防止错误进入代码修复。随后新增篡改测试误解析普通输出为 JSON，以及取消测试漏导入已修正。
定向回归 20 passed（12.94 秒）；Windows/macOS 配置专项 6 passed（63.93 秒），包含真实 LPAC
临时目录、子进程隔离、成功/跳过/零收集，和主进程提前退出后仍等待整个 Job 的超时处理。
ToolResult 补充后端实际 process_argv/process_cwd，区分逻辑命令与 Windows 执行副本路径；
旧记录保留空启动字段；证据查询按原始已提供字段核对 RunSpec 指纹，避免新增默认字段改写历史身份。
第一轮全量 862 passed、24 skipped（282.67 秒），发生在实际启动字段及临时目录生命周期补测前，
不能代替当前代码最终回归。启动字段/篡改/取消定向 5 passed（38.52 秒）。

2026-09-17 Windows 临时目录补测发现实际能力缺口：原普通 scratch 写入通过，
但 TemporaryDirectory 创建时超时（首轮 153.12 秒；加诊断后 40.60 秒）。
单次探测确认 os.mkdir(0o777) 成功、os.mkdir(0o700) 返回 WinError 5；
Python mkdtemp 把该 PermissionError 当作名称冲突重试，表现为挂起。
CPython 3.12.4+ 的私有目录使用 protected DACL，仅含 SYSTEM/Administrators/Owner，
排除了 LPAC 身份。只增加 DELETE_CHILD 仍失败；临时授予 scratch 完全控制后虽可创建，
目录内写入及清理仍被拒绝。已撤回完全控制和 DELETE_CHILD 实验，保留最小读写执行/删除权限。
不修改 Python 标准库或绕开 LPAC；原私有目录创建/写入/清理成功断言保留在严格 xfail
能力测试中，失败明确计入未完成项，不能视为该能力通过。普通目录的创建、写入、删除、
子进程禁读原项目/控制记录/敏感文件及禁网断言继续正常执行。
这意味着 P09 尚不支持所有 pytest 插件、临时目录 fixture 或构建工具。

专项 35 passed、1 xfailed（88.15 秒）；Ruff check/format 与 Windows/Darwin mypy
均通过（60 个源码文件）。最终全量 862 passed、24 skipped、1 xfailed（352.34 秒），
日志 coding-agent-p09-full-20260917.log；24 项跳过仍为 macOS/POSIX 和真实账号/API
环境条件，1 项 xfail 是上述 Windows 已知能力失败，不计为通过。agent evidence --help、
文档 UTF-8/代码围栏及 git diff --check 通过。P09 保持 IN_PROGRESS。
日志位于本机临时目录 coding-agent-p09-verification-final.log、
coding-agent-p09-mkdir-probe.log、coding-agent-p09-mkdir-full-scratch.log、
coding-agent-p09-mkdir-delete-child.log。保留诊断失败，不以模拟结果代替平台实测。

官方核对：[AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)、
[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、
[Job limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)、
[CPython mkdir](https://github.com/python/cpython/blob/3.12/Modules/posixmodule.c)、
[Python 3.12 os.mkdir](https://docs.python.org/3.12/library/os.html#os.mkdir)。
本次尚未提交或推送。

### 8.12.1 P09 能力前置检查（2026-09-17）

起点 e07a015，工作区干净；用户继续授权 P09。复杂度 medium、风险 high：
复用已有基线、审批、沙箱、记录与证据门禁，不改隔离实现，不推进 P10。
核对 [CPython #134587](https://github.com/python/cpython/issues/134587) 与
[PR #148804](https://github.com/python/cpython/pull/148804)，均仍开放，未发现可直接采用的已合并修复。
保留上节兼容失败；不把重试超时的诊断改进声称为临时目录能力修复。

新增 examples/python-private-temp-check.json，作为完整草稿中单个 command 检查的模板：
直接调用一次 os.mkdir(0o700)，验证读写/清理，用既有 baseline_check_ids 在 Coder 前运行。
无需新增领域字段或配置开关；必须关联 criterion，保留业务测试和预算。
Planner 提示词要求记录必需能力的未知项或基线探测；模型提示不是已验证能力。
run 的基线阻塞原因现在包含 check ID、证据状态和原因，避免只返回笼统失败。
已有计划的验收修订仍受 P11 限制，文档禁止用新计划绕过活动会话授权或预算。

真实 Windows 测试确认非重构计划也能使用该基线：版本探测成功、能力命令立即失败
（PermissionError、非超时）、Evidence 为 unavailable、Coder 未调用、原源码和 Worktree
字节未改动，现有业务检查保留。受影响回归 3 passed（12.14 秒）。
首轮静态检查只发现新测试一行超长，已用 Ruff 格式化。
最终全量 863 passed、24 skipped、1 xfailed（351.46 秒）；
日志位于本机临时目录 coding-agent-p09-preflight-full.log，定向日志为
coding-agent-p09-capability-baseline.log。既有跨平台/真实账号跳过项和 Windows 已知失败保留。
Ruff check/format、Windows/Darwin mypy（60 个源码文件）、模板 Schema/Python 语法、
文档 UTF-8/代码围栏和 git diff --check 均通过。未执行真实模型调用。
原 TemporaryDirectory 成功断言及严格 xfail 保留，P09 仍为 IN_PROGRESS；本次尚未提交。

### 8.12.2 Open Code Review 适配评估（2026-09-17）

起点 d363943，工作区干净；用户仅授权先评估适配程度，不实施 P10。
官方源码核对范围、固定提交及 Windows v1.12.4 离线冒烟结果见
[OPEN_CODE_REVIEW_ASSESSMENT.md](OPEN_CODE_REVIEW_ASSESSMENT.md)。
Delegation 的 preview/rule JSON 命令实测通过；默认筛选排除 README.md，
接入时必须独立核对必需审查范围。完整 review 源码已有 manifest，
但部分完成可退出 0，且逐次持久记录、统一预算及当前沙箱存在适配差异。
建议优先验证 Delegation 的受控执行边界，再决定实施；本次只新增评估文档。
未执行真实模型调用、OCR LPAC 运行或 macOS 实测，不声称质量、成本优势；
P09 既有未完成项保留，P10 保持 NOT_STARTED。

### 8.12.3 Delegation Windows 执行边界探测（2026-09-17）

用户授权继续最小接入验证；保留上节未提交文档。复杂度 medium、风险 high，
本次仅开发诊断与离线实测，不扩展产品权限、不接入 Reviewer。
新增 examples/ocr_delegation_probe.py，复用现有 Windows 验证后端，
对人工 Git 仓库运行官方 v1.12.4 的 version、delegate preview、delegate rule。
三者均退出 2：剪贴板依赖包初始化加载 user32 失败，尚未进入 Git 或规则逻辑。
二进制摘要一致，3 组请求/结果完整记录，未超时或截断，原样例仓库指纹未变。
记录位于本机临时目录 coding-agent-ocr-probe-20260917-final/records/probe.jsonl。
能力探针退出 1 明确表示不可用，不计为验收通过；未关闭 Win32k、放宽 ACL 或回退宿主执行。
初次临时探针漏填 risk，执行前校验失败后补全；脚本两行超长经格式化修复。
脚本 Ruff 检查/格式检查通过，文档校验及 git diff --check 通过；无产品代码变更，
不重跑全量测试。未执行真实模型、修改版 OCR 构建或 macOS 原生测试。
评估已改为先验证无交互依赖的离线入口，必要时只复用规则资产；
Git 执行和后续审查能力未验证。P09 既有未完成项保留，P10 保持 NOT_STARTED。

### 8.12.4 OCR 离线规则入口验证（2026-09-17）

用户继续授权最小适配验证，保留前两次未提交评估与探针。
在临时目录使用校验过的 Go 1.25.5 工具链，从固定上游提交原样复用
rules.LoadDefault / delegate.GroupRules，构建无 CLI/TUI 初始化的独立入口。
新增 examples/ocr_offline_rules 的入口、单元测试及构建说明；未增加项目运行依赖。
复杂度 medium、风险 high；原型只提供内置规则，不接入模型或产品状态流程。

真实 Windows 单进程只读 LPAC 探测通过：冻结路径列表全部得到规则，
Python 规则与官方 CLI 输出逐字一致；路径穿越、控制路径、未知 schema、
超大输入均退出 1，未超时/截断，样例仓库指纹未变。
使用 verification=False，未开放 scratch、Git runtime、网络或子进程。
调用前请求及实际结果保留在临时目录
coding-agent-ocr-headless-qmj8tvnw/native-final/records/probe.jsonl。
新入口 Go 单元测试 2 passed（含 7 类非法输入及分组覆盖）；构建成功。
首轮对照脚本误用默认编码读取工件，改用 UTF-8 后重跑通过，失败记录保留。

尚未验证完整 Delegation 筛选、自定义规则/内容识别、macOS 与真实审查质量。
官方完整 exe 的启动失败不视为已修复；本次只证明裁剪入口可运行。
后续产品化可选择固定规则资产或该入口；仍需 Reviewer 上下文、规则优先级、
调用留痕、身份与完成门禁适配。P09 已知失败保留，P10 仍为 NOT_STARTED。

### 8.13 P10 规则来源与上下文接口（2026-09-17）

用户授权继续接入规则来源；保留此前未提交评估/原型。复杂度 medium、风险 high：
只新增只读规则操作和补充上下文接口，不修改沙箱或放宽完成门禁。
选择固定规则资产，避免生产引入 Go/完整 OCR CLI；保留 Apache-2.0 许可、来源及转换声明。
上游固定提交 4e59c7e815bde045158b549cdc2a7f58a32f6ba0；52 份原样规则文档，
顺序路径映射保留，花括号选项预展开后复用既有 glob_matches。
172 个路径与上游 Go 原型逐字输出的 SHA-256 对照一致，测试夹具保留来源和期望摘要。

ReviewRulesOperation 经 ToolRuntime 权限、共享预算和先记录后读取流程；
每次校验固定规则包及请求摘要，无缓存绕过资产变更。输出记录上游提交、文档路径/
摘要及补充属性，不能作为 Evidence。attach_review_guidance 保留项目显式规范，
绑定原任务上下文及请求事件，拒绝过期、截断、覆盖缺失和规则版本不符。
公共 API、package-data、规格与 README 同步；Coder 工具列表不变。
该上下文接口尚未接入 agent run 的 Reviewer 调度，不提前声称完整独立审查可用。

定向测试 19 passed（14.10 秒），覆盖来源对照、权限、预算、日志故障、
资产丢失/损坏/版本变化、上下文与项目规则保留，以及读取期间代码变化。
P10 开始规则来源这一子步骤，状态 IN_PROGRESS；其四项完整验收清单仍未完成。
P09 的 Windows 私有临时目录失败、macOS/真实账号待验证项继续保留。

最终验证：全量 882 passed、24 skipped、1 xfailed（287.82 秒），日志位于
本机临时目录 coding-agent-review-rules-full.log；既有平台跳过和私有目录失败保留。
Ruff check/format、Windows/Darwin mypy（63 个源码文件）通过。
规则/LICENSE/NOTICE 的 wheel 打包与独立 zip 导入读取通过，工件位于本机临时目录
coding-agent-rule-wheel-12wbyadx/wheel/verified_coding_agent-0.1.0-py3-none-any.whl。
构建检查初因虚拟环境缺少 setuptools 失败，改在临时目录下载并校验 setuptools 80.9.0，
未修改项目环境或全局安装。初次 Diff 检查发现 README 末尾多余空行，已移除。
文档 UTF-8、代码围栏、来源对照与 git diff --check 通过；未调用真实模型。
本次未提交或推送。

### 8.14 P10 独立审查与 run 调度（2026-09-17）

用户授权继续；复用 ModelRuntime、ToolRuntime、Workflow Engine 和 QualityGate，
新增最小 ReviewDraft/ReviewRunner，无新依赖。复杂度 medium、风险 high：
本次改变必需审查可用性与最终门禁，仍由运行时生成身份及记录，模型不能写权威状态。

独立上下文包含需求、任务、累计 Diff、当前源码、全体显式项目规则、补充 OCR 指导、
实际验证证据以及重构不变量/历史基线，不传 Coder 对话或工具能力。
新增和删除路径纳入覆盖；64 路径/512 KiB 与既有读取限制同时生效，截断即阻塞。
Schema 要求 blocking/major/minor、结论、路径/规则/维度覆盖及文档判断；
模型声称通过但有 blocking/major 仍失败，伪造 Evidence 字段或不完整覆盖不能通过。
覆盖声明不能证明真实模型理解正确，质量验收需单独实测。

agent run 新增 --review-env-file/--review-model，编码 --model 语义不变；
API 接受 reviewer=ModelProvider。供应商、生成配置、端点摘要和规则包参与执行指纹。
任务验证通过后审查，最后对全部业务任务和全局验收按最终快照重验/重审；
final_reviews 保存最终结果，失败 run=blocked，历史任务状态不等于需求完成。
review_context_built、模型请求/结果、review_recorded 保留上下文、摘要、阶段与身份。
调用共享已有预算，记录失败与取消不转换成成功；未配置 Reviewer 仍 unavailable。

专项测试覆盖独立上下文、完整覆盖、伪造字段、major 阻塞、过期、截断、
新增/删除、预算耗尽、Provider 配置变化、日志故障、取消、最终重审、
CLI 配置分离、重构历史基线。首次重构测试错误地修改已有计划的不变量，
触发既有 P07 保护；改为在未执行的临时项目创建独立计划，未削弱保护。
初次回归命令误用了不存在的测试文件，纠正后完成回归。
专项前 22 项通过，新增重构/取消 2 项通过；相关回归 116 passed、1 xfailed。
完整验证：906 passed、24 skipped、1 xfailed（397.40 秒），日志位于本机临时目录
coding-agent-p10-full.log。Ruff check/format、Windows/Darwin mypy（65 个源码文件）、
CLI 帮助、文档 UTF-8/围栏及 git diff --check 通过。

未调用真实模型、未执行 macOS 原生验证；保留 Windows 私有临时目录严格 xfail。
P10 保持 IN_PROGRESS，不推进 P11/P12，不提交或推送。


### 8.15 P10 真实模型与 Windows 验证联调（2026-09-17）

用户明确告知已配置真实模型并授权继续，读取 .env 的智谱 glm-5.3 配置。
新增显式启用的 tests/test_reviewer_live.py，复用临时项目、固定 Coder 补丁、
实际 Windows LPAC unittest、ReviewRunner、最终快照门禁及日志；无新依赖。
每轮上限 4 次 API 调用、每次 8192 输出 token/60 秒，不自动重试；
max_review_fixes=0 用于固定本冒烟的调用预算，不改生产默认预算。

保留诊断过程：初次两个请求均收到 HTTP 429，无模型结果，正确记录 unavailable。
HTTP 429 的具体供应商原因未取得，不能据此认定余额不足。
初版测试错误地读取工作区容器而非返回的 worktree 路径，已修正。
后续诊断调用恢复成功：任务/最终任务审查 passed，全局重审漏报覆盖，
运行时以 inconclusive 阻止完成。明确提示完整路径、规则、四维度输出后，
两个场景 2 passed（51.46 秒）：正常改动三次审查 passed（14749 总 token），
回归审查识别 service.py 返回值从 1 改为 2 的 blocking（5177 总 token），
即使实际测试通过仍返回 replan_required（修复预算耗尽）。

人工核对另发现模型将既有弱测试误称为本次削弱。提示词补充“历史变化必须有
Diff/历史源码依据”，追加回归 1 passed、1 deselected（19.14 秒，5364 总 token）；
该次结果明确区分既有测试缺口与本次返回值回归。未降低覆盖检查或修改预期来放行。
有限样例不证明模型持续准确，也未对完整 OCR 引擎做审查质量对比。

最终成功场景和历史归因复验的报告、journal、上下文/模型工件副本保存在本机临时目录
coding-agent-p10-live-evidence-2rtzoo_m，manifest.json 列出三个报告。
原始报告内 journal 路径指向 pytest 临时目录；长期核对使用副本目录 records/events.jsonl。
初次失败与诊断日志分别是 coding-agent-p10-live.log、
coding-agent-p10-live-diagnostic.log；成功/归因复验日志为
coding-agent-p10-live-final.log、coding-agent-p10-live-history.log。
pytest 会轮换旧临时目录，早期失败完整工件未保留，不将日志当作成功证据。

离线 Reviewer 回归 24 passed（97.97 秒），默认真实入口 2 skipped；
Ruff check/format、Windows/Darwin mypy（65 个源码文件）通过。
本轮仅增冒烟和调整提示词，未重复之前 906 passed 的全量回归。
P10 保持 IN_PROGRESS：代表性项目质量、macOS、真实 Codex 全链路仍待验收。
原有 Windows Python 私有目录严格 xfail 保留；未提交或推送。
提交检查发现 core.autocrlf=true 会改变规则包字节；新增 .gitattributes 固定 rules.json 为 LF，
验证暂存内容及 Windows Git 检出后的 SHA-256 与固定摘要一致。

### 8.16 AutoResearch X 渠道真实对照评估（2026-09-17）

用户授权在独立基线副本使用真实 Codex + GLM 评估，原 AutoResearch 修改未改写。
完整报告见 [AUTORESEARCH_X_EVALUATION.md](AUTORESEARCH_X_EVALUATION.md)。

原版运行 19.97 秒，Codex invalid_response，尚未修改代码。Schema 检查发现可空 replan 未列入
required；复用已有 OpenAI SDK 严格 Schema 转换，补充实际 CLI 请求断言，无新依赖。
定向 Codex 协议测试 55 passed，Ruff check/format、mypy（65 个源码文件）通过。
修复后独立复验保持需求、预算和权限，24.56 秒后 blocked：模型报告 code-mode host is disabled，
没有动态工具调用/patch。真实合成冒烟也受阻；未放开原生工具或进程权限。
两轮日志各 40 条事件，均无未配对请求；工作流未进入验证或 GLM 审查。

Windows 基线 pytest 预检在 AnyIO 插件导入 asyncio/_overlapped 时 WinError 10013，
不能计为测试通过；与已有私有临时目录 0700 问题分别保留。
宿主基线 85 passed、参考实现 106 passed，Ruff/compileall/secret_scan 通过，均非产品 Evidence。
另做一次真实 GLM 参考实现只读评估：无 blocking/major，但必审路径拼写错误，
覆盖校验不通过，不能采纳模型 passed；没有转化为工作流成功证据。
已知用量 150038 token（含修复后 Codex 业务任务/合成冒烟和 GLM），两次失败调用用量未知，
参考实现开发成本未知，不能作成本或质量优势结论。

保留全部失败及试验记录，不重置旧会话预算；本次未提交/推送。
P10 保持 IN_PROGRESS，下一步先解决真实 Codex 工具路由和 Windows 验证兼容，再复验全链路。
未运行真实 X API 或 macOS；P11/P12 不推进。

### 8.17 兼容 Codex 模型与候选对照（2026-09-17）

本机 Codex 模型目录确认 gpt-6-astra / gpt-5.6 系列要求 code_mode_only；
保持原权限配置，选择 gpt-5.5 的直接动态工具路径，真实读写冒烟 1 passed（14.61 秒）。
不等于 gpt-6-astra 的 code-mode 已适配。README 同步实测版本与兼容边界。

同基线第三轮试验使用相同需求、预算和 300 秒时限：304.05 秒超时 blocked，
6 个文件有实际改动，测试/文档未完成；102 条事件、29 个工具请求、17 个模型调用段，
日志无未配对请求，未进入工作流验证/审查。保留副本、Diff 和全部失败历史，未重置旧会话。

候选宿主检查 85 passed（原有测试）、Ruff 1 项 E501、compileall/secret_scan 通过。
相同离线行为探针参考实现 8 passed，候选 2 passed/6 failed，涉及部分错误、数量不符、
非法身份、空正文、重定向及异常链泄露模拟文本。探针为事后诊断，不是盲测模型排名。
补充 GLM changes_requested 与主要失败方向一致，但路径覆盖仍不完整，不能成为有效通过证据；
输入包含已知失败摘要，不声称模型独立发现了全部问题。

Windows 去掉 AnyIO 插件的独立诊断可收集 85 项，但私有目录仍 WinError 5。
新增 examples/python-asyncio-check.json，复用 required baseline，在 Coder 前阻塞不支持的环境；
2 项基线测试通过，原私有目录严格 xfail 保留。该改动不修复底层兼容性，不放宽禁网。
Ruff check/format、Windows/Darwin mypy（65 个源码文件）通过，未重复全仓 pytest。

详细事实与工件见 AUTORESEARCH_X_EVALUATION.md 第 9 节。
累计已知 182698 token，不含失败无用量及超时业务任务；不作成本优势结论。
原项目摘要一致；未提交/推送。P10 保持 IN_PROGRESS，先处理 Windows 验证与计划预算再复验。

### 8.18 计划调用预算与 Windows 后端路线（2026-09-17）

PlanSettings 新增 max_tool_calls / max_model_calls，agent plan 对应参数默认 30 / 31，
正整数校验、持久化、摘要与 RunSpec 编译已接通，非默认值绑定审批指纹。
默认字段不写入序列化，保留旧计划指纹；3 份真实评估历史计划指纹逐一一致。
沿用修订不能改变预算的限制，不修改任何旧会话预算，不实现 P11 恢复。
计划/执行检查 86 passed；工具/模型耗尽用例改为使用保存计划中的额度，2 passed。
完整 pytest：919 passed、26 skipped、1 xfailed（435.40 秒），跳过不计通过，
Windows 私有临时目录严格 xfail 保留。Ruff check/format、Windows/Darwin mypy
（65 个源码文件）、git diff --check 均通过；未重跑真实模型或宣称业务全链路通过。

Windows 方案见 WINDOWS_VERIFICATION_PROPOSAL.md，Docker 后端尚未选择、实现或安装。
CPython 3.12.10 的 0700 mkdir 使用受保护且不继承父目录的 ACL；原 LPAC 阻塞保留。
额外原生 ACL 对照探针分别在 ctypes 导入和 MinGW 程序启动时失败，未取得 ACL 对照结果。
P10 仍为 IN_PROGRESS；原项目不改写，本轮未提交/推送。

## 9. 下一步执行单元

当前安排（2026-09-17）：按用户要求暂停 Windows 适配，列为下方 TODO。
此前开发记录中的 Windows 优先推进顺序已被本安排替代；后续“继续”不自动恢复该项。

1. P06 真实探索需在代表性项目上显式运行 agent init PATH --refresh --model MODEL 并核查来源质量。
   在专用 CODEX_HOME 登录，配置 CODING_AGENT_CODEX_HOME / CODING_AGENT_CODEX_MODEL，
   显式执行 Codex 真实冒烟；OpenAI API 冒烟仍需 OPENAI_API_KEY / OPENAI_MODEL。
   两条路径分别记录，缺少配置或跳过均不计为通过。
2. 用代表性项目显式执行 agent plan --model MODEL，人工核对需求覆盖、风险/复杂度、
   来源规则、当前任务与后续里程碑；离线 Schema 测试不能代替真实计划质量验收。
3. 在 macOS/POSIX 复验 P03/P04、共享记录、Codex 进程管理及 P06/P07 初始化/计划流程。
   Windows WMI 诊断并入暂停 TODO，保留现有未定位记录。
4. 显式运行 CODING_AGENT_RUN_LIVE=1 对应的 P08 应用冒烟，确认专用账号下的上下文、
   真实修改与缺少证据仍阻塞；同时在 macOS/POSIX 复验 P08。
5. P09 验证、基线、最终重跑及 Evidence 查询已接入；后续补 macOS 实测及其他非 Windows 必需验证能力。
   Windows 验证适配按下方 TODO 暂停。不得以扩大宿主权限或改写运行时语义绕过。
   保留上述未验证项与严格 xfail，不提前声称阶段 DONE；P10 已接入独立审查，仍需真实模型质量与 macOS 原生验收。
   Claude Code、Pi 需要先证明相同工具边界和留痕能力，不能直接开启不受控的原生工具。


### 9.1 TODO：Windows 验证适配（已暂停）

- [ ] 恢复条件：用户明确要求恢复 Windows 工作；普通“继续”不视为恢复。
- [ ] 处理 LPAC 下 asyncio/_overlapped 导入失败（WinError 10013）。
- [ ] 处理 Python 0700 私有临时目录权限失败（WinError 5），保留严格 xfail。
- [ ] 恢复后再选择原生 LPAC 或可选 Docker 验证后端，明确网络隔离语义并完成真实验收。
      [Windows 方案](WINDOWS_VERIFICATION_PROPOSAL.md)仅保留为候选；当前不再等待路线选择，
      不开发该后端、不安装 Docker/WSL 等系统组件。
- [ ] Windows WMI 诊断及其他原生兼容性复验一并暂停。
- [ ] 环境能力通过后，再恢复依赖该环境的 AutoResearch 真实 Codex + GLM 全链路复验。

暂停不删除既有实现、测试、失败记录或验收要求；Windows 全链路仍未通过，
P10 保持 IN_PROGRESS。与 Windows 无关的工作可继续按阶段依赖推进。
