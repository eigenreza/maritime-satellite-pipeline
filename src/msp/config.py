"""Configuration: one YAML file (config/pipeline.yaml) plus a few environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("MSP_ROOT", Path(__file__).resolve().parents[2]))


class Config(dict):
    """Dictionary with attribute access for nested sections."""

    def __getattr__(self, key: str) -> Any:
        try:
            value = self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc
        return Config(value) if isinstance(value, dict) else value


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> Config:
    p = Path(path or os.environ.get("MSP_CONFIG", ROOT / "config" / "pipeline.yaml"))
    with open(p, encoding="utf-8") as f:
        return Config(yaml.safe_load(f))


def db_url() -> str:
    return os.environ.get("MSP_DB_URL", "postgresql://msp:msp@localhost:15432/msp")
