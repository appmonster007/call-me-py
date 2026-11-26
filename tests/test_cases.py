"""Run the analyzer on examples/cases and compare with each docstring's EXPECTED list.

Soundness check: every EXPECTED (traced) edge must be in the computed graph.
STATIC_ONLY edges (dead code a static analysis still reports) must be present too.
Extra edges are imprecision, reported but tolerated only where listed in ALLOWED_EXTRA.
"""
import re
from pathlib import Path

import pytest

from callme import analyze_file

CASES = Path(__file__).resolve().parent.parent / "examples" / "cases"
EDGE = re.compile(r"^\s+(\S+) -> (\S+)$", re.M)

# Cases inside the supported core: defs, lambdas, closures, defaults, *args/**kwargs,
# function arguments/returns, comprehensions, shadowing, decorators, HOF recursion.
CORE = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "12", "13", "25", "26", "27"]


def section(src: str, name: str) -> set:
    m = re.search(name + r"[^\n]*\n(.*?)(?=\n[A-Z_]+[ (:]|\"\"\")", src, re.S)
    return set(EDGE.findall(m.group(1))) if m else set()


def case_files():
    return [next(CASES.glob(f"{n}_*.py")) for n in CORE]


@pytest.mark.parametrize("path", case_files(), ids=lambda p: p.stem)
def test_expected_edges_are_found(path):
    src = path.read_text()
    expected = section(src, "EXPECTED") | section(src, "STATIC_ONLY")
    got = analyze_file(path, id_style="short", include_unreachable=True).function_edges()
    assert expected <= got, f"missing: {sorted(expected - got)}"


@pytest.mark.parametrize("path", case_files(), ids=lambda p: p.stem)
def test_no_spurious_edges_beyond_static_only(path):
    """Context-sensitive analysis should not add edges beyond traced + static-only ones."""
    src = path.read_text()
    allowed = section(src, "EXPECTED") | section(src, "STATIC_ONLY")
    got = analyze_file(path, id_style="short", include_unreachable=True).function_edges()
    assert got <= allowed, f"extra: {sorted(got - allowed)}"
