"""Where the versioned config/ directory lives (repo checkout, or /config in containers)."""

from __future__ import annotations

import os
from pathlib import Path


def config_root() -> Path:
    env = os.environ.get("PIPELINEOS_CONFIG")
    if env:
        return Path(env)
    repo = Path(__file__).resolve().parents[2] / "config"
    return repo if repo.exists() else Path("/config")
