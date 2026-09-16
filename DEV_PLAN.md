# Verified Coding Agent — 开发计划

> 版本：V1 / 2026-09-16 / P03 Windows 适配修订
> 依据：[开发规格](CODING_AGENT_DEVELOPMENT_SPEC.md)，重点参考 §16、§24、§29–31、§35–39、§42、§44–48。
> 当前状态：P01/P02 已完成；P03 安全修复与 Windows 适配已完成本机验证；macOS 集成复验待完成，状态为 IN_PROGRESS。本次范围仅 P03，P04–P12 尚未开始。

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
| 技术栈 | Python 3.12+、Pydantic v2、asyncio、Git CLI、subprocess；Windows 只读 Git 使用 Dulwich；CLI 阶段引入 Typer；检查使用 pytest、ruff、mypy |
| 源码布局 | 使用 `src/coding_agent/` 包；在包内保持规格要求的 core、agents、runtime、tools、verification、context、session 等职责边界 |
| 建目录方式 | 当前阶段需要时再建立文件和目录，不预建规格 §33 的全部目录树 |
| 执行方式 | V1 同时运行一个业务任务；不实现并行调度或远程 Worker |
| 模型 | P05 仅接入一个用户可用的 Provider；单元测试和故障验证使用 Fake，不依赖 API 凭据 |
| 持久化 | 先使用规格中的 JSON 状态快照、版本化计划、JSONL 事件/证据及工件目录；有明确需求时再引入 SQLite，避免两份可写状态真相 |
| 验证策略 | 先支持显式配置和已识别的仓库命令；自动发现不能把未执行命令当作通过 |
| 复杂度 | 分别记录 small/medium/large 与风险等级；通过 Explorer/Planner 评估范围、耦合、未知项和验证难度，决定单任务、任务图或分阶段执行 |
| 质量承诺 | `VERIFIED` 必须有适用于当前计划及代码快照的必需证据；实现结束和交付成功分别记录 |

不实现 Web UI、多用户、复杂 RAG、AST/LSP 图谱、Agent 团队、分布式调度、自动 PR 或复杂 MCP 生态。执行后端集成和竞品评测不构成重建这些产品的要求。

大型任务先验证单仓库、多模块、具有回归测试的重构。以明确的行为不变量作为验收条件，建立基线，再执行有界批次与最终集成验证。后续里程碑作为版本化计划中的待完成范围保留；当前批次完成不能代表整个需求完成。复杂度元数据在 P07 加入计划，不提前扩充 P01 或新增独立分类 Agent。

## 3. 开工前需要统一的规格事项

以下保留统一事项的原问题、结论与后续待办，不静默覆盖原规格。D01、D02 已在 P01 解决，D03 已在 P02 解决，D05 已在 P03 解决；其他事项在对应阶段先记录结论并同步冲突章节，再实现相关模型或行为。能由现有需求确定的实现选择自行处理；只有影响用户目标、授权或交付范围的未决问题才需要用户回答。

| 编号 | 最迟阶段 | 现有问题 | 建议与完成条件 |
| --- | --- | --- | --- |
| D01 | P01 / 已解决 | §10 将 Tests/Review 画为后续任务，§11 又要求每任务验证后才能解锁下游 | 已同步 §10–11：业务任务包含实现、必需测试和适用审查；交付前另做全局集成验证。仅 VERIFIED 依赖解锁下游；图不持有可写运行状态 |
| D02 | P01 / 已解决 | §7、§9、§29 的 Risk 示例结构不同；AcceptanceSpec 缺少统一模型 | 已同步 §7、§9.1、§23–24、§29.1：统一 RiskProfile，总等级覆盖已评估维度；AcceptanceSpec 使用稳定 criterion/check ID；Evidence 显式记录来源、结果、时间和计划/上下文/工作区版本 |
| D03 | P02 / 已解决 | FAST 的精简流程与总体 Review、§39 两次审批要求关系不清 | 已同步 §11–12、§25–28、§31、§37、§39：所有模式保留声明检查及计划/交付授权边界；FAST 仅低风险且未声明审查时省略 Reviewer，仍经过 REVIEWING 和门禁；中/高风险分别至少 STANDARD/STRICT；完整 RunSpec 指纹匹配的授权在任务/修复间复用，交付授权单独处理 |
| D04 | P04 | §18、§37 Phase 4、§49 对 Worktree 所属版本有不同表述 | 按 §37 将顺序执行所需的 Worktree 能力纳入 P04，任务并行保持后续范围；开工时统一相关章节和工作区生命周期 |
| D05 | P03 / 已解决 | `network: false`、受限 shell 和控制记录保护缺少具体执行边界 | 已同步规格 §20–21.1、§30–31：原生文件工具使用 POSIX 目录描述符或 Windows 无跟随句柄；进程使用 macOS 只读沙箱或 Windows LPAC/Job 及受控执行副本，写入通过 Patch；不支持的权限/平台明确拒绝。数据库权限按资源访问定义，测试数据库与需写入/子进程的验证后端仍是 P09 前置条件；不能用命令前缀或 Worktree 声称隔离 |
| D06 | P05 | 尚未选择真实 Provider 与可用凭据 | 选择一个可用 Provider，并定义结构化输出、工具调用、超时及用量返回；缺少凭据时继续离线实现，明确记录真实接入尚未验证 |

## 4. 阶段总览

状态仅使用 `NOT_STARTED`、`IN_PROGRESS`、`BLOCKED`、`DONE`。P01/P02 已完成；P03 安全复核后重新进入 IN_PROGRESS；`DONE` 必须附交付物与检查证据，见 §8 的阶段验收记录。

| 阶段 | 依赖 | 主要交付 | 状态 |
| --- | --- | --- | --- |
| P01 领域基础 | 无 | 领域模型、DAG 校验、状态转换约束、基础工程配置 | DONE |
| P02 工作流引擎 | P01 | 单任务调度、状态机、QualityGate、Fake 完整生命周期 | DONE |
| P03 工具与执行记录 | P02 | PolicyEngine、Tool Runtime、持久事件、工件记录 | IN_PROGRESS |
| P04 工作区 | P03 | Git 状态/差异/快照、Worktree、受控恢复与清理 | NOT_STARTED |
| P05 模型适配 | P04 | Provider 接口与一个真实实现 | NOT_STARTED |
| P06 Explorer / init | P05 | 项目梳理、用户规范、项目知识与增量刷新、初始化 CLI | NOT_STARTED |
| P07 Planner | P06 | 复杂度评估、PlanDraft 校验/编译、版本化分阶段计划、规则引用、规划 CLI | NOT_STARTED |
| P08 Coder | P07 | 受约束的工具调用循环、真实修改与完整留痕 | NOT_STARTED |
| P09 Verification | P08 | 测试/构建/静态检查、结果解析、版本化 Evidence | NOT_STARTED |
| P10 Reviewer | P09 | 独立上下文审查、结构化问题、规范与文档检查 | NOT_STARTED |
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

- [ ] 处理 D04，明确主仓库、会话工作区和 Worktree 的使用及清理规则。
- [ ] 实现 prepare/status/diff/snapshot/reset/cleanup；区分用户原有修改与本次任务改动。
- [ ] 快照包含相关未提交和未跟踪输入；排除执行日志及验证生成物造成的自我失效。
- [ ] 使用临时 Git 仓库验证 Worktree 和恢复；文件删除或重置前核实目标与归属。

**验收：** 快照能发现实际源代码变化；失败、恢复、清理不丢失用户修改；保留可检查的差异工件。Worktree 只代表代码工作区隔离，不等同于进程或网络隔离。

### P05 — 模型适配

- [ ] 处理 D06，实现规格要求的 ModelProvider 接口与一个真实 Provider。
- [ ] 支持结构化输出、工具请求/响应关联、超时、错误分类和可用的用量数据。
- [ ] 使用 Fake/模拟响应验证无效结构、工具调用参数错误、限流、超时和中断。
- [ ] 有可用凭据时执行一次有界真实冒烟，记录模型与配置；将其与离线测试结果分开报告。

**验收：** 无效模型结果不能直接进入工作流状态或绕过工具权限；业务层不依赖某个 Provider 的原始响应结构。真实接入未验证时不能宣称该交付已完成。

### P06 — Explorer 与项目初始化

- [ ] 保持 Explorer 只读；输出 RepoSummary、重要模块/调用关系、验证命令及来源。
- [ ] 针对当前任务补充范围、耦合、关键未知项和验证环境信息，为复杂度评估提供源码依据。
- [ ] 区分代码事实、明确规则和未确认假设，收集用户补充的开发约定和模块交互约束。
- [ ] 控制器保存 `.agent/project.md` 与修订元数据；复用已有 AGENTS.md 等资料。
- [ ] 提供 `agent init`、`agent init --refresh` 和首次 plan/run 共用的初始化流程。
- [ ] 刷新变动来源，保留用户内容；非交互场景遇到必要决策时返回可操作的说明。

**验收：** 能解释代表性模块关系并指向源码；重复 init 不覆盖用户规则；来源变化可检测；未执行的验证命令没有“通过”记录；无需完整调用图即可完成初始化。

### P07 — Planner 与可执行计划

- [ ] 编译 RequirementContract + RepoSummary + 项目知识为 PlanDraft，再校验为 TaskGraph。
- [ ] 在计划中记录 complexity、reasons、unknowns、execution_strategy 及支持来源，复杂度与 RiskProfile 分开处理。
- [ ] 为 Large 任务保留整体里程碑及待完成范围，详细编译当前批次；每个业务任务包含自己的验收，后续不确定任务不能直接调度。
- [ ] 校验依赖、任务范围、验收完整性与规则引用；启发式判断和确定性结构校验分别处理。
- [ ] 保存计划版本、稳定任务/criterion ID、起始工作区和上下文修订；显示可读范围与验证摘要。
- [ ] 提供 `agent plan`、保存计划导入校验，以及 graph/status 的当前阶段能力。

**验收：** 非法图不能进入执行；计划先保存再执行；旧计划不被覆盖；导入计划检查来源变更与适用授权。缺少必需信息时产生明确待决项。覆盖低复杂度/高风险组合、未知依赖与分批计划；不得因 Small 降低必要验证，也不得因当前批次全部 VERIFIED 就忽略后续必需范围。

### P08 — Coder 与首次真实修改

- [ ] 实现有步数、时间及资源边界的模型—工具—观察循环。
- [ ] 使用当前任务、计划和知识修订构建上下文；全部动作经过 Tool Runtime。
- [ ] 返回 ImplementationResult 或显式 replan 请求；真实文件列表和命令记录由运行时产生。
- [ ] 发现额外调用方、共享状态或范围扩大时提交复杂度重评估请求，不等连续失败后才纠正计划。
- [ ] 提供 `agent run` 与 diff/history 的可用进度查看；在受控样本仓库完成一次实际修改。

**验收：** 所有动作能关联任务和计划；模型自述与真实结果不符时采用真实记录；达到边界时停止调度并保留工件。此阶段尚不能把缺少后续验证/审查的任务标为 VERIFIED。

### P09 — Verification 与 Evidence

- [ ] 核实验证命令所需的写入、子进程与测试数据库能力；扩展并验证 P03 后端，能力不可用时阻塞，不能跳过必需检查或静默放宽权限。
- [ ] 执行适用的 test/lint/type-check/build 命令，记录工作目录、退出码、工具版本和输出引用。
- [ ] 映射到稳定 criterion/check ID，区分 passed、failed、skipped、unavailable、inconclusive。
- [ ] 绑定计划、上下文和实际代码快照；实现必需证据缺失及过期检查。
- [ ] 提供 `agent evidence`；初版用保守重跑完成最终快照的验证。
- [ ] 为重构在结构性实现修改前执行基线检查，记录已有失败与行为不变量；每批检查后保留最终集成验证要求。

**验收：** 覆盖无测试收集、跳过、环境不可用、失败与验证后代码变化；它们不能被错误计作通过。检查从哪版代码得到、覆盖哪个条件均可查询。

### P10 — 独立 Reviewer

- [ ] Reviewer 使用需求、任务、Diff、相关源码、规则和证据，不接收完整 Coder 对话。
- [ ] 输出结构化 blocking/major/minor 问题及结论，并绑定计划/代码版本。
- [ ] 检查规则遵循、需求遗漏和文档维护需求；区分审查判断与真实测试结果。
- [ ] 审查重构是否保持约定接口、结果、错误语义和相关副作用，不能仅以文件移动完成或新增测试通过判断成功。

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

## 9. 下一步执行单元

Windows 适配已在本机完成验证；下一步在 macOS 对当前 P03 代码完成真实集成复验并更新阶段状态。P03 验收通过且获得实施授权后，再推进 **P04：工作区与代码快照**：

1. 处理 D04，统一主仓库、会话工作区和 Worktree 的生命周期及版本归属。
2. 实现 prepare/status/diff/snapshot/reset/cleanup，区分并保留用户原有修改。
3. 快照覆盖相关已修改和未跟踪文件；排除控制日志与验证生成物，不能只使用 Git HEAD。
4. 通过 Tool Runtime 记录 Git/恢复副作用；Worktree 不代替进程或网络隔离。
5. 在临时仓库验证失败、恢复和清理不丢改动；未授权前不推进 P04。
