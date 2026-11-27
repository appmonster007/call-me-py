"""Unit tests: entry-state contexts, return values, termination, exports."""
import json
import textwrap
from typing import cast

from callme import CallGraph, analyze_source, to_dict, to_dot, to_json


def graph(src: str, *, include_unreachable: bool = False,
          max_contexts: int | None = None) -> CallGraph:
    return analyze_source(textwrap.dedent(src), "t.py", "short",
                          include_unreachable=include_unreachable, max_contexts=max_contexts)


def labels(cg: CallGraph) -> set[str]:
    return {cg.labels[n] for n in cg.nodes}


def functions(cg: CallGraph) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", to_dict(cg)["functions"])


APPLY = """
    def square(x): return x
    def double(x): return x
    def apply(f, v): return f(v)
    apply(square, 1)
    apply(double, 2)
"""


def test_functional_contexts_split_by_entry_state() -> None:
    cg = graph(APPLY)
    assert {"apply[f={square}]", "apply[f={double}]"} <= labels(cg)
    # each context calls only its own callee (a call never returns to a different call's context)
    ctx = {(cg.labels[e.caller], cg.labels[e.callee]) for e in cg.edges}
    assert ("apply[f={square}]", "square") in ctx
    assert ("apply[f={double}]", "double") in ctx
    assert ("apply[f={square}]", "double") not in ctx
    assert ("apply[f={double}]", "square") not in ctx


def test_same_entry_state_shares_one_context() -> None:
    cg = graph("""
        def sq(x): return x
        def apply(f, v): return f(v)
        apply(sq, 1)
        apply(sq, 2)
        apply(sq, 3)
    """)
    assert [n for n in labels(cg) if n.startswith("apply")] == ["apply[f={sq}]"]


def test_return_value_flows_back_per_context() -> None:
    cg = graph("""
        def a(): pass
        def b(): pass
        def ident(f): return f
        x = ident(a)
        y = ident(b)
        x()
        y()
    """)
    by_line: dict[int, set[str]] = {}
    for e in cg.edges:
        if e.caller.func.qualname == "<module>":
            by_line.setdefault(e.site.line, set()).add(e.callee.func.qualname)
    assert by_line[7] == {"a"} and by_line[8] == {"b"}  # not {a, b}


def test_closure_captures_defining_context() -> None:
    cg = graph("""
        def inc(x): return x
        def dec(x): return x
        def make(f):
            def h(x): return f(x)
            return h
        make(inc)(1)
        make(dec)(1)
    """)
    pairs = {(cg.labels[e.caller], e.callee.func.qualname) for e in cg.edges}
    h_nodes = [n for n in labels(cg) if n.startswith("make.h")]
    assert len(h_nodes) == 2  # one h per captured f
    assert not any(c.startswith("make.h") and d not in {"inc", "dec"} for c, d in pairs)
    ctx = {(cg.labels[e.caller], cg.labels[e.callee]) for e in cg.edges}
    # captured f must not leak between the two closures
    for caller, callee in ctx:
        if caller.startswith("make.h"):
            assert callee in {"inc", "dec"}
    assert len({callee for caller, callee in ctx if caller.startswith("make.h")}) == 2
    assert len([1 for caller, _ in ctx if caller.startswith("make.h")]) == 2


def test_recursion_terminates_and_has_cycle() -> None:
    cg = graph("""
        def even(n): return odd(n)
        def odd(n): return even(n)
        even(1)
    """)
    assert {("even", "odd"), ("odd", "even")} <= cg.function_edges()


def test_computed_call_reaches_all_possible_targets() -> None:
    # f(n) may reach inc, dec or ide.
    cg = graph("""
        def inc(i): return i
        def dec(j): return j
        def ide(k): return k
        def foo(n, f):
            if n == 0:
                f = ide
            r = f(n)
            return r
        def main():
            x = input()
            if x > 0:
                y = foo(x, inc)
            else:
                y = foo(x, dec)
            return y
        main()
    """)
    assert {("foo", "inc"), ("foo", "dec"), ("foo", "ide")} <= cg.function_edges()


def test_arity_filters_callees() -> None:
    cg = graph("""
        def one(a): pass
        def two(a, b): pass
        def run(f): f(1)
        run(one)
        run(two)
    """)
    assert ("run", "one") in cg.function_edges()
    assert ("run", "two") not in cg.function_edges()


def test_unresolved_calls_produce_no_edges() -> None:
    cg = graph("print(len([1]))")
    assert cg.function_edges() == frozenset()


def test_context_cap_widens_but_stays_sound() -> None:
    fns = "\n".join(f"def f{i}(x): return x" for i in range(6))
    calls = "\n".join(f"apply(f{i}, 1)" for i in range(6))
    cg = graph(fns + "\ndef apply(f, v): return f(v)\n" + calls, max_contexts=3)
    assert {("apply", f"f{i}") for i in range(6)} <= cg.function_edges()


def test_exports() -> None:
    cg = graph(APPLY)
    data = json.loads(to_json(cg))
    assert {"functions", "sites", "edges", "contexts", "context_edges"} <= set(data)
    assert {"caller": "apply", "callee": "square"} in data["edges"]
    site = next(s for s in data["sites"] if s["text"] == "f(v)")
    assert site["targets"] == ["double", "square"]
    dot = to_dot(cg)
    assert dot.startswith("digraph") and '"apply" -> "square"' in dot
    assert '"apply[f={square}]" -> "square"' in to_dot(cg, contexts=True)
    assert "style=dotted" in to_dot(cg, returns=True)
    assert to_dict(cg)["file"] == "t.py"


def test_unreachable_functions_marked() -> None:
    cg = graph("""
        def used(): pass
        def unused(): used()
        used()
    """)
    reach = {f["id"]: f["reachable"] for f in functions(cg)}
    assert reach["used"] and not reach["unused"]
    cg2 = graph("""
        def used(): pass
        def unused(): used()
        used()
    """, include_unreachable=True)
    assert ("unused", "used") in cg2.function_edges()


def test_default_ids_are_file_line_col_qualname() -> None:
    cg = analyze_source("def f(x): return x\ndef f(x): return x+1\ndef g(): return f(1)\ng()\n", "m.py")
    # flow-insensitive: g's call f(1) may reach either definition of f
    assert cg.function_edges() == {
        ("m.py:1:0:<module>", "m.py:3:0:g"),
        ("m.py:3:0:g", "m.py:1:0:f"), ("m.py:3:0:g", "m.py:2:0:f"),
    }
    ids = [f["id"] for f in functions(cg)]
    assert len(ids) == len(set(ids)) == 4


def test_funcid_parts_and_rendering() -> None:
    from callme.model import FuncId
    f = FuncId("Outer.make.inner", "pkg/util.py", 7, 4)
    assert (f.name, f.scope, f.module) == ("inner", "Outer.make", "pkg.util")
    assert f.render("full") == "pkg/util.py:7:4:Outer.make.inner" == str(f)
    assert f.render("module") == "pkg.util.Outer.make.inner"
    assert f.render("short") == "Outer.make.inner"
    assert FuncId("g", "pkg/__init__.py", 1, 0).module == "pkg"


def test_id_style_module() -> None:
    cg = analyze_source("def f(): pass\nf()\n", "pkg/m.py", id_style="module")
    assert ("pkg.m.<module>", "pkg.m.f") in cg.function_edges()


def _site_targets(cg: CallGraph, caller: str) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for e in cg.edges:
        if e.caller.func.qualname == caller:
            out.setdefault(e.site.line, set()).add(e.callee.func.qualname)
    return out


def test_nonlocal_strong_and_weak_updates() -> None:
    # Unconditional nonlocal rebinding: exact before and after each setter call.
    cg = graph("""
        def a(): pass
        def b(): pass
        def c(): pass
        def outer():
            f = a
            def set_b():
                nonlocal f
                f = b
            def set_c():
                nonlocal f
                f = c
            f()
            set_b()
            f()
            set_c()
            f()
        outer()
    """)
    sites = _site_targets(cg, "outer")
    assert sites[13] == {"a"}
    assert sites[15] == {"b"}
    assert sites[17] == {"c"}

    # Conditional nonlocal write stays weak (keeps old binding).
    cg2 = graph("""
        def a(): pass
        def b(): pass
        def outer(flag):
            f = a
            def maybe():
                nonlocal f
                if flag:
                    f = b
            maybe()
            return f()
        outer(True)
    """)
    assert {("outer", "a"), ("outer", "b")} <= cg2.function_edges()


def test_nonlocal_indirect_and_recursive_soundness() -> None:
    # Indirect write through a helper propagates back via State.writes.
    cg = graph("""
        def a(): pass
        def b(): pass
        def run(cb): cb()
        def outer():
            f = a
            def inner():
                nonlocal f
                f = b
            run(inner)
            return f()
        outer()
    """)
    assert {("outer", "a"), ("outer", "b")} <= cg.function_edges()

    # Recursive closure mutating outer activation's nonlocal must reach outer activation.
    cg2 = graph("""
        def a(): pass
        def b(): pass
        def outer(cb=None):
            f = a
            def inner():
                nonlocal f
                f = b
            if cb is not None:
                cb()
            else:
                outer(inner)
            return f()
        outer()
    """)
    assert {("outer", "a"), ("outer", "b")} <= cg2.function_edges()


def test_nonlocal_conditional_expressions_and_stubs_stay_weak() -> None:
    # Short-circuit expressions, comprehensions, try/except, and stubs must not strong-kill.
    cg = graph("""
        def a(): pass
        def b(): pass
        def outer(flag, xs):
            f = a
            def inner(*_):
                nonlocal f
                f = b
            _ = inner() if flag else 0
            f()
            f = a
            _ = flag and inner()
            f()
            f = a
            _ = [inner() for _ in xs]
            f()
            f = a
            list(map(inner, xs))
            f()
            f = a
            try:
                inner()
            except ValueError:
                f()
        outer(False, [])
    """)
    sites = _site_targets(cg, "outer")
    for line in (10, 13, 16, 19, 24):
        assert sites[line] == {"a", "b"}


def test_global_rebinding_flow_sensitive_per_site() -> None:
    cg = graph("""
        def a(): pass
        def b(): pass
        def set_q():
            global q
            q = b
        q = a
        q()
        set_q()
        q()
    """)
    sites = _site_targets(cg, "<module>")
    assert sites[8] == {"a"}
    assert sites[10] == {"b"}
