"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("AUTOAPPS_DATA_DIR", "data")).resolve())
    model: str = field(default_factory=lambda: os.environ.get("AUTOAPPS_MODEL", "claude-opus-5-5"))
    # Server-side refusal fallbacks on the Claude API. Set to 0 if your provider rejects them.
    llm_fallbacks: bool = field(default_factory=lambda: _bool("AUTOAPPS_LLM_FALLBACKS", True))
    host: str = field(default_factory=lambda: os.environ.get("AUTOAPPS_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("AUTOAPPS_PORT", "8000")))
    # SMTP settings for "your draft is ready" emails. All optional.
    smtp_host: str = field(default_factory=lambda: os.environ.get("AUTOAPPS_SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("AUTOAPPS_SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.environ.get("AUTOAPPS_SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.environ.get("AUTOAPPS_SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: os.environ.get("AUTOAPPS_SMTP_FROM", ""))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "autoapps.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir / "browser-profile"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
