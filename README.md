# Verified Coding Agent Runtime

按项目规范执行编码任务，以关联代码快照的证据判断完成状态。

当前实现范围为 P01：领域模型、依赖图校验、就绪任务计算和状态转换约束。
尚未实现 Agent、CLI、工作流执行、权限执行后端或证据持久化。

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

## 领域边界

- `core/models.py`：需求、风险、任务、范围、权限、验收与 Evidence 结构。
- `core/graph.py`：不可变 DAG；新增任务或依赖返回经过校验的新图。
- `core/state.py`：状态枚举和纯转换校验；P02 的 Workflow Engine 才负责改变状态。
- 任务状态单独传入图查询；图不维护第二份可写状态。只有前置任务全部为
  `VERIFIED` 的 `PENDING` / `READY` 任务会被返回。
- 领域模型使用冻结对象和 tuple，拒绝未知字段。加载外部数据使用
  `model_validate` / `model_validate_json`，不能用绕过校验的 `model_construct`
  或 `model_copy(update=...)` 加载不可信数据。
- Evidence 的结果、来源、带时区时间及版本字段由调用方明确提供。
  结构合法不代表真实执行或门禁通过；来源鉴权、快照比对和 QualityGate 属于后续阶段。
- ScopePolicy / PermissionPolicy 仅描述边界；路径解析、链接检查及进程隔离在 P03/P04 实现。

完整要求见 [开发规格](CODING_AGENT_DEVELOPMENT_SPEC.md)，当前进度和检查结果见
[开发计划](DEV_PLAN.md)，仓库维护方式见 [AGENTS.md](AGENTS.md)。
