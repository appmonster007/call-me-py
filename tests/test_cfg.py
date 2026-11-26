"""CFG builder and ICFG construction."""
import json
import sys
import textwrap
from pathlib import Path

import pytest

from callme import analyze_file, analyze_source, to_icfg_dot, to_json
from callme.extract import build_program, call_sites, parse_file
from callme.model import (AFTER_CALL, BRANCH, CALL, CALL_EDGE, CALL_TO_RETURN, EXC, FALSE,
                          LOOP, RETURN_EDGE, TRUE)

CASES = sorted((Path(__file__).resolve().parent.parent / "examples" / "cases").glob("*.py"))


def cfg_of(src, name):
    prog = build_program(textwrap.dedent(src), "t.py")
    return next(c for f, c in prog.cfgs.items() if f.qualname == name)


def find(cfg, kind, text=None):
    return next(n.idx for n in cfg.nodes if n.kind == kind and (text is None or n.text == text))


def succ(cfg, i, kind=None):
    return {e.dst for e in cfg.succ(i) if kind is None or e.kind == kind}


def reachable(cfg, start):
    seen, todo = set(), [start]
    while todo:
        i = todo.pop()
        if i not in seen:
            seen.add(i)
            todo.extend(succ(cfg, i))
    return seen


# ------------------------------------------------------------ CFG shape
def test_call_split_into_call_and_after_call():
    c = cfg_of("def f():\n    return g(h())\n", "f")
    inner, outer = c.calls[next(s for s in c.calls if s.text == "h()")], \
        c.calls[next(s for s in c.calls if s.text == "g(h())")]
    for call, ret in (inner, outer):
        assert c.nodes[call].kind == CALL and c.nodes[ret].kind == AFTER_CALL
        assert succ(c, call, CALL_TO_RETURN) == {ret}
    assert inner[1] in {e.src for e in c.pred(outer[0])}  # arguments are evaluated first


def test_if_has_true_and_false_branches():
    c = cfg_of("""
        def f(x):
            if x:
                a()
            else:
                b()
            z()
    """, "f")
    br = find(c, BRANCH)
    assert succ(c, br, TRUE) == {c.calls[next(s for s in c.calls if s.text == "a()")][0]}
    assert succ(c, br, FALSE) == {c.calls[next(s for s in c.calls if s.text == "b()")][0]}


def test_while_back_edge_and_break():
    c = cfg_of("""
        def f():
            while cond():
                if stop():
                    break
                body()
            after()
    """, "f")
    head = find(c, LOOP)
    body_ret = c.calls[next(s for s in c.calls if s.text == "body()")][1]
    assert head in reachable(c, body_ret) and head != body_ret
    brk = find(c, "stmt", "break")
    after = c.calls[next(s for s in c.calls if s.text == "after()")][0]
    assert after in succ(c, brk)


def test_return_runs_finally_first():
    c = cfg_of("""
        def f():
            try:
                return a()
            finally:
                cleanup()
    """, "f")
    ret = find(c, "stmt", "return a()")
    fin = c.calls[next(s for s in c.calls if s.text == "cleanup()")][0]
    assert c.exit not in succ(c, ret)
    assert fin in succ(c, ret)
    assert c.exit in reachable(c, fin)


def test_try_body_points_reach_handler():
    c = cfg_of("""
        def f():
            try:
                a()
            except E:
                h()
    """, "f")
    handler = find(c, "handler")
    a_call = c.calls[next(s for s in c.calls if s.text == "a()")][0]
    assert handler in succ(c, a_call, EXC)


def test_comprehension_loops_and_short_circuit():
    c = cfg_of("def f(xs):\n    return [g(x) for x in xs if p(x)] or d()\n", "f")
    head = find(c, LOOP)
    g_ret = c.calls[next(s for s in c.calls if s.text == "g(x)")][1]
    assert head in succ(c, g_ret)
    d_call = c.calls[next(s for s in c.calls if s.text == "d()")][0]
    assert any(e.kind == FALSE for e in c.pred(d_call))  # `or` evaluates d() only when falsy


def test_decorators_are_call_points_in_enclosing_function():
    c = cfg_of("@deco\ndef f(): pass\n", "<module>")
    assert any(s.kind == "decorator" and s.text == "@deco" for s in c.calls)


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_cfg_well_formed(path):
    prog = parse_file(path)
    for fid, c in prog.cfgs.items():
        assert set(call_sites(prog, fid)) <= set(c.calls), fid  # every syntactic site has points
        for e in c.edges:
            assert 0 <= e.src < len(c.nodes) and 0 <= e.dst < len(c.nodes)
        assert not c.succ(c.exit)
        assert c.exit in reachable(c, c.entry) or not prog.funcs[fid].body


# ------------------------------------------------------------ ICFG
@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_icfg_call_and_return_edges_match(path):
    for unreachable in (False, True):
        cg = analyze_file(path, include_unreachable=unreachable)
        g = cg.icfg()
        calls = list(g.call_edges())
        rets = list(g.return_edges())
        assert len(calls) == len(rets) == len(cg.edges)
        for c, r in zip(calls, rets):
            assert c.kind == CALL_EDGE and r.kind == RETURN_EDGE and c.site == r.site
            assert (c.dst, c.dst_pt) == g.entry(c.dst)
            assert (r.src, r.src_pt) == g.exit(c.dst)
            assert r.dst == c.src  # returns go back to the calling context only
            cfg = g.cfg(c.src)
            assert cfg.nodes[c.src_pt].kind == CALL and cfg.nodes[r.dst_pt].kind == AFTER_CALL
            assert succ(cfg, c.src_pt, CALL_TO_RETURN) == {r.dst_pt}


def test_icfg_contexts_have_separate_points():
    cg = analyze_source(textwrap.dedent("""
        def sq(x): return x
        def db(x): return x
        def apply(f, v): return f(v)
        apply(sq, 1)
        apply(db, 2)
    """), "t.py", id_style="short")
    g = cg.icfg()
    applies = [n for n in cg.nodes if n.func.qualname == "apply"]
    assert len(applies) == 2
    callees = set()
    for n in applies:  # each context calls only its own callee from its own CALL point
        (call_pt, _), = g.cfg(n).calls.values()
        out = [e for e in g.succ((n, call_pt)) if e.kind == CALL_EDGE]
        assert len(out) == 1
        callees.add(out[0].dst.func.qualname)
    assert callees == {"sq", "db"}
    assert sum(1 for _ in g.points()) == sum(len(g.cfg(n).nodes) for n in cg.nodes)


def test_icfg_exports():
    cg = analyze_source("def f(): pass\nf()\n", "t.py", id_style="short")
    d = json.loads(to_json(cg, icfg=True))["icfg"]
    assert set(d["cfgs"]) == {"<module>", "f"}
    assert sorted(e["kind"] for e in d["edges"]) == ["call", "return"]
    dot = to_icfg_dot(cg)
    assert dot.startswith("digraph icfg") and "cluster_1" in dot


# ------------------------------------------------------------ analysis and CFG agree
def edges_of(src):
    cg = analyze_source(textwrap.dedent(src), "t.py", id_style="short")
    return cg, cg.function_edges()


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_every_edge_site_is_a_listed_site(path):
    for unreachable in (False, True):
        cg = analyze_file(path, include_unreachable=unreachable)
        assert {e.site for e in cg.edges} <= set(cg.sites)


def test_class_body_calls_are_sites():
    cg, edges = edges_of("""
        def f(): pass
        class C:
            x = f()
    """)
    assert ("<module>", "f") in edges
    assert any(s.text == "f()" for s in cg.sites)


def test_class_decorator_is_applied():
    _, edges = edges_of("""
        def deco(cls): return cls
        @deco
        class C: pass
    """)
    assert ("<module>", "deco") in edges


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match needs Python 3.10+")
def test_match_case_bodies_are_analysed():
    cg, edges = edges_of("""
        def a(): pass
        def b(): pass
        def run(cmd, f):
            match cmd:
                case "a" if a():
                    f()
                case [g, *_]:
                    g()
        run("a", b)
    """)
    assert {("run", "a"), ("run", "b")} <= edges
    c = cg.prog.cfgs[next(f for f in cg.prog.funcs if f.qualname == "run")]
    assert sum(n.kind == BRANCH for n in c.nodes) >= 3  # two cases + one guard
