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


def test_decorator_factory_direct_and_indirect_no_spurious_edge() -> None:
    cg = graph("""
        def make_deco(n):
            def deco(fn):
                def wrapper():
                    return fn()
                return wrapper
            return deco

        class Holder:
            def get_deco(self):
                return make_deco(1)

        @make_deco(2)
        def direct():
            return 1

        d = make_deco(3)
        @d
        def indirect():
            return 2

        h = Holder()
        @h.get_deco()
        def via_attr():
            return 3

        direct()
        indirect()
        via_attr()
    """)
    edges = cg.function_edges()
    assert ("<module>", "direct") not in edges
    assert ("<module>", "indirect") not in edges
    assert ("<module>", "via_attr") not in edges
    assert {("make_deco.deco.wrapper", "direct"),
            ("make_deco.deco.wrapper", "indirect"),
            ("make_deco.deco.wrapper", "via_attr")} <= edges


def test_unknown_decorator_falls_back_to_identity_after_convergence() -> None:
    cg = graph("""
        from thirdparty import ext_deco, ext_factory

        def make_deco():
            def deco(fn):
                def wrapper():
                    return fn()
                return wrapper
            return deco

        @ext_deco
        def plain():
            return 1

        @ext_factory(1)
        def factory_wrapped():
            return 2

        @ext_deco
        @make_deco()
        def stacked():
            return 3

        @ext_deco
        class C:
            def m(self):
                return 4

        plain()
        factory_wrapped()
        stacked()
        C().m()
    """)
    edges = cg.function_edges()
    assert {("<module>", "plain"), ("<module>", "factory_wrapped"),
            ("<module>", "make_deco.deco.wrapper"), ("make_deco.deco.wrapper", "stacked"),
            ("<module>", "C.m")} <= edges
    assert ("<module>", "stacked") not in edges


def test_unknown_decorator_is_context_sensitive_and_persists() -> None:
    import json
    from callme.analysis import init_state, solve, step
    from callme.frontend import build_program
    from callme.output import state_from_dict, state_to_dict

    src = textwrap.dedent("""\
        from thirdparty import ext_deco

        def wrap(fn):
            def inner():
                return fn()
            return inner

        def make(deco):
            @deco
            def target():
                return 1
            return target

        f1 = make(wrap)
        f2 = make(ext_deco)
        f1()
        f2()
    """)
    cg = analyze_source(src, id_style="short")
    sites = _site_targets(cg, "<module>")
    assert sites[16] == {"wrap.inner"}
    assert sites[17] == {"make.target"}

    prog = build_program(src, "t.py")
    s_solve = solve(prog)
    s_manual = init_state(prog)
    for _ in range(100):
        if not step(prog, s_manual).changed:
            break
    assert s_manual.same_result(s_solve)

    back = state_from_dict(prog, json.loads(json.dumps(state_to_dict(s_solve))))
    assert back.same_result(s_solve)
    assert solve(prog, back).same_result(s_solve)

    chained = solve(prog, s_solve, include_unreachable=True)
    direct = solve(prog, include_unreachable=True)
    assert chained.same_result(direct)


def test_model_ids_module_name_and_token_labels() -> None:
    import pytest
    from callme.model import (Alloc, BoundMethod, Box, Builtin, ClassId, ClassMethod, ClassObj,
                              Closure, FuncId, Instance, Module, Property, StaticMethod, Super,
                              module_name, token_label)

    assert module_name("__init__.py") == "__init__.py"
    assert module_name("./pkg/util.py") == "pkg.util"
    assert module_name("../a/b/__init__.py") == "a.b"
    assert module_name("<string>") == "<string>"

    top = FuncId("f", "m.py", 1, 0)
    assert (top.name, top.scope) == ("f", "")
    with pytest.raises(ValueError, match="unknown id style"):
        top.render("bogus")

    cid = ClassId("C", "m.py", 2, 0)
    alloc = Alloc(top, 5, 4)
    clo = Closure(top)
    inst = Instance(cid, alloc)
    assert token_label(clo, "short") == "f"
    assert token_label(Builtin("map"), "short") == "map"
    assert token_label(Builtin("<partial>", (frozenset([clo]),)), "short") == "<partial:f>"
    assert token_label(Builtin("<partial*>", (frozenset([clo]),)), "short") == "<partial*:f>"
    assert token_label(ClassObj(cid), "short") == "<class C>"
    assert token_label(inst, "short") == "C()@5:4"
    assert token_label(Instance(cid, None), "short") == "C()"
    assert token_label(Box("list", alloc), "short") == "<list@5:4>"
    assert token_label(Module("pkg.util"), "short") == "<module pkg.util>"
    assert token_label(BoundMethod(clo, inst), "short") == "f@C()@5:4"
    assert token_label(ClassMethod(clo), "short") == "classmethod(f)"
    assert token_label(StaticMethod(clo), "short") == "staticmethod(f)"
    assert token_label(Super(cid, inst), "short") == "super(C, C()@5:4)"
    assert token_label(Property(frozenset([clo])), "short") == "property(f)"


def test_context_label_disambiguation_and_site_truncation() -> None:
    cg = graph("""
        def inc(): pass
        def dec(): pass
        def make(f):
            def h(): return f()
            return h
        make(inc)()
        make(dec)()
        def long_call(aaaaaaaaaa, bbbbbbbbbb, cccccccccc, dddddddddd, eeeeeeeeee):
            return 1
        long_call(1111111111, 2222222222, 3333333333, 4444444444, 5555555555)
    """)
    h_labels = sorted(n for n in labels(cg) if n.startswith("make.h"))
    assert h_labels == ["make.h#1", "make.h#2"]
    long_sites = [s for s in cg.sites if s.text.startswith("long_call(")]
    assert len(long_sites) == 1
    assert len(long_sites[0].text) == 60 and long_sites[0].text.endswith("...")


def test_max_contexts_zero_widens_immediately_and_exports_star() -> None:
    cg = graph(APPLY, max_contexts=0)
    assert "apply[*]" in labels(cg)
    assert {("apply", "square"), ("apply", "double")} <= cg.function_edges()
    doc = to_dict(cg)
    ctxs = cast("list[dict[str, object]]", doc["contexts"])
    widened = next(c for c in ctxs if c["id"] == "apply[*]")
    assert widened["context"] == {"*": []}
    assert '"apply[*]" -> "square[*]"' in to_dot(cg, contexts=True)


def test_closure_depth_widening_terminates_and_default_without_parent() -> None:
    from callme.analysis import mk_closure
    from callme.model import FuncId

    assert mk_closure(FuncId("f", "t.py", 1, 0), None).parent is None

    # Recursive closure creation exceeds MAX_DEPTH and widens parent to None while staying sound.
    cg = graph("""
        def target(): return 1
        def nest(n, f):
            def wrap(cb=f):
                return cb()
            if n > 0:
                return nest(n - 1, wrap)
            return wrap
        nest(5, target)()
    """)
    assert ("nest.wrap", "target") in cg.function_edges()
    assert any(n.func.qualname == "nest.wrap" and n.ctx.parent is None for n in cg.nodes)


def test_bind_args_signature_and_default_edge_cases() -> None:
    cg = graph("""
        def leaf1(): pass
        def leaf2(): pass
        def leaf3(): pass
        def leaf4(): pass

        def one(a): return a()
        def kw_only(*, cb): return cb()
        def kw_default(*, cb=leaf1): return cb()
        def needs_two(a, b):
            a()
            b()
        def multi_var(*args, **kwargs):
            for f in args:
                f()
            for g in kwargs.values():
                g()

        def try_bad(f):
            f(leaf1, leaf2)          # too many positional for `one`
            f(leaf1, a=leaf2)        # duplicate positional + keyword for `one`
            f(leaf1, bogus=leaf2)    # unknown keyword for `one`

        try_bad(one)
        kw_only(leaf1)               # rejected: positional given to kw-only
        kw_only()                    # rejected: missing required kw-only
        kw_only(cb=leaf2)            # accepted
        kw_default()                 # non-constant kw-only default
        needs_two(*(leaf3, leaf4))   # *args satisfies required params
        needs_two(**{"a": leaf3, "b": leaf4})  # **kwargs satisfies required params
        multi_var(*(leaf1,), *(leaf2,), **{"x": leaf3}, **{"y": leaf4})
        lam = lambda x, f=leaf1, *, g=leaf2: (f(), g())
        lam(0)
    """)
    edges = cg.function_edges()
    assert ("try_bad", "one") not in edges
    assert ("kw_only", "leaf1") not in edges
    assert ("kw_only", "leaf2") in edges
    assert ("kw_default", "leaf1") in edges
    assert {("needs_two", "leaf3"), ("needs_two", "leaf4")} <= edges
    assert {("multi_var", "leaf1"), ("multi_var", "leaf2"),
            ("multi_var", "leaf3"), ("multi_var", "leaf4")} <= edges
    assert any(c.startswith("<lambda>@") and d == "leaf1" for c, d in edges)
    assert any(c.startswith("<lambda>@") and d == "leaf2" for c, d in edges)


def test_decorator_edge_cases() -> None:
    cg = graph("""
        from thirdparty import ext_deco

        def swallow(fn):
            return None

        def deep_factory():
            return make_deco()

        def make_deco():
            def deco(fn):
                def wrapper():
                    return fn()
                return wrapper
            return deco

        def replace_cls(cls):
            class Proxy:
                def run(self):
                    return cls.orig()
            return Proxy

        @swallow
        def swallowed():
            return 1

        @deep_factory()
        def delayed():
            return 2

        @ext_deco
        async def async_fn():
            return 3

        class Box:
            @ext_deco
            def method(self):
                return 4

        @replace_cls
        class Real:
            @staticmethod
            def orig():
                return 5

        swallowed()
        delayed()
        async_fn()
        Box().method()
        Real().run()
    """)
    edges = cg.function_edges()
    assert ("<module>", "swallowed") not in edges
    assert ("<module>", "delayed") not in edges
    assert {("<module>", "swallow"), ("make_deco.deco.wrapper", "delayed"),
            ("<module>", "async_fn"), ("<module>", "Box.method"),
            ("<module>", "replace_cls"), ("<module>", "replace_cls.Proxy.run"),
            ("replace_cls.Proxy.run", "Real.orig")} <= edges


def test_cli_formats_print_graph_and_dot_escaping(tmp_path, capsys) -> None:
    from callme.__main__ import main as cli

    prog_file = tmp_path / "prog.py"
    prog_file.write_text(textwrap.dedent("""\
        def leaf(s):
            return s
        def run():
            return leaf('a"b\\\\c')
        run()
    """), encoding="utf-8")

    # No -o: runs cleanly and writes nothing.
    assert cli([str(prog_file)]) == 0
    assert capsys.readouterr().out == ""

    # --print-graph writes lexical nesting and call sites to stderr.
    assert cli([str(prog_file), "--short-ids", "--print-graph"]) == 0
    err = capsys.readouterr().err
    assert "# function identifier graph" in err and "<module> -> run" in err
    assert "# call sites" in err and "run -> leaf(" in err

    # --format dot (--contexts, --returns), --format icfg, --format json --icfg, --flow-insensitive.
    dot_out = tmp_path / "g.dot"
    icfg_out = tmp_path / "icfg.dot"
    json_out = tmp_path / "g.json"
    assert cli([str(prog_file), "--short-ids", "--format", "dot", "--contexts", "--returns",
                "--max-contexts", "2", "--flow-insensitive", "-o", str(dot_out)]) == 0
    dot_text = dot_out.read_text(encoding="utf-8")
    assert dot_text.startswith("digraph callgraph") and "style=dotted" in dot_text

    assert cli([str(prog_file), "--short-ids", "--format", "icfg", "-o", str(icfg_out)]) == 0
    icfg_text = icfg_out.read_text(encoding="utf-8")
    assert icfg_text.startswith("digraph icfg") and '\\"' in icfg_text and "\\\\" in icfg_text

    assert cli([str(prog_file), "--short-ids", "--icfg", "-o", str(json_out)]) == 0
    doc = json.loads(json_out.read_text(encoding="utf-8"))
    assert "icfg" in doc and set(doc["icfg"]["cfgs"]) == {"<module>", "run", "leaf"}


def test_method_stub_fallback_depth_bound_terminates() -> None:
    """Recursive reads of container-method names (e.g. `.values`, `.keys`) stay finite."""
    cg = graph("""
        def leaf(): return 1
        def walk(e):
            walk(e.values)
            walk(e.keys)
            for v in e.values:
                walk(v)
        walk([leaf])
    """)
    assert ("walk", "walk") in cg.function_edges()


def test_self_analysis_of_cli_converges() -> None:
    from pathlib import Path
    from callme import analyze_file

    cli_path = Path(__file__).resolve().parent.parent / "callme" / "__main__.py"
    cg = analyze_file(cli_path, max_rounds=100)
    assert len(cg.nodes) > 0 and len(cg.edges) > 0
