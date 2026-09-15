from collections.abc import Callable

import pytest

from coding_agent.core import AcceptanceSpec, Evidence, TaskSpec


@pytest.fixture
def acceptance() -> AcceptanceSpec:
    return AcceptanceSpec.model_validate(
        {
            "criteria": [
                {
                    "id": "behavior",
                    "description": "preserve public behavior",
                    "required_check_ids": ["unit", "review"],
                }
            ],
            "checks": [
                {
                    "id": "unit",
                    "description": "behavior tests",
                    "evidence_type": "test",
                    "command": ["python", "-m", "pytest", "tests/"],
                },
                {
                    "id": "review",
                    "description": "independent review",
                    "evidence_type": "review",
                },
            ],
        }
    )


@pytest.fixture
def make_task(acceptance: AcceptanceSpec) -> Callable[..., TaskSpec]:
    def make(task_id: str, *dependencies: str) -> TaskSpec:
        return TaskSpec.model_validate(
            {
                "id": task_id,
                "title": "Implement behavior",
                "goal": "Preserve public behavior",
                "depends_on": dependencies,
                "scope": {"allowed": ["src/**", "tests/**"], "forbidden": ["secrets/**"]},
                "requirements": ["preserve public behavior"],
                "acceptance": acceptance,
                "risk": {"level": "high", "security": "high"},
                "permissions": {"network": False, "shell": "restricted", "database": "test_only"},
            }
        )

    return make


@pytest.fixture
def evidence_data() -> dict[str, object]:
    # Explicit test fixture facts, never defaults for production evidence.
    return {
        "id": "evidence-001",
        "task_id": "task-001",
        "plan_version": 1,
        "context_revision": "project-001",
        "workspace_revision": "snapshot-001",
        "criterion_id": "behavior",
        "criterion": "preserve public behavior",
        "check_id": "unit",
        "evidence_type": "test",
        "command": ["python", "-m", "pytest", "tests/"],
        "result": "2 passed",
        "status": "passed",
        "source": "events/check-001",
        "timestamp": "2026-09-15T09:00:00Z",
    }


@pytest.fixture
def evidence(evidence_data: dict[str, object]) -> Evidence:
    return Evidence.model_validate(evidence_data)
