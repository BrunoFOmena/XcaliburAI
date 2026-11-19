"""User configuration (LLM provider). Never holds secrets: only the NAME of the key's variable.

Where the configuration is read from, in order:
1. ``--config PATH`` (explicit, trusted);
2. ``--workspace DIR`` -> ``DIR/xcalibur.yaml`` (workspace file, untrusted);
3. ``./xcalibur.yaml`` in the current directory, if present (workspace file, untrusted);
4. the user configuration, ``$XDG_CONFIG_HOME/xcalibur/xcalibur.yaml`` (trusted).

A workspace file may come from a cloned repository, so it may not set ``base_url``:
that would send the API key to another server.
"""

from __future__ import annotations

import os
import re
from dataclasses import MISSING, asdict, dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from xcalibur.llm.base import ProviderConfig
from xcalibur.llm.errors import LLMConfigError

CONFIG_FILENAME = "xcalibur.yaml"
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")

# Expected type of each setting (bool is checked before int: bool is a subclass of int).
_TYPES: dict[str, tuple[type, ...]] = {
    "provider": (str,),
    "model": (str,),
    "api_key_env": (str, type(None)),
    "base_url": (str, type(None)),
    "max_tokens": (int,),
    "effort": (str, type(None)),
    "max_retries": (int,),
    "timeout": (int, float),
    "refusal_fallback": (bool,),
}


@dataclass(frozen=True)
class ConfigLocation:
    path: Path
    trusted: bool
    """False for workspace files, which may come from someone else."""


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "xcalibur" / CONFIG_FILENAME


def workspace_config_path(workspace: Path) -> Path:
    return workspace / CONFIG_FILENAME


def locate_config(path: Path | None = None, workspace: Path | None = None) -> ConfigLocation:
    if path is not None:
        return ConfigLocation(path, trusted=True)
    if workspace is not None:
        return ConfigLocation(workspace_config_path(workspace), trusted=False)
    local = workspace_config_path(Path.cwd())
    if local.exists():
        return ConfigLocation(local, trusted=False)
    return ConfigLocation(user_config_path(), trusted=True)


def validate_env_name(name: str) -> str:
    """Accept only an environment variable NAME, so a pasted key is never saved."""
    if _ENV_NAME.fullmatch(name) is None:
        raise LLMConfigError(
            "The API key setting must be the NAME of an environment variable "
            "(e.g. ANTHROPIC_API_KEY), not the key itself."
        )
    return name


def save_llm_config(config: ProviderConfig, path: Path) -> Path:
    if config.api_key_env:
        validate_env_name(config.api_key_env)
    data = {"llm": {k: v for k, v in asdict(config).items() if v is not None}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    except OSError as exc:
        raise LLMConfigError(f"Could not write {path}: {exc.strerror or exc}.") from None
    return path


def load_llm_config(location: ConfigLocation | Path) -> ProviderConfig:
    if isinstance(location, Path):
        location = ConfigLocation(location, trusted=True)
    path = location.path
    if not path.exists():
        raise LLMConfigError(f"No configuration at {path}; run 'xcalibur init'.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LLMConfigError(f"Could not read {path}: {exc.strerror or exc}.") from None
    except yaml.YAMLError as exc:
        raise LLMConfigError(f"{path} is not valid YAML: {exc}.") from None
    llm = data.get("llm") if isinstance(data, dict) else None
    if not isinstance(llm, dict):
        raise LLMConfigError(f"{path} has no 'llm' section; run 'xcalibur init'.")
    _check_settings(llm, path)
    config = ProviderConfig(**llm)
    if config.api_key_env:
        validate_env_name(config.api_key_env)
    if config.base_url and not location.trusted:
        raise LLMConfigError(
            f"{path} sets base_url, which is not allowed in a workspace configuration "
            "(it would send your API key to that server). Set it in your user configuration "
            "or pass the file explicitly with --config."
        )
    return config


def _check_settings(llm: dict[str, Any], path: Path) -> None:
    unknown = sorted(set(llm) - set(_TYPES))
    if unknown:
        raise LLMConfigError(f"{path}: unknown llm settings {', '.join(unknown)}.")
    required = [f.name for f in fields(ProviderConfig) if f.default is MISSING]
    missing = [name for name in required if name not in llm]
    if missing:
        raise LLMConfigError(f"{path}: missing llm settings {', '.join(missing)}.")
    for name, value in llm.items():
        expected = _TYPES[name]
        is_bool = isinstance(value, bool)
        ok = isinstance(value, expected) and (bool in expected or not is_bool)
        if not ok:
            kinds = " or ".join("null" if t is type(None) else t.__name__ for t in expected)
            raise LLMConfigError(f"{path}: '{name}' must be {kinds}, got {value!r}.")
    for name in ("max_tokens", "timeout"):
        if name in llm and llm[name] <= 0:
            raise LLMConfigError(f"{path}: '{name}' must be greater than zero.")
    if llm.get("max_retries", 0) < 0:
        raise LLMConfigError(f"{path}: 'max_retries' cannot be negative.")
