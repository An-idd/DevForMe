# Verified Coding Agent Runtime

按项目规范执行编码任务，以关联代码快照的证据判断完成状态。

当前实现 P01 领域基础与 P02 串行工作流：领域模型、依赖图、状态转换、
模式与授权匹配、QualityGate、有限修复及 Fake 生命周期事件。
尚未实现真实 Agent、CLI、工具权限执行后端、证据持久化或最终交付。

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
测试不需要模型凭据，也不调用网络或真实工具。

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
- ScopePolicy / PermissionPolicy 仅描述边界；路径解析、链接检查及进程隔离在 P03/P04 实现。

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
