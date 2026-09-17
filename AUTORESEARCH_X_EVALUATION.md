# AutoResearch X 渠道：真实 CodingAgent 对照评估

日期：2026-09-17。最新结论：**gpt-5.5 的真实 Codex 工具桥接通过，业务任务产出 6 个文件改动后超时；候选未通过独立行为检查，全链路仍未通过。**

本报告第 1–8 节保留最初 gpt-6-astra 两轮试验事实；第 9 节记录后续兼容模型试验，覆盖前文“尚无候选代码”的阶段性结论。
候选与参考实现的比较是单任务、不等预算的事后检查，不能当作模型能力排名。

## 1. 评估范围与方法

- AutoResearch 基线：`63d212b4bd8d9b496c9cae2ccfea44c051bea888`。
- 参考实现：原工作区已有的 11 个修改/新增文件，包含 X collector、配置/CLI、信号归一化、测试及文档。
- 候选实现：从同一 Git 基线归档创建独立副本，由 CodingAgent 驱动真实 Codex 实现相同需求；没有把参考实现交给 Coder。
- Coder：本机原生 Codex `0.154.0-alpha.6.2`，`gpt-6-astra`；专用 `D:/AI Project/CODEX_HOME`。
- Reviewer：项目 `.env` 配置的智谱 `glm-5.3`；凭据不进入模型上下文或报告。
- 用户明确授权向 Codex 和 GLM 发送任务相关源码、文档、Diff 与测试结果。首次自动审批拒绝发生在外发之前；取得明确授权后才执行。
- 初始化和计划由离线 API/确定性草案构建，本次**不评估真实 Planner 质量**。
- 单轮预算：30 个运行时工具请求、31 个模型调用段、最多 2 次 Coder 尝试、1 次 review fix；Coder 每次 300 秒/20 个工具，Verifier 每项 120 秒，GLM 每次 60 秒/8192 输出 token。
- Schema 修复后从干净副本另做一次独立复验，需求、预算和权限不变；原失败会话完整保留，没有重置其预算。
- 11 个原项目文件的 SHA-256 复核全部一致；原 AutoResearch 实现未被改写，未提交、未推送。

这不是统计性 benchmark：只有一个业务任务，参考实现的开发时间/token 未记录，也没有双方可交付代码可供盲评。

## 2. 结果对照

| 项目 | 原有参考实现 | CodingAgent 候选 |
| --- | --- | --- |
| X 数据采集入口 | 已有实现；配置、CLI、采集与归一化均有代码 | 两轮都未产生 patch |
| 宿主离线 pytest | 106 passed；原基线 85 passed | 未生成实现，未运行候选验收 |
| Ruff / compileall / secret_scan | 均 exit 0 | 工作流未进入验证 |
| Windows 产品沙箱测试 | 无该快照的产品 Evidence | 独立基线预检在导入阶段失败 |
| 工作流内 GLM Reviewer | 不适用 | 未调度，前置步骤未满足 |
| 补充只读 GLM 代码评估 | 有结果，但覆盖声明不完整 | 无代码，无法比较质量 |
| 最终完成判定 | 仅宿主检查通过，真实 X API 未验证 | failed / blocked；requirement_complete=false |

宿主测试采用 AutoResearch 现有虚拟环境：Python 3.12.10、pytest 9.1.1、Ruff 0.16.6。
Ruff 与 requirements-dev 的 0.15.14 固定版本不同；两份副本使用相同已安装环境，不能据此声称锁定依赖环境也通过。
上述宿主检查是外部评估事实，未伪装成产品沙箱验收 Evidence。

## 3. 真实运行和诊断

### 3.1 原版完整任务

- 19.97 秒，`failed: Codex adapter: invalid_response`。
- 40 条事件、13 次运行时工具请求（上下文及工作区操作）、1 个模型调用段。
- 0 个 patch、0 条验证 Evidence、0 次 GLM 审查；日志没有未配对请求。
- 独立合成冒烟同样失败（18.58 秒），诊断收到包含 schema 信息的服务错误通知；原始供应商错误正文未保留，不能声称已取得完整错误原因。
- 代码检查发现直接传递的 Pydantic Schema 没有把可空的 `replan` 列为 required。

### 3.2 已实施的最小修复

`executors/codex.py` 复用已安装 OpenAI SDK 的 `pydantic_function_tool`，生成严格输出 Schema。
同仓 OpenAI Provider 已采用这个转换；无新依赖，无新增通用框架。
现有真实 CLI + 本地假服务的协议测试增加断言：所有对象属性必填、replan 保留 null、移除默认值。

[OpenAI 官方结构化输出文档](https://developers.openai.com/api/docs/guides/structured-outputs)要求所有属性都列为 required，可空字段通过 null 表达可选值。
修复后的真实调用已能返回结构化结果，说明已越过原先的 Schema 阻塞；这不代表工具桥接通过。

### 3.3 修复后完整任务

- 24.56 秒，状态 `blocked`。
- Codex 返回：`code-mode host is disabled`，无法调用批准的工具。
- 仍为 40 条事件、13 次运行时工具请求、1 个模型调用段；没有来自 Coder 的 patch。
- 合成冒烟也在相同位置受阻（12.99 秒），没有工具请求或文件修改。
- 可观察事实是模型返回了该阻塞说明、运行时没有收到动态工具调用；本次未保留内部工具宿主原始日志，尚未确定完整底层调用路径。
- 当前配置明确禁用 `code_mode_host`，离线假服务的直连 function-call 测试通过，真实模型路径却无法使用工具。这是尚未解决的兼容缺口。
- 未开启原生 shell/code-mode 宿主，也未放宽工具白名单或进程限制来获得表面成功。

### 3.4 Windows 验证预检

单独通过 ToolRuntime + VerificationProcess，在基线副本运行原计划的 `python -m pytest tests/ -q`：
exit 1，测试收集尚未开始。调用链：

`pytest 自动加载插件 → anyio.pytest_plugin → asyncio.windows_events → _overlapped → WinError 10013`

当前禁网 LPAC 环境下，导入链触发了被拒绝的套接字操作。没有把禁网改成允许网络。
这与此前已有的 Python 私有临时目录 0700 兼容问题是两个阻塞；本次尚未执行到后者。
预检不属于两轮主任务的验证 Evidence。

## 4. 补充真实 GLM 评估

为了仍能评估现有交付质量，另做 **1 次流程外、只读、参考实现评估**，复用现有 `review_context` 和 `ModelRuntime`：

- 输入：相同需求、参考实现当前源码与 Diff、CONTRIBUTING、固定 OCR 指导规则、宿主检查结果及局限。
- 没有向 ReviewRunner 注入伪造 Evidence；结果不改变任务状态或完成门禁。
- 耗时 38.25 秒，模型返回 conclusion=passed，blocking/major 均为空，minor 有 4 项。
- 覆盖检查失败：应为 `config/project.example.json`，模型却列出 `config/project.example.`。不能自动修正名字后采纳“通过”。
- 按产品相同覆盖原则，该结果只能视为 **inconclusive**；保留原始草案。

人工核对 minor：
1. 隐去底层异常链会降低诊断信息：属于安全与可诊断性的取舍，不能因此恢复可能含凭据的原始异常。
2. 建议拆分解析 try 块：属于可维护性建议；模型给出的 60–64 行与实际解析块 70–81 行不符。
3. 通道注册表的默认查询 `agent`：代码确实存在；项目任务路径会构造自己的查询，需要结合其他调用方判断，不应直接认定业务缺陷。
4. 用户查询括号可能影响查询语义：尚未提供可复现失败输入或实际 X API 验证，暂不作为已确认缺陷。

因此，只能说“这一条样本没有获得 GLM 的严重问题报告”，不能声称参考实现无缺陷或 Reviewer 已稳定可靠。

## 5. 消耗与局限

| 已知用量 | input | output | total |
| --- | ---: | ---: | ---: |
| 修复后完整任务 Codex | 109561 | 137 | 109698 |
| 修复后合成冒烟 Codex | 11552 | 101 | 11653 |
| 补充参考实现 GLM | 27110 | 1577 | 28687 |
| 已知总量 | 148223 | 1815 | 150038 |

两次 Schema 失败调用没有返回用量；总开销不完整。Codex 用量是 CLI turn 累计值，
一个 ModelRuntime 调用段不等于一次底层服务请求。没有账单金额，不能换算或比较真实成本。
参考实现的开发时间/用量未知，不作速度或省钱排名。
未提供 X_BEARER_TOKEN，未调用真实 X API；macOS 未验证。

## 6. 本次仓库检查

- `pytest tests/test_codex.py -q`：55 passed（9.50 秒）。
- Ruff check、Ruff format --check、mypy src/coding_agent：通过，类型检查 65 个源码文件。
- 真实 Codex 冒烟：失败，原因如上；不能算入离线通过结果。
- 未重复执行全仓 pytest；本次生产代码只改 Schema 转换，相关协议测试已运行。
- P10 继续 IN_PROGRESS；本次不推进 P11/P12。

## 7. 下一步顺序

1. 针对固定 Codex 版本核实真实模型的动态工具路由，找到仍由 ToolRuntime 执行副作用的受支持路径；先让真实 read → patch 合成冒烟通过。
2. 修复或明确 Windows 禁网验证环境的兼容边界，保持原测试和权限要求；同时保留已有私有临时目录问题。
3. 预估上下文、验证及多次审查的工具消耗。当前两轮各消耗 13/30 个工具请求却尚未编码；预算适配需要明确计划，不能运行中悄悄上调。
4. 重新执行相同基线任务，取得真实候选 Diff、完整测试与有效 GLM 覆盖后，才能比较代码质量与交付效率。

## 8. 可核查工件

完整工件目录：
`C:/Users/7x/AppData/Local/Temp/coding-agent-x-evaluation-lzu_wfkg`（临时目录，系统清理后可能失效）。

- `manifest.json`：基线、参考文件摘要、实际专用 profile 与试验索引（无凭据）。
- `requirement.json`、`prepare_plan.py`、`run_evaluation.py`：需求及驱动。
- `run-result.json`、`run-timing.json`、`baseline/.agent/run-*/events.jsonl`：原版结果。
- `schema-fix-trial/run-result.json`、`run-timing.json`、其 `baseline/.agent/run-*/events.jsonl`：修复后结果。
- `smoke-summary.json`、`smoke-pytest-153/`、`smoke-pytest-155/`：两次真实合成冒烟的模型记录副本。
- `external-baselines.json` 与 `baseline-*.log` / `reference-*.log`：宿主检查。
- `verification-preflight.json`、`verification-preflight-records/`：Windows 实际失败结果。
- `reference-review-context.json`、`reference-review-result.json`、`reference-review-records/`：补充 GLM 上下文、草案和调用记录。

所有结论均按实际失败和未验证状态保留，不把本次诊断修复记为全链路验收通过。


## 9. 后续兼容模型试验与实际代码对照

### 9.1 工具桥接已找到可用路径

本机模型目录明确将 gpt-6-astra、gpt-5.6-sol/terra/luna 声明为
`tool_mode=code_mode_only`；gpt-5.5 没有该限制。
在说明兼容问题后，评估控制器选择 gpt-5.5 复验，没有更改专用 profile、原生工具开关、
Job 限制或 ToolRuntime 权限。没有修改模型目录来伪装能力。

真实合成冒烟 **1 passed，14.61 秒**。日志记录 read → patch，产物内容一致，
最终用量 9226 token；记录已复制到 `smoke-gpt55-records/`。
这是直接动态工具路径的实测通过，**不是 gpt-6-astra 的 code-mode 适配完成**。
模型目录快照仅保存 slug/tool_mode/visibility，位于 `codex-model-tool-modes.json`。

### 9.2 实际候选任务

在同一基线建立第三个独立试验 `gpt55-trial/`，保留相同需求、30/31 总预算、
20 个 Coder 工具与 300 秒时限。没有把此前副本的预算或失败记录删除。

- 304.05 秒结束，`blocked: TimeoutError: adapter failed; reconciliation required`。
- 102 条事件，29 个总工具请求，17 个模型调用段；最后一段为 interrupted。
- 7 次 patch 请求中一次失败，后续重试该文件成功；最终实际改动 **6 个文件**。
- 改动包括 X collector、通道注册、配置、CLI、信号处理和示例配置。
- 新 X 测试与 4 份文档均未完成；不能把中断草稿当作完整实现。
- 日志无未配对请求，最终工作区快照和 Diff 已记录；副本保留供核查。
- 工作流没有进入验证/GLM 阶段，也没有自动续跑、扩大超时或重置预算。
- 该超时运行没有返回可累计 token 用量，不能记为零成本。

### 9.3 两份实现的外部检查

所有检查在副本上执行，不覆盖原项目，不作为 Workflow Evidence：

| 检查 | 参考实现 | gpt-5.5 超时候选 |
| --- | --- | --- |
| 项目 pytest | 106 passed | 85 passed，仅原有测试 |
| Ruff | 通过 | 1 项 E501，collector 第 159 行 |
| compileall / secret_scan | 通过 | 通过 |
| 共用行为探针 | 8 passed | 2 passed、6 failed |
| 新增功能测试与文档 | 已有 | 未完成 |

共用探针在看到候选代码后编写，因此属于**事后诊断，不是盲测 benchmark**。
两份实现只在函数入口适配（search_x / search_x_recent）上不同，使用相同模拟 HTTP 响应与断言，
不依赖内部 helper 名称，也不发真实 X 请求。脚本为 `test_behavior_comparison.py`。

候选通过成功响应与真正空结果两项；以下六项失败，参考实现全部通过：

1. HTTP 200 的部分错误 payload 被当作正常结果。
2. meta.result_count 与 data 数量不一致时仍返回成功。
3. 非法 post ID（../bad）未拒绝。
4. 空帖子正文未拒绝。
5. HTTP 302 加空结果正文被当作成功，未明确识别源失败。
6. 运输异常通过 `raise ... from exc` 保留了模拟敏感文本的 traceback。

第 6 项没有使用真实凭据；它证明异常文本可通过异常链暴露，不等于发现了本轮真实密钥泄漏。
这些问题说明“旧测试通过”不足以验证新渠道边界行为；不证明 gpt-5.5 完成开发后也无法修复。

### 9.4 GLM 对候选的补充审查

独立、只读调用一次 glm-5.3，26.16 秒，23434 token。
输入包含候选源码、Diff、完整需求、宿主检查结果和上述六项失败摘要，
所以这不是对 GLM 独立发现能力的盲测。

模型返回 changes_requested，列出 4 条 blocking、2 条 major：
识别了缺少测试/文档、Ruff 和错误处理问题，方向与执行事实一致。
但仍将 config/project.example.json 写为 config/project.example.，覆盖检查不通过。
已核对发送的 required_paths 是完整路径，配置脱敏器不会改变该路径。
没有自动纠正模型声明，也没有生成流程内“审查通过”记录。

人工复核还发现两处不精确之处：

- 把“暂不进行 X 全站抓取”与有限官方 API 搜索视为必然矛盾，判断过强；未补充配置与限制说明才是明确文档缺口。
- 声称 _metrics 调用两次，实际调用三次；不影响主要阻塞结论，但说明细节仍需核查。

本轮只能评价为：GLM 能利用失败证据给出有用的修改方向，覆盖准确性仍不稳定。

### 9.5 Windows 能力问题的进一步定位

CPython [_overlapped 的初始化实现](https://github.com/python/cpython/blob/main/Modules/overlapped.c)
会创建套接字来取得扩展函数指针。本机实际导入被禁网 LPAC 拒绝（WinError 10013）。

独立诊断结果：

- 明确 `-p no:anyio --collect-only` 后，全部 **85 项基线测试可收集**。
- 单独 `os.mkdir(path, 0o700)` 返回 **WinError 5**；私有目录问题独立存在。
- 未把禁用插件的诊断命令替换正式验收，也未声称测试执行通过。
- 本机仅有 wsl.exe 入口，WSL 尚未安装；未发现 Docker 命令。未安装新系统组件。

新增 [asyncio 能力探针](examples/python-asyncio-check.json)，复用现有 required baseline 机制，
在编码前记录 unavailable 并停止。与私有目录模板共同测试 **2 passed、36 deselected，11.31 秒**；
deselected 只是本仓定向测试选择，不是业务验收通过。
这是提前发现不可用环境的改进，**没有修复 Windows LPAC 对 asyncio/私有目录的兼容性**。

### 9.6 检查、消耗与下一步

新增代码仅为探针及现有回归测试参数化；此前 Schema 修复保持。
Codex 协议 55 passed、Windows 能力基线 2 passed、真实 gpt-5.5 冒烟 1 passed。
Ruff check/format、Windows/Darwin mypy（65 个源码文件）通过。
初次新测试存在一行超长，已格式化修复；用户中断后核对确认此前命令未执行，随后完成检查。
未重复全仓 pytest；原有 Windows 私有目录严格 xfail 保留。

累计已知用量增加 32660，达到 **182698 token**；仍不包括两次 Schema 失败和本轮超时业务任务。
不知道参考实现的开发成本，不能声称更快、更便宜或作公平质量排名。
原 AutoResearch 11 个参考文件摘要仍一致；未 commit/push。

当时的下一步顺序（Windows 工作现已暂停，当前安排见第 11 节）：

1. 明确并实现 Windows 验证后端的兼容方案；保持网络和文件隔离，不改写 Python 语义或删减测试来放行。
2. 将环境能力基线纳入正式试验计划，避免先消耗编码预算才发现验证不可用。
3. 针对这一规模明确任务拆分与完整会话预算；当前固定应用预算、超时及未实现的恢复流程限制了完成能力。
4. 满足前置条件后，再执行新的一轮端到端验收；保留本次中断草稿作为失败样本。

新增工件：`gpt55-trial/run-result.json`、其 journal/工作区、`external-checks.json` 与各检查日志、
`candidate-review-context.json` / `candidate-review-result.json` / `candidate-review-records/`、
`windows-capability-results.json` / `windows-capability-records/`。


## 10. 后续运行条件修复（2026-09-17）

计划现可通过 --max-tool-calls / --max-model-calls 指定调用额度，默认仍为 30 / 31。
配置进入审批指纹；旧计划默认序列化不变，3 份历史评估计划指纹已核对一致。
未给已有失败任务追加预算，也未发起新一轮真实评估；上文计时、用量和对比结论不变。

Windows 完整验证后端路线记录于 [方案文档](WINDOWS_VERIFICATION_PROPOSAL.md)，现按用户要求暂停，转为 TODO。
本轮额外 ACL 诊断在 ctypes 导入（DLL 初始化失败）和 MinGW 探针启动（WinError 623）
阶段失败，未验证 ACL 对照，不构成修复证据。LPAC 的 asyncio 与 0700 目录阻塞仍存在。


## 11. Windows 工作暂停（2026-09-17）

用户要求先暂停 Windows，已列入 [DEV_PLAN.md](DEV_PLAN.md) 第 9.1 节 TODO。
暂停原生兼容诊断、Docker 后端路线选择/开发、系统组件安装和依赖该环境的全链路复验；
用户明确恢复后再推进，普通“继续”不自动恢复本项。
保留现有阻塞、严格 xfail、试验工件和全部对照结果；暂停不代表 Windows 验收通过。
