"""Check callme against the ground truth in examples/cases (see examples/cases/README.md).

Each case's docstring lists `caller -> callee` edges under headers such as `EXPECTED`,
`STATIC_ONLY` and `SPAWN`. Per case:

  test_sound    every EXPECTED edge is found
  test_precise  nothing is found outside EXPECTED | STATIC_ONLY | SPAWN

Select with -k, e.g. `pytest -k 13_`, `pytest -k sound -x`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from callme import analyze_file

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CASES = EXAMPLES / "cases"
ALLOWED_EXTRA = ("STATIC_ONLY", "SPAWN")

Edge = tuple[str, str]
_HEADER = re.compile(r"^([A-Z_]+)\b[^:]*:\s*$")


def _cases() -> list[Path]:
    files = [p for p in CASES.glob("*.py")]
    dirs = [d for d in CASES.iterdir() if d.is_dir() and (d / "main.py").is_file()]
    return sorted(files + dirs, key=lambda p: p.name)


def _entry(case: Path) -> Path:
    return case / "main.py" if case.is_dir() else case


def _blocks(doc: str) -> dict[str, set[Edge]]:
    """Map each docstring header (EXPECTED, STATIC_ONLY, ...) to its indented edge lines."""
    out: dict[str, set[Edge]] = {}
    key: str | None = None
    for line in doc.splitlines():
        if m := _HEADER.match(line):
            key = m.group(1)
            out[key] = set()
        elif key and line.startswith((" ", "\t")) and " -> " in line:
            caller, callee = line.strip().split(" -> ", 1)
            out[key].add((caller.strip(), callee.strip()))
        elif not line.startswith((" ", "\t")):
            key = None
    return out


def _expectations(case: Path) -> dict[str, set[Edge]]:
    src = _entry(case).read_text(encoding="utf-8")
    blocks = _blocks(ast.get_docstring(ast.parse(src)) or "")
    if "EXPECTED" not in blocks:
        pytest.skip(f"{case.name}: no EXPECTED block")
    return blocks


def _analyze(case: Path, monkeypatch: pytest.MonkeyPatch) -> set[Edge]:
    if case.is_dir():  # module-qualified names (main.main, pkg.core.run) need paths relative to the case
        monkeypatch.chdir(case)
        cg = analyze_file("main.py", id_style="module")
    else:
        cg = analyze_file(case, id_style="short")
    return set(cg.function_edges())


def _fmt(title: str, edges: set[Edge]) -> str:
    lines = "\n".join(f"  {c} -> {d}" for c, d in sorted(edges)) or "  (none)"
    return f"{title}:\n{lines}"


CASE_PARAMS = [pytest.param(c, id=c.stem if c.is_file() else c.name) for c in _cases()]


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_sound(case: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _expectations(case)["EXPECTED"]
    got = _analyze(case, monkeypatch)
    missing = expected - got
    assert not missing, "\n".join([
        f"{case.name}: {len(missing)} of {len(expected)} expected edges missing",
        _fmt("missing", missing), _fmt("found", got)])


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_precise(case: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocks = _expectations(case)
    allowed = blocks["EXPECTED"].union(*(blocks.get(k, set()) for k in ALLOWED_EXTRA))
    got = _analyze(case, monkeypatch)
    extra = got - allowed
    assert not extra, "\n".join([
        f"{case.name}: {len(extra)} edges outside EXPECTED | {' | '.join(ALLOWED_EXTRA)}",
        _fmt("extra", extra), _fmt("allowed", allowed)])


def _smoke() -> list[Path]:
    """Loose examples without ground truth: top-level *.py and every <dir>/main.py outside cases/."""
    mains = [d / "main.py" for d in EXAMPLES.iterdir()
             if d.is_dir() and d != CASES and (d / "main.py").is_file()]
    return sorted(EXAMPLES.glob("*.py")) + sorted(mains)


@pytest.mark.parametrize("path", _smoke(), ids=lambda p: str(p.relative_to(EXAMPLES)))
def test_example_runs(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(path.parent)
    analyze_file(path.name)
