"""Feature-family selection.

Example cases live in examples/cases/<family>/ and their tests are marked
`@pytest.mark.family(<family>)`; other test files may opt in with
`pytestmark = pytest.mark.family("classes")`. Which families run is set in pytest.ini:

    families = all                          # family names, or `all` (every family)
    exclude_families = classes modules      # subtracted from `families`

and can be replaced for one run on the command line:

    pytest --families all                   # everything
    pytest --families hof,stdlib            # only these
    pytest --exclude-families modules       # everything but these

Tests of other families are deselected; tests without a family mark always run.
Selected tests are ordered family by family (families ranked by their lowest case number).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CASES = Path(__file__).resolve().parent.parent / "examples" / "cases"
ALL = "all"
_NUM = re.compile(r"^(\d+)_")
_SELECTION = pytest.StashKey[tuple[list[str], list[str]]]()


def case_families() -> list[str]:
    """Family folders of examples/cases, ordered by the lowest case number each holds."""
    def rank(d: Path) -> tuple[int, str]:
        nums = [int(m.group(1)) for p in d.iterdir() if (m := _NUM.match(p.name))]
        return min(nums, default=1 << 30), d.name

    dirs = [d for d in CASES.iterdir()
            if d.is_dir() and not d.name.startswith((".", "_")) and not (d / "main.py").is_file()]
    return [d.name for d in sorted(dirs, key=rank)]


def _family(item: pytest.Item) -> str | None:
    mark = item.get_closest_marker("family")
    return str(mark.args[0]) if mark and mark.args else None


def _names(values: list[str]) -> list[str]:
    """Split ini / CLI values on commas and whitespace."""
    return [n for v in values for n in v.replace(",", " ").split()]


def _selection(config: pytest.Config, known: list[str]) -> tuple[list[str], list[str]]:
    """(selected, excluded) families, both in `known` order."""
    cli_include: str | None = config.getoption("families")
    cli_exclude: str | None = config.getoption("exclude_families")
    if cli_include is not None or cli_exclude is not None:  # the CLI pair replaces the ini pair
        include, exclude = _names([cli_include or ALL]), _names([cli_exclude or ""])
    else:
        include = _names(config.getini("families"))
        exclude = _names(config.getini("exclude_families"))

    unknown = sorted({n for n in include + exclude if n != ALL and n not in known})
    if unknown:
        raise pytest.UsageError(
            f"unknown test famil{'y' if len(unknown) == 1 else 'ies'}: {', '.join(unknown)}"
            f" (known: {', '.join(known)}, or {ALL})")

    inc = set(known) if not include or ALL in include else set(include)
    exc = set(known) if ALL in exclude else set(exclude)
    selected = [f for f in known if f in inc and f not in exc]
    return selected, [f for f in known if f not in selected]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini("families", f"feature families to test (folders of examples/cases), or {ALL}",
                  type="args", default=[ALL])
    parser.addini("exclude_families", "feature families to leave out", type="args", default=[])
    group = parser.getgroup("callme", "feature families")
    group.addoption("--families", metavar="NAMES",
                    help=f"comma-separated families to test, or {ALL} (replaces the pytest.ini selection)")
    group.addoption("--exclude-families", metavar="NAMES",
                    help="comma-separated families to leave out (replaces the pytest.ini selection)")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "family(name): feature family of a test (see tests/conftest.py)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    known = case_families()
    known += sorted({f for it in items if (f := _family(it)) is not None} - set(known))
    selected, excluded = _selection(config, known)
    config.stash[_SELECTION] = (selected, excluded)

    rank = {f: i for i, f in enumerate(selected)}
    keep: list[pytest.Item] = []
    drop: list[pytest.Item] = []
    for it in items:
        fam = _family(it)
        (keep if fam is None or fam in rank else drop).append(it)
    if drop:
        config.hook.pytest_deselected(items=drop)
    def order(it: pytest.Item) -> int:  # unmarked tests last; sort is stable within a family
        fam = _family(it)
        return len(rank) if fam is None else rank[fam]

    items[:] = sorted(keep, key=order)


def pytest_report_collectionfinish(config: pytest.Config) -> list[str]:
    if _SELECTION not in config.stash:
        return []
    selected, excluded = config.stash[_SELECTION]
    line = f"families: {', '.join(selected) or '(none)'}"
    return [line + (f" (excluded: {', '.join(excluded)})" if excluded else "")]
