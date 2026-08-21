"""Configuration helpers that never print secret values."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path | str | None, override: bool = False) -> None:
    if not path:
        return
    env_path = Path(path).expanduser().resolve()
    if not env_path.exists():
        raise FileNotFoundError(f"env file not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and (override or name not in os.environ):
            os.environ[name] = value


def load_runtime_config(path: Path | str | None = None) -> Dict[str, Any]:
    cfg_path = Path(path).resolve() if path else PROJECT_ROOT / "config.yaml"
    with cfg_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def resolve_project_path(value: str, default: str) -> Path:
    raw = value or default
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()
