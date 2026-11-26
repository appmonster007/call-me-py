"""Unit tests: entry-state contexts, return values, termination, exports."""
import json
import textwrap

from callme import analyze_source, to_dict, to_dot, to_json


def graph(src, **kw):
    kw.setdefault("id_style", "short")
    return analyze_source(textwrap.dedent(src), "t.py", **kw)


def labels(cg):
    return {cg.labels[n] for n in cg.nodes}


APPLY = """
    def square(x): return x
    def double(x): return x
    def apply(f, v): return f(v)
    apply(square, 1)
    apply(double, 2)
"""


def test_functional_contexts_split_by_entry_state():
    cg = graph(APPLY)
    assert {"apply[f={square}]", "apply[f={double}]"} <= labels(cg)
    # each context calls only its own callee (a call never returns to a different call's context)
    ctx = {(cg.labels[e.caller], cg.labels[e.callee]) for e in cg.edges}
    assert ("apply[f={square}]", "square") in ctx
    assert ("apply[f={double}]", "double") in ctx
    assert ("apply[f={square}]", "double") not in ctx
    assert ("apply[f={double}]", "square") not in ctx


def test_same_entry_state_shares_one_context():
    cg = graph("""
        def sq(x): return x
        def apply(f, v): return f(v)
        apply(sq, 1)
        apply(sq, 2)
        apply(sq, 3)
    """)
    assert [n for n in labels(cg) if n.startswith("apply")] == ["apply[f={sq}]"]


def test_return_value_flows_back_per_context():
    cg = graph("""
        def a(): pass
        def b(): pass
        def ident(f): return f
        x = ident(a)
        y = ident(b)
        x()
        y()
    """)
    by_line = {}
    for e in cg.edges:
        if e.caller.func.qualname == "<module>":
            by_line.setdefault(e.site.line, set()).add(e.callee.func.qualname)
    assert by_line[7] == {"a"} and by_line[8] == {"b"}  # not {a, b}


def test_closure_captures_defining_context():
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


def test_recursion_terminates_and_has_cycle():
    cg = graph("""
        def even(n): return odd(n)
        def odd(n): return even(n)
        even(1)
    """)
    assert {("even", "odd"), ("odd", "even")} <= cg.function_edges()


def test_computed_call_reaches_all_possible_targets():
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


def test_arity_filters_callees():
    cg = graph("""
        def one(a): pass
        def two(a, b): pass
        def run(f): f(1)
        run(one)
        run(two)
    """)
    assert ("run", "one") in cg.function_edges()
    assert ("run", "two") not in cg.function_edges()


def test_unresolved_calls_produce_no_edges():
    cg = graph("print(len([1]))")
    assert cg.function_edges() == frozenset()


def test_context_cap_widens_but_stays_sound():
    fns = "\n".join(f"def f{i}(x): return x" for i in range(6))
    calls = "\n".join(f"apply(f{i}, 1)" for i in range(6))
    cg = graph(fns + "\ndef apply(f, v): return f(v)\n" + calls, max_contexts=3)
    assert {("apply", f"f{i}") for i in range(6)} <= cg.function_edges()


def test_exports():
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


def test_unreachable_functions_marked():
    cg = graph("""
        def used(): pass
        def unused(): used()
        used()
    """)
    reach = {f["id"]: f["reachable"] for f in to_dict(cg)["functions"]}
    assert reach["used"] and not reach["unused"]
    cg2 = graph("""
        def used(): pass
        def unused(): used()
        used()
    """, include_unreachable=True)
    assert ("unused", "used") in cg2.function_edges()


def test_default_ids_are_file_line_col_qualname():
    cg = analyze_source("def f(x): return x\ndef f(x): return x+1\ndef g(): return f(1)\ng()\n", "m.py")
    # flow-insensitive: g's call f(1) may reach either definition of f
    assert cg.function_edges() == {
        ("m.py:1:0:<module>", "m.py:3:0:g"),
        ("m.py:3:0:g", "m.py:1:0:f"), ("m.py:3:0:g", "m.py:2:0:f"),
    }
    ids = [f["id"] for f in to_dict(cg)["functions"]]
    assert len(ids) == len(set(ids)) == 4


def test_funcid_parts_and_rendering():
    from callme.model import FuncId
    f = FuncId("Outer.make.inner", "pkg/util.py", 7, 4)
    assert (f.name, f.scope, f.module) == ("inner", "Outer.make", "pkg.util")
    assert f.render("full") == "pkg/util.py:7:4:Outer.make.inner" == str(f)
    assert f.render("module") == "pkg.util.Outer.make.inner"
    assert f.render("short") == "Outer.make.inner"
    assert FuncId("g", "pkg/__init__.py", 1, 0).module == "pkg"


def test_id_style_module():
    cg = analyze_source("def f(): pass\nf()\n", "pkg/m.py", id_style="module")
    assert ("pkg.m.<module>", "pkg.m.f") in cg.function_edges()
