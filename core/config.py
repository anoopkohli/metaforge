"""Environment-driven configuration. No secrets have defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    db_path: str
    auth_secret: str
    model: str

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("METAFORGE_AUTH_SECRET", "")
        if not secret:
            raise ConfigError(
                "METAFORGE_AUTH_SECRET is not set. Copy .env.example, or export a dev secret."
            )
        return cls(
            db_path=os.environ.get("METAFORGE_DB", "metaforge.db"),
            auth_secret=secret,
            model=os.environ.get("METAFORGE_MODEL", "claude-sonnet-5-5"),
        )
