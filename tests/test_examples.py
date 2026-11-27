"""Check callme against the ground truth in examples/cases (see examples/cases/README.md).

Each case's docstring lists `caller -> callee` edges under headers such as `EXPECTED`,
`STATIC_ONLY` and `SPAWN`, and optionally `caller:line -> callee` lines under `SITES`. Per case:

  test_sound          every EXPECTED edge is found
  test_precise        nothing is found outside EXPECTED | STATIC_ONLY | SPAWN
  test_sites          (cases with SITES) each listed call line resolves to exactly the listed callees
  test_flow_refines   the flow-sensitive edges are a subset of the flow-insensitive ones

Cases are grouped into feature families (examples/cases/<family>/); which families run is
configured in pytest.ini (`families`, `exclude_families`) or with `--families` /
`--exclude-families`, see tests/conftest.py. Test ids are `<family>/<case>`, so -k also works:
`pytest -k 13_`, `pytest -k "stdlib and sound"`, `pytest --families all -k sound -x`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from callme import CallGraph, analyze_file

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CASES = EXAMPLES / "cases"
ALLOWED_EXTRA = ("STATIC_ONLY", "SPAWN")

Edge = tuple[str, str]
_HEADER = re.compile(r"^([A-Z_]+)\b[^:]*:\s*$")


def _is_case(p: Path) -> bool:
    """A single-file case (<case>.py) or a multi-file case (<case>/main.py)."""
    return p.suffix == ".py" if p.is_file() else (p / "main.py").is_file()


def _cases() -> list[tuple[str, Path]]:
    """(family, case) for every case in examples/cases/<family>/ (conftest orders the families)."""
    stray = sorted(p.name for p in CASES.iterdir() if _is_case(p))
    if stray:
        raise RuntimeError(f"cases must live in a family folder of {CASES}: move {', '.join(stray)}")
    families = [d for d in CASES.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))]
    return [(fam.name, case)
            for fam in sorted(families)
            for case in sorted(p for p in fam.iterdir() if _is_case(p))]


def _entry(case: Path) -> Path:
    return case / "main.py" if case.is_dir() else case


def parse_blocks(doc: str) -> dict[str, set[Edge]]:
    """Map each docstring header (EXPECTED, STATIC_ONLY, ...) to its indented edge lines."""
    out: dict[str, set[Edge]] = {}
    key: str | None = None
    for line in doc.splitlines():
        if m := _HEADER.match(line):
            name = str(m.group(1))
            key = name
            out[name] = set()
        elif key and line.startswith((" ", "\t")) and " -> " in line:
            caller, callee = line.strip().split(" -> ", 1)
            out[key].add((caller.strip(), callee.strip()))
        elif not line.startswith((" ", "\t")):
            key = None
    return out


def _expectations(case: Path) -> dict[str, set[Edge]]:
    src = _entry(case).read_text(encoding="utf-8")
    blocks = parse_blocks(ast.get_docstring(ast.parse(src)) or "")
    if "EXPECTED" not in blocks:
        pytest.skip(f"{case.name}: no EXPECTED block")
    return blocks


def _callgraph(case: Path, *, flow_sensitive: bool = True) -> CallGraph:
    if case.is_dir():  # module-qualified names (main.main, pkg.core.run), relative to the case
        return analyze_file(case / "main.py", id_style="module", flow_sensitive=flow_sensitive)
    return analyze_file(case, id_style="short", flow_sensitive=flow_sensitive)


def _analyze(case: Path, *, flow_sensitive: bool = True) -> set[Edge]:
    return set(_callgraph(case, flow_sensitive=flow_sensitive).function_edges())


def _fmt(title: str, edges: set[Edge]) -> str:
    lines = "\n".join(f"  {c} -> {d}" for c, d in sorted(edges)) or "  (none)"
    return f"{title}:\n{lines}"


CASE_PARAMS = [pytest.param(c, id=f"{family}/{c.stem if c.is_file() else c.name}",
                            marks=pytest.mark.family(family))
               for family, c in _cases()]


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_sound(case: Path) -> None:
    expected = _expectations(case)["EXPECTED"]
    got = _analyze(case)
    missing = expected - got
    assert not missing, "\n".join([
        f"{case.name}: {len(missing)} of {len(expected)} expected edges missing",
        _fmt("missing", missing), _fmt("found", got)])


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_precise(case: Path) -> None:
    blocks = _expectations(case)
    allowed = blocks["EXPECTED"].union(*(blocks.get(k, set()) for k in ALLOWED_EXTRA))
    got = _analyze(case)
    extra = got - allowed
    assert not extra, "\n".join([
        f"{case.name}: {len(extra)} edges outside EXPECTED | {' | '.join(ALLOWED_EXTRA)}",
        _fmt("extra", extra), _fmt("allowed", allowed)])


def _has_sites(case: Path) -> bool:
    doc = ast.get_docstring(ast.parse(_entry(case).read_text(encoding="utf-8"))) or ""
    return "SITES" in parse_blocks(doc)


SITE_PARAMS = [p for p in CASE_PARAMS if _has_sites(p.values[0])]  # type: ignore[arg-type]


@pytest.mark.parametrize("case", SITE_PARAMS)
def test_sites(case: Path) -> None:
    """Per call line: the analysis reports exactly the traced callees (`caller:line -> callee`)."""
    want: dict[tuple[str, int], set[str]] = {}
    for at, callee in _expectations(case)["SITES"]:
        caller, line = at.rsplit(":", 1)
        want.setdefault((caller, int(line)), set()).add(callee)
    cg = _callgraph(case)
    wrong: list[str] = []
    for (caller, line), callees in sorted(want.items()):
        got = set().union(*(cg.targets(s) for s in cg.sites
                            if cg.name(s.caller) == caller and s.line == line))
        if got != callees:
            wrong.append(f"  {caller}:{line}  expected {sorted(callees)}, found {sorted(got)}")
    assert not wrong, f"{case.name}: {len(wrong)} call lines resolve differently\n" + "\n".join(wrong)


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_flow_refines(case: Path) -> None:
    """Flow sensitivity may only remove edges: its graph is a subgraph of the flow-insensitive one."""
    fs = _analyze(case)
    fi = _analyze(case, flow_sensitive=False)
    assert fs <= fi, "\n".join([f"{case.name}: flow-sensitive edges missing from the flow-insensitive graph",
                                 _fmt("only flow-sensitive", fs - fi)])


def _smoke() -> list[Path]:
    """Loose examples without ground truth: top-level *.py and every <dir>/main.py outside cases/."""
    mains = [d / "main.py" for d in EXAMPLES.iterdir()
             if d.is_dir() and d != CASES and (d / "main.py").is_file()]
    return sorted(EXAMPLES.glob("*.py")) + sorted(mains)


@pytest.mark.parametrize("path", _smoke(), ids=lambda p: str(p.relative_to(EXAMPLES)))
def test_example_runs(path: Path) -> None:
    analyze_file(path)
