from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from tests.conftest import BACKEND


def test_import_contracts_hold() -> None:
    exe = shutil.which("lint-imports") or str(Path(sys.executable).parent / "lint-imports")
    r = subprocess.run([exe], cwd=BACKEND, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 kept, 0 broken" in r.stdout
