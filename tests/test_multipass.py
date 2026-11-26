"""Multi-pass solving: solve/step/analyze outputs can be fed back as inputs."""
import json
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


# ------------------------------------------------------------ program identity
from callme import CallGraphPass, Facts, analyze_file, analyze_source, run_passes
from callme.__main__ import main as cli
from callme.callgraph import build_callgraph
from callme.icfg import icfg_from_state
from callme.persist import (facts_from_dict, facts_to_dict, load_state, save_state,
                            state_from_dict, state_to_dict)
from callme.solver import init_state

SRC = "def f(): pass\nf()\n"


def test_reparsed_unchanged_program_is_accepted():
    s = solve(build_program(SRC, "t.py"))
    again = solve(build_program(SRC, "t.py"), s)
    assert again.same_result(s)


@pytest.mark.parametrize("src, name", [("def g(): pass\ng()\n", "t.py"), (SRC, "other.py")])
def test_edited_or_renamed_program_is_rejected(src, name):
    s = solve(build_program(SRC, "t.py"))
    with pytest.raises(ValueError, match="different program"):
        solve(build_program(src, name), s)


def test_callgraph_keeps_state_for_chaining(tmp_path):
    path = tmp_path / "m.py"
    path.write_text("def f(): pass\ndef dead(): f()\nf()\n")
    cg1 = analyze_file(path, id_style="short")
    cg2 = analyze_file(path, id_style="short", state=cg1.state, include_unreachable=True)
    assert ("dead", "f") in cg2.function_edges() and ("dead", "f") not in cg1.function_edges()
    direct = analyze_file(path, id_style="short", include_unreachable=True)
    assert cg2.state.same_result(direct.state)
    path.write_text("def f(): pass\n")
    with pytest.raises(ValueError):
        analyze_file(path, state=cg1.state)


# ------------------------------------------------------------ widening
@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
@pytest.mark.parametrize("k", [1, 2])
def test_widened_states_refeed_and_chain(path, k):
    prog = parse_file(path)
    s1 = solve(prog, max_contexts=k)
    assert solve(prog, s1).same_result(s1)
    # which contexts get merged depends on pass order; function-level edges do not
    chained = build_callgraph(prog, solve(prog, s1, include_unreachable=True)).function_edges()
    direct = build_callgraph(prog, solve(prog, include_unreachable=True, max_contexts=k)).function_edges()
    assert chained == direct


# ------------------------------------------------------------ step / analyze
@pytest.mark.parametrize("path", CASES[:10], ids=lambda p: p.stem)
def test_manual_step_loop_equals_solve(path):
    prog = parse_file(path)
    s = init_state(prog)
    for _ in range(1000):
        if not step(prog, s).changed:
            break
    assert s.same_result(solve(prog))


def test_analyze_joins_explicit_env():
    prog = build_program("def a(): pass\ndef b(): pass\nx = a\nx()\n", "t.py")
    s = solve(prog)
    mod = s.order[0]
    a_tok = next(iter(s.envs[mod]["x"]))
    b_tok = next(iter(s.envs[mod]["b"]))
    default = analyze(mod, s.copy(), prog)
    assert analyze(mod, s.copy(), prog, s.envs[mod]) == default
    env, _, calls = analyze(mod, s.copy(), prog, {"x": frozenset([b_tok])})
    assert env["x"] >= {a_tok, b_tok}  # seeded value is joined, not replaced
    assert {callee.func.qualname for _, callee in calls} == {"a", "b"}


def test_analyze_only_registers_contexts():
    prog = build_program("def f(g): g()\ndef h(): pass\nf(h)\n", "t.py")
    s = init_state(prog)
    analyze(s.order[0], s, prog)
    assert len(s.order) == 2  # f[g={h}] registered ...
    assert not s.envs and not s.ret and not s.edges  # ... but nothing written back


# ------------------------------------------------------------ pipeline
class ReachPass:
    """Toy second analysis: functions reachable in the call graph (reads its facts)."""

    name = "reach"

    class S:
        def __init__(self, funcs=()):
            self.funcs = set(funcs)

        def copy(self):
            return ReachPass.S(self.funcs)

        def same_result(self, other):
            return self.funcs == other.funcs

    def init(self, prog, facts, state):
        return state or ReachPass.S()

    def step(self, prog, facts):
        st, cg = facts[self.name], facts["callgraph"]
        new = {n.func for n in cg.order} - st.funcs
        st.funcs |= new
        return bool(new)


def test_pipeline_matches_solve_and_is_idempotent():
    prog = parse_file(CASES[7])
    facts = run_passes(prog, [CallGraphPass(), ReachPass()])
    assert facts["callgraph"].same_result(solve(prog))
    assert facts["reach"].funcs == {n.func for n in facts["callgraph"].order}
    assert run_passes(prog, [CallGraphPass(), ReachPass()], facts).same_result(facts)


def test_pipeline_keeps_unlisted_states_and_alternates():
    prog = build_program("def f(): pass\ndef dead(): pass\nf()\n", "t.py")
    facts = run_passes(prog, [CallGraphPass(), ReachPass()])
    later = run_passes(prog, [CallGraphPass(include_unreachable=True)], facts)
    assert later["reach"].same_result(facts["reach"])  # not run, kept
    both = run_passes(prog, [CallGraphPass(include_unreachable=True), ReachPass()], facts)
    assert "dead" in {f.qualname for f in both["reach"].funcs}
    assert "dead" not in {f.qualname for f in facts["reach"].funcs}  # input not mutated


def test_pipeline_rejects_other_program_and_non_convergence():
    a, b = build_program(SRC, "a.py"), build_program(SRC, "b.py")
    with pytest.raises(ValueError):
        run_passes(b, [CallGraphPass()], run_passes(a, [CallGraphPass()]))

    class Never:
        name = "never"

        def init(self, prog, facts, state):
            return ReachPass.S()

        def step(self, prog, facts):
            return True

    with pytest.raises(RuntimeError, match="never"):
        run_passes(a, [Never()], max_rounds=3)


def test_icfg_from_state_matches_callgraph_icfg():
    prog = parse_file(CASES[7])
    s = solve(prog)
    assert set(icfg_from_state(prog, s).edges()) == set(build_callgraph(prog, s).icfg().edges())


# ------------------------------------------------------------ persistence
@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_state_roundtrip(path):
    prog = parse_file(path)
    for kw in ({}, {"include_unreachable": True}, {"max_contexts": 1}):
        s = solve(prog, **kw)
        d = json.loads(json.dumps(state_to_dict(s)))
        back = state_from_dict(parse_file(path), d)
        assert back.same_result(s) and back.max_ctx == s.max_ctx
        assert solve(prog, back).same_result(s)  # resumable


def test_load_rejects_other_program(tmp_path):
    save_state(solve(build_program(SRC, "t.py")), tmp_path / "s.json")
    with pytest.raises(ValueError):
        load_state(build_program(SRC + "\n", "t.py"), tmp_path / "s.json")


def test_facts_roundtrip_and_missing_codec():
    prog = parse_file(CASES[7])
    facts = run_passes(prog, [CallGraphPass()])
    assert facts_from_dict(prog, facts_to_dict(facts)).same_result(facts)
    with pytest.raises(ValueError, match="no codec"):
        facts_to_dict(run_passes(prog, [CallGraphPass(), ReachPass()]))


def test_cli_save_then_resume_equals_single_run(tmp_path):
    case = str(CASES[3])
    cli([case, "--save-state", str(tmp_path / "s.json")])
    cli([case, "--load-state", str(tmp_path / "s.json"), "--include-unreachable",
         "-o", str(tmp_path / "chained.json")])
    cli([case, "--include-unreachable", "-o", str(tmp_path / "direct.json")])
    chained, direct = (json.loads((tmp_path / f).read_text()) for f in ("chained.json", "direct.json"))
    assert chained["edges"] == direct["edges"] and chained["sites"] == direct["sites"]
