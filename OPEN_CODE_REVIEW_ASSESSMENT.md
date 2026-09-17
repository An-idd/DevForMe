# Open Code Review 适配评估

日期：2026-09-17。范围：评估 P10 Reviewer 的复用方案；未实施接入。

## 结论

**值得复用；裁剪后的离线内置规则入口已通过严格 Windows 沙箱验证，完整官方 CLI 仍不兼容。**

Delegation 复用文件筛选和规则解析，模型/工具调用、独立 Reviewer 上下文及门禁由本项目承接。它不包含完整 OCR 的审查循环、反思和评论后处理，不能据此声称获得相同审查效果。若目标是完整复用这些能力，应选择完整引擎接入并承担调用边界改造。

下表是工程判断，不是测试通过率、审查质量评分或工期承诺。

| 维度 | 完整 review CLI | Delegation + 本项目 Reviewer |
|---|---|---|
| 现成能力复用 | 高：完整审查流程 | 中：筛选、分组和规则 |
| Python 接口 | 中：Go 核心在 internal，CLI 是自然边界 | 较高：两个命令提供版本化 JSON |
| 需求、规则、证据输入 | 中：background 需由宿主整理 | 高：宿主直接组织独立上下文 |
| 覆盖与结果 | 较高：manifest 和结构化评论可映射 | 中：宿主记录实际完成范围 |
| 调用前持久记录 | 低：现有日志不是宿主逐次执行门禁 | 较高：模型和工具可留在现有运行时 |
| 统一预算 | 低：内部轮次、重试与宿主计数不同 | 较高：可沿用宿主计数；P11 会话预算仍待完成 |
| 当前 Windows/macOS 沙箱 | 低：Git 子进程、网络、会话写入均需适配 | 中：无需 OCR 模型调用，但仍依赖 Git |

## 核对版本与实测

- 源码固定提交：`4e59c7e815bde045158b549cdc2a7f58a32f6ba0`。核对 CLI、Delegation、规则、manifest、持久化、模型/工具循环及 Windows 进程处理；不是完整安全审计。
- Windows 普通进程冒烟使用官方 [v1.12.4](https://github.com/alibaba/open-code-review/releases/tag/v1.12.4)，二进制报告提交 `f1101fd7f`。源码与发布版本分别记录。
- 官方 Windows amd64 二进制 SHA-256 与 GitHub asset digest 一致：`d06718e293e6c303dcab64caab6dbc3ac42e29978e9ffccdcf20f7a7c0ac8ee9`。
- 二进制、受控用户配置和样例 Git 仓库均置于临时目录；无模型凭据、全局安装或用户 Git 配置改动。

| Windows 离线命令 | 实际结果 |
|---|---|
| `ocr version` | 退出 0，v1.12.4，windows/amd64 |
| `ocr delegate preview --format json` | 退出 0，schema_version 为 1；识别修改的 sample.py 和未跟踪的 new.py |
| 同次 preview 的文档覆盖 | README.md 被排除，原因 unsupported_ext；3 个变更文件仅 2 个进入 reviewable_files |
| `ocr delegate rule --format json sample.py new.py` | 退出 0，返回 Python 系统规则及文件分组 |

原始记录：本机临时目录 `coding-agent-ocr-smoke-s2loo377/results.json`。临时工件可能被清理，关键结果已记于上表。

**后续更新：** OCR LPAC 启动已实测失败，见文末追加记录。仍未验证：macOS 原生运行、完整 review 模型调用、取消后的进程树清理、审查准确率、成本及速度。环境未安装 Go，未构建或执行上游 Go 测试。普通 Windows CLI 冒烟不能代替沙箱验收。

## 关键发现

### 1. Delegation 是可用接口，但仍依赖 Git

preview JSON 提供候选和排除文件及原因，rule JSON 提供规则分组；无需 OCR 模型调用。两者都通过要求 Git 仓库的上下文加载，rule 也不是独立于 Git 的纯函数。[实现][delegate]

规则来自显式配置、项目配置、用户全局配置和内置规则；自定义规则是否合并系统规则由配置决定。接入时需固定来源并记录指纹，保留本项目规范的优先级。[规则加载][rules]

### 2. 完整引擎已有覆盖记录，不能只读退出码

完整 review 的 `ocr.run-manifest/v1` 包含 selected/completed/failed/waived、输入身份及 complete/partial/failed/skipped 状态，适合映射到门禁。它不只是评论列表。[Manifest][manifest]

partial、skipped 可能退出 0；空 comments 或退出 0 均不能单独表示通过。还须检查运行失败、完整覆盖及本项目的必需范围。[退出语义][exit]

评论有位置、建议及可选 severity，与 blocking/major/minor 不同构。需明确映射，缺失严重度或无法判断的必需审查不能默认通过。[评论模型][comment]

### 3. 完整 CLI 的日志不能代替本项目调用前留痕

本项目 ModelRuntime 在供应商调用前写请求并检查日志可写。OCR JSONL 使用缓冲写入，单条记录没有调用前持久化确认；写入器初始化失败可延后到结束时返回。最终报告错误与立即阻止后续调用是不同保证。[持久化][persist]、[初始化][history]

OCR 自行执行模型和工具调用。只在外层记录一次 review 命令，不能提供内部每次执行前的宿主确认点。完整复用需要增加可等待宿主确认的调用接口或修改引擎调用层；事后读取日志不足以满足现有约束。[调用循环][loop]

### 4. 默认并发可调，预算语义仍需改造

并发可设为 1。review 的 max-tools 是每子任务轮次，参数有最小 50 的处理，共用加载逻辑仅在超过模板默认值时提高限额，无法直接表达宿主剩余的少量调用额度。token budget 不等于逐次调用计数，内部重试也需统一计入。[参数][flags]、[加载逻辑][shared]

### 5. Windows/macOS 的主要难点在执行边界

当前 Windows 普通 shell 使用 LPAC、只读源码副本、禁网及进程限制；不暴露 .git，专用 Git 查询使用受控 helper。macOS 当前禁止 process-fork。完整 OCR 自行调用 Git、请求模型并写会话目录；Delegation 无模型调用但仍调用 Git。这些是源码确认的结构性差异，尚无 OCR 沙箱实测结论。

上游 Windows 进程组配置为空实现，注释指出终止直接子进程不能保证孙进程结束；宿主需验证整棵进程树的取消及资源限制。提供 Windows exe 不等于兼容本项目隔离要求。[Windows 实现][windows]

## 建议的最小接入验证

1. 固定版本和 JSON schema，先证明 Delegation 能处理受控、冻结的审查输入。优先确认离线输入接口的可行性；若使用合成 Git 仓库，只包含获准内容，不暴露真实 .git、hooks、凭据及控制记录。此方案尚未实现。
2. 本项目保存权威必需范围，核对 OCR 排除列表。文档等必需项被排除时补审或阻塞，不跟随筛选结果缩小验收范围。
3. 独立 Reviewer 接收需求、任务、Diff、相关源码、规则和证据，模型与只读工具经过现有运行时。现有 Codex executor 针对 Coder，仍需调整；Claude Code、Pi 的执行边界需分别验证。
4. 控制器校验结果，绑定实际代码快照、计划版本和知识修订，生成 ReviewResult；Workflow/QualityGate 决定完成。OCR manifest 不能替代本项目版本绑定或真实测试证据。
5. 验证部分完成但退出 0、必需文件被排除、无效结果、旧快照、日志故障、预算耗尽、越权及取消，并补 Windows 沙箱和 macOS 原生实测。

建议先做第 1 步的执行边界原型，再承诺工期。当前证据支持优先复用 Delegation，不能支持“一个 wrapper 就完成 P10”。本次仅记录评估，未引入依赖或适配代码，P10 保持 NOT_STARTED。上游采用 [Apache-2.0][license]；未来复制或分发时保留适用许可与声明。

[delegate]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/cmd/opencodereview/delegate_cmd.go
[rules]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/internal/config/rules/system_rules.go
[manifest]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/internal/session/manifest.go
[exit]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/cmd/opencodereview/review_cmd.go
[comment]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/internal/model/review.go
[persist]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/internal/session/persist.go
[history]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/internal/session/history.go
[loop]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/internal/llmloop/loop.go
[flags]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/cmd/opencodereview/shared_flags.go
[shared]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/cmd/opencodereview/shared.go
[windows]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/cmd/opencodereview/procattr_windows.go
[license]: https://github.com/alibaba/open-code-review/blob/4e59c7e815bde045158b549cdc2a7f58a32f6ba0/LICENSE

## 2026-09-17 追加：真实 Windows 沙箱探测

用户授权继续最小接入验证。复用现有 WindowsReadOnlyProcess(verification=True)，保持
LPAC、Win32k 禁用、禁网、只读源码副本、私有 scratch 和最多 16 个进程；
未改生产沙箱，也未以普通进程回退。仅构造人工 Git 仓库，不读取用户项目 Git 元数据。

**结果：官方 v1.12.4 在当前 Windows 沙箱中无法启动。**

| 命令 | 实测 |
|---|---|
| ocr version | 退出 2；未超时，输出未截断 |
| ocr delegate preview --format json | 退出 2；未进入 Delegation |
| ocr delegate rule --format json sample.py new.py | 退出 2；未进入规则读取 |

三者均报告 `panic: Failed to load user32: A dynamic link library (DLL) initialization routine failed.`，
堆栈落在 `github.com/atotto/clipboard@v0.1.4/clipboard_windows.go` 的包初始化。
该依赖在初始化时调用 MustLoadDLL("user32")；与当前后端禁用 Win32k 的策略存在启动冲突。
没有关闭该策略做对照，因此不声称已排除其他加载限制。
[依赖源码](https://github.com/atotto/clipboard/blob/v0.1.4/clipboard_windows.go)
与本地 `src/coding_agent/runtime/_lowbox.py` 可核对这一调用和策略。

新增 [examples/ocr_delegation_probe.py](examples/ocr_delegation_probe.py) 作为开发诊断。
它复制所选可信 OCR 二进制，使用人工仓库及现有后端；JSONL 在每次调用前 flush/fsync，
保存二进制摘要、实际进程结果、输入文件指纹及原仓库未变检查。它不是产品审查证据，
不生成 ReviewResult，不需要模型凭据，也不安装依赖。输出目录必须不存在，便于保留结果而不覆盖文件。

复验命令（项目虚拟环境，路径按本机填写）：

```powershell
.\.venv\Scripts\python.exe examples/ocr_delegation_probe.py --ocr C:/tools/ocr/ocr.exe --git-runtime "C:/Program Files/Git/mingw64/bin" --output-dir C:/Temp/ocr-probe-new
```

脚本退出 1 表示此次能力探测不可用，不是通过或跳过。本次记录位于本机临时目录
`coding-agent-ocr-probe-20260917-final/records/probe.jsonl`：
3 组请求和结果完整配对，compatible=false，fixture_unchanged=true，二进制摘要与上次一致。
脚本 Ruff 检查/格式检查通过；未修改产品源码，因此没有重跑全量产品测试。
初次临时探针因遗漏必填 risk 字段在执行前失败，补全后完成探测；初次脚本静态检查的两行超长已格式化。

### 修订后的接入判断

- **收回“可以直接用官方 Windows exe 做受控 Delegation”这一实施假设。**
  之前普通进程的 JSON 冒烟仍有效，但不足以支持现有沙箱内的接入。
- 优先验证无交互界面、无剪贴板初始化依赖的独立 Delegation 入口，
  同时评估接受冻结文件/Diff 的离线接口，以避免原生 Git 子进程与真实 .git 依赖。
  单独移除剪贴板依赖只能解决第一个阻塞，不能承诺全部兼容。
- 若维护上游构建的成本过高，可只复用有版本和来源记录的规则资产，
  用现有 Git/文件工具提供输入；这比重新实现完整 OCR 引擎更小，但复用范围进一步缩减。
- 当前无 Go 工具链，未构建修改版，也未实现产品 adapter；macOS 原生验证、
  Git 阶段执行、完整审查质量和取消测试仍待完成。P10 保持 NOT_STARTED。


## 2026-09-17 追加：无交互依赖的离线规则入口通过

继续验证后，采用独立 Go 入口直接调用固定上游的
`rules.LoadDefault` 和 `delegate.GroupRules`，未修改上游规则和匹配算法。
入口、单元测试及构建说明保存在 [examples/ocr_offline_rules](examples/ocr_offline_rules/README.md)。
该入口是新增开发原型，不是官方 OCR CLI，也不是产品 adapter。

### 构建与来源

- 临时下载 Go 1.25.5 Windows amd64 ZIP，SHA-256 与官方下载元数据一致：
  `ae756cce1cb80c819b4fe01b0353807178f532211b47f72d7fa77949de054ebb`。
- 不修改全局安装、PATH 或项目 pyproject 依赖。源码、Go 工具链、GOPATH、GOCACHE
  均在本机临时目录 `coding-agent-ocr-headless-qmj8tvnw`。
- 固定上游提交仍为 `4e59c7e815bde045158b549cdc2a7f58a32f6ba0`。
  仅复制规则、分组及其两个内部依赖目录，保留 LICENSE 和适用 NOTICE；
  外部构建依赖为 doublestar/v4 v4.10.0，go.sum 从固定上游提取并由 Go 校验。
- 构建通过，产物 3,420,160 字节；源码输入、模型及权限不依赖该大小判断。

### 实测

本次使用 `WindowsReadOnlyProcess(verification=False)`：
LPAC、Win32k 禁用、禁网、只读副本、最多 1 个进程。
未配置 Git runtime，没有开放验证 scratch，也没有提供模型凭据。

| 检查 | 结果 |
|---|---|
| 冻结路径输入 sample.py、new.py、README.md | 退出 0，全部得到非空规则分组 |
| Python 规则与上次官方 v1.12.4 普通进程输出对照 | 文本逐字一致 |
| 路径穿越 ../private | 退出 1，拒绝 |
| 控制目录 .GiT/config | 退出 1，拒绝 |
| 未知输入 schema | 退出 1，拒绝 |
| 超过 256 KiB 的输入 | 退出 1，拒绝 |
| 输入仓库指纹 | 执行前后不变 |
| 新入口 Go 单元测试 | 2 passed；包含 7 种非法输入及完整规则分组检查 |

真实请求和结果在
`coding-agent-ocr-headless-qmj8tvnw/native-final/records/probe.jsonl`，
合法 JSON 输出在同目录 valid-output.json，单测日志在构建根目录 unit-tests.log。
所有原生探测均未超时、未截断。首轮对照脚本读取旧工件时使用 Windows 默认编码导致
UnicodeDecodeError；改为显式 UTF-8 后完整重跑，未修改原型或规则来绕过断言。

### 当前结论与剩余范围

**已证明：可以在当前严格 Windows 沙箱中离线复用上游内置规则和分组。**
官方完整二进制的启动失败仍存在，本次通过仅属于裁剪后的新入口。

输入只是路径列表，不包含 Diff 或文件内容；没有执行完整 Delegation 文件筛选、
项目/用户自定义规则加载、Objective-C 内容识别、模型审查或修复。
README.md 得到默认规则只是规则覆盖，不是已完成文档审查。
生产接入仍需独立组织任务上下文，合并适用项目规范，校验资产/二进制身份，
并通过 Tool Runtime/ModelRuntime 留痕和门禁。

下一步可按“复用固定规则资产或裁剪入口 + 现有运行时 Reviewer”推进产品化；
是否维护 Go 构建需与直接管理规则资产的成本比较。本次未把原型注册为产品工具，
未实现 Reviewer，P10 仍为 NOT_STARTED；macOS 原生验证和真实审查质量验证仍待完成。

## 2026-09-17 追加：生产规则来源 API 接入

经用户继续授权，选择固定规则资产路线，生产端不依赖 OCR exe 或 Go。
新增 ReviewRulesOperation 和 ToolRuntime 处理，规则读取计入共享预算并先记录请求；
输出绑定上游提交、规则包摘要、单份规则文档摘要及实际选中路径。
52 份上游规则文档保持原文，路径映射的花括号选项预展开，复用现有 Python glob 匹配；
172 个路径已与固定上游 Go 原型的规则文本摘要对照一致，夹具随测试保存。
本次没有引入 OCR 的文件排除策略，文档等所有请求路径均获得对应或默认指导。

新增 attach_review_guidance 控制器 API，将该指导与已有 TaskContextPack 分开保存。
项目显式规范优先，外部指导标记为 supplemental；上下文版本、路径覆盖或输出完整性不符即拒绝。
返回值包含请求事件引用及可计算摘要，尚未由 agent run 自动构建/持久化，
也没有模型 Reviewer 或 ReviewResult。不能将规则获取成功当作审查通过。

许可证、上游来源和转换声明随 Python 包分发，固定资产每次读取都校验摘要。
Wheel 已验证包含规则/许可证/声明，并完成独立 zip 导入读取。
构建虚拟环境原先缺少 setuptools；使用临时目录下载并校验 setuptools 80.9.0 构建，
没有安装到项目虚拟环境或修改全局依赖。

定向 19 passed；全量 882 passed、24 skipped、1 xfailed（287.82 秒）。
Ruff check/format、Windows/Darwin mypy（63 个源码文件）通过。
跳过项与 Windows 私有目录已知失败保留，未执行真实模型或 macOS 原生验证。
P10 状态改为 IN_PROGRESS，仅完成规则来源与上下文接口子步骤。

## 2026-09-17 追加：独立 Reviewer 调度

固定 OCR 规则资产现已接入 agent run 的独立 Reviewer 上下文。模型调用与记录复用
本项目 ModelRuntime；项目显式规则优先，外部规则仍仅为 supplemental。
ReviewDraft 与运行时生成的 ReviewResult 分离，路径/规则/维度覆盖不全、
blocking/major、过期或不可用结果均不能通过；最终快照重新验证并审查。

本次未复用 OCR CLI/模型循环，也未扩大沙箱权限。离线验证只证明本项目的输入、
记录和门禁行为，不能证明真实模型审查质量或比上游更可靠；相关测试与待验收项见
DEV_PLAN.md §8.14。P10 保持 IN_PROGRESS，真实模型与 macOS 原生验证仍待完成。


## 2026-09-17 追加：真实模型冒烟

用户配置的 glm-5.3 已通过有限 Windows 联调：正常改动通过任务与最终审查，
返回值回归在实际 unittest 通过时仍被 blocking 问题阻止。
初次 429、全局覆盖漏报、既有弱测试的历史归因错误均保留在 DEV_PLAN.md §8.15；
提示词补充覆盖和历史依据要求后分别复验通过，门禁未放宽。
本次使用固定 Coder 补丁，不代表真实编码模型全链路、全面审查质量或与 OCR 的质量对照。
