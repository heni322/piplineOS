"""Versioned prompt files (ADR-0006): prompts/<agent>/<name>.v<N>.md with YAML front-matter."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
FRONT_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
NAME_RE = re.compile(r"^(?P<name>[a-z0-9_]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class PromptFile:
    id: str
    version: int
    model_tier: Literal["volume", "reasoning"]
    max_tokens: int
    budget_micros_gbp: int | None
    body: str
    sha256: str
    path: Path

    def render(self, **values: str) -> str:
        out = self.body
        for k, v in values.items():
            out = out.replace("{{" + k + "}}", v)
        if "{{" in out:
            missing = re.findall(r"\{\{(\w+)\}\}", out)
            raise ValueError(f"prompt {self.id}.v{self.version}: unfilled placeholders {missing}")
        return out


def load_prompt(rel_path: str, root: Path = PROMPTS_DIR) -> PromptFile:
    path = root / rel_path
    m = NAME_RE.match(path.name)
    if not m:
        raise ValueError(f"bad prompt file name: {path.name}")
    raw = path.read_text(encoding="utf-8")
    fm = FRONT_RE.match(raw)
    if not fm:
        raise ValueError(f"{rel_path}: missing front-matter")
    meta = yaml.safe_load(fm.group(1))
    if meta.get("version") != int(m.group("version")):
        raise ValueError(f"{rel_path}: front-matter version does not match file name")
    return PromptFile(
        id=meta["id"],
        version=meta["version"],
        model_tier=meta["model_tier"],
        max_tokens=int(meta.get("max_tokens", 1024)),
        budget_micros_gbp=meta.get("budget_micros_gbp"),
        body=fm.group(2),
        sha256=hashlib.sha256(raw.encode()).hexdigest(),
        path=path,
    )


def compute_lock(root: Path = PROMPTS_DIR) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*.md"))}


def check_lock(root: Path = PROMPTS_DIR) -> list[str]:
    """Locked prompt versions are immutable. Returns a list of violations (empty = OK)."""
    lock_file = root / "LOCK.json"
    locked: dict[str, str] = json.loads(lock_file.read_text()) if lock_file.exists() else {}
    current = compute_lock(root)
    problems = [f"{k}: locked prompt changed" for k, h in locked.items() if current.get(k) not in (None, h)]
    problems += [f"{k}: locked prompt deleted" for k in locked if k not in current]
    return problems
