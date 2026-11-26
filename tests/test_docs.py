"""The explanations in docs/ show real analyzer output: keep them in sync with the code.

Fails when a page names code that no longer exists (a broken code-ref) or when
docs/generated/data.js is older than the code it describes. To fix: run `python docs/build.py`,
then open the chapters whose scenes changed with `?selftest` (see docs/README.md).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "docs" / "build.py"


def test_docs_are_current() -> None:
    r = subprocess.run([sys.executable, str(BUILD), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
