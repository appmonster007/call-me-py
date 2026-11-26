"""Multi-pass solving: solve/step/analyze outputs can be fed back as inputs."""
from pathlib import Path

import pytest

from callme.extract import build_program, parse_file
from callme.solver import solve, step
from callme.transfer import analyze

CASES = sorted((Path(__file__).resolve().parent.parent / "examples" / "cases").glob("*.py"))


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_solve_is_idempotent(path):
    prog = parse_file(path)
    s1 = solve(prog)
    s2 = solve(prog, s1)
    assert s2.same_result(s1)
    assert not step(prog, s1.copy()).changed  # s1 is already a fixpoint


# Context choice is not monotone in argument values, so a transient empty argument
# (callee return not yet known) can hit a seeded unreachable context and leave a
# spurious value behind. The result then depends on iteration order.
ORDER_DEPENDENT = {"19_decorators"}


@pytest.mark.parametrize("path", [
    pytest.param(p, marks=pytest.mark.xfail(strict=True, reason="order-dependent fixpoint"))
    if p.stem in ORDER_DEPENDENT else p for p in CASES], ids=lambda p: p.stem)
def test_chained_passes_match_single_pass(path):
    prog = parse_file(path)
    chained = solve(prog, solve(prog), include_unreachable=True)
    direct = solve(prog, include_unreachable=True)
    assert chained.same_result(direct)


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_analyze_output_refeeds_stably(path):
    prog = parse_file(path)
    state = solve(prog)
    for node in list(state.order):
        env, ret, calls = analyze(node, state.copy(), prog)
        env2, ret2, calls2 = analyze(node, state.copy(), prog, env)
        assert (env2, ret2, set(calls2)) == (env, ret, set(calls))


def test_input_state_not_mutated_unless_inplace():
    prog = build_program("def f(): pass\nf()\n", "t.py")
    s1 = solve(prog)
    snap = s1.copy()
    solve(prog, s1, include_unreachable=True)
    assert s1.same_result(snap)
    s3 = solve(prog, s1, inplace=True)
    assert s3 is s1


def test_state_from_other_program_rejected():
    a = build_program("def f(): pass\n", "a.py")
    b = build_program("def g(): pass\n", "b.py")
    with pytest.raises(ValueError):
        solve(b, solve(a))


def test_conflicting_max_contexts_rejected():
    prog = build_program("def f(): pass\n", "t.py")
    with pytest.raises(ValueError):
        solve(prog, solve(prog, max_contexts=4), max_contexts=8)
