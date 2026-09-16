"""Explicit dotenv configuration for read-only model assistance; never exports secrets."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr

from ..core.provider import ModelFailure, ProviderError


class AssistantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai", "zhipu"]
    api_url: str
    model: str
    api_key: SecretStr


def load_assistant_config(path: Path, *, model: str | None = None) -> AssistantConfig:
    """Read four CODING_AGENT_* keys. Environment overrides file; CLI overrides model."""
    names = {"PROVIDER", "API_URL", "MODEL", "API_KEY"}
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError("oversized configuration")
        values: dict[str, str] = {}
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key = key.strip()
            if not key.startswith("CODING_AGENT_") or key.startswith("CODING_AGENT_CODEX_"):
                continue
            name = key.removeprefix("CODING_AGENT_")
            if not separator or name not in names or name in values:
                raise ValueError("invalid configuration entry")
            value = value.strip()
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError("unclosed quoted value")
                value = value[1:-1]
            values[name] = value
        for name in names:
            if (environment_value := os.environ.get("CODING_AGENT_" + name)) is not None:
                values[name] = environment_value.strip()
        if model is not None:
            values["MODEL"] = model
        if set(values) != names or any(not value.strip() for value in values.values()):
            raise ValueError("missing configuration")
        return AssistantConfig.model_validate({key.lower(): value for key, value in values.items()})
    except (OSError, UnicodeError, ValueError):
        # Never include file contents or a Pydantic input_value in errors.
        raise ProviderError(ModelFailure(code="configuration")) from None
