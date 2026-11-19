"""User configuration (LLM provider). Never holds secrets: only the NAME of the key's variable."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, fields
from pathlib import Path

import yaml

from xcalibur.llm.base import ProviderConfig
from xcalibur.llm.errors import LLMConfigError

CONFIG_FILENAME = "xcalibur.yaml"
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "xcalibur" / CONFIG_FILENAME


def workspace_config_path(workspace: Path) -> Path:
    return workspace / CONFIG_FILENAME


def validate_env_name(name: str) -> str:
    """Accept only an environment variable NAME, so a pasted key is never saved."""
    if _ENV_NAME.fullmatch(name) is None:
        raise LLMConfigError(
            "The API key setting must be the NAME of an environment variable "
            "(e.g. ANTHROPIC_API_KEY), not the key itself."
        )
    return name


def resolve_config_path(path: Path | None = None, workspace: Path | None = None) -> Path:
    """Explicit path > workspace file (if present) > user config directory."""
    if path is not None:
        return path
    if workspace is not None:
        return workspace_config_path(workspace)
    local = workspace_config_path(Path.cwd())
    return local if local.exists() else user_config_path()


def save_llm_config(config: ProviderConfig, path: Path) -> Path:
    if config.api_key_env:
        validate_env_name(config.api_key_env)
    data = {"llm": {k: v for k, v in asdict(config).items() if v is not None}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def load_llm_config(path: Path) -> ProviderConfig:
    if not path.exists():
        raise LLMConfigError(f"No configuration at {path}; run 'xcalibur init'.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    llm = data.get("llm")
    if not isinstance(llm, dict):
        raise LLMConfigError(f"{path} has no 'llm' section; run 'xcalibur init'.")
    known = {f.name for f in fields(ProviderConfig)}
    unknown = sorted(set(llm) - known)
    if unknown:
        raise LLMConfigError(f"{path}: unknown llm settings {', '.join(unknown)}.")
    try:
        config = ProviderConfig(**llm)
    except TypeError as exc:
        raise LLMConfigError(f"{path}: incomplete llm settings ({exc}).") from None
    if config.api_key_env:
        validate_env_name(config.api_key_env)
    return config
