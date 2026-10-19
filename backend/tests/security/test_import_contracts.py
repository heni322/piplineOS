from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from tests.conftest import BACKEND


def test_import_contracts_hold() -> None:
    exe = shutil.which("lint-imports") or str(Path(sys.executable).parent / "lint-imports")
    # Decode as UTF-8 whatever the platform: on Windows the default code page cannot decode some of
    # lint-imports' output when PYTHONIOENCODING=utf-8, which left stdout as None (seen on the owner's PC).
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([exe], cwd=BACKEND, capture_output=True, encoding="utf-8", errors="replace", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 kept, 0 broken" in r.stdout
