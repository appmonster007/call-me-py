"""Flow sensitivity and side-effect write (`scope_must_writes` / `State.writes`) edge cases."""
from __future__ import annotations

import ast
import textwrap

from callme import CallGraph, analyze_source
from callme.frontend.scopes import scope_must_writes


def _site_targets(cg: CallGraph, caller: str) -> dict[int, set[str]]:
    """Map 1-based line number to callee names for every edge leaving `caller`."""
    out: dict[int, set[str]] = {}
    for e in cg.edges:
        if e.caller.func.qualname == caller:
            out.setdefault(e.site.line, set()).add(e.callee.func.qualname)
    return out


def _at(src: str, text: str, *, flow_sensitive: bool = True,
        max_contexts: int | None = None) -> set[str]:
    """Return callee names at the unique call site whose source text equals `text`."""
    cg = analyze_source(textwrap.dedent(src), "t.py", "short",
                        flow_sensitive=flow_sensitive, max_contexts=max_contexts)
    sites = [s for s in cg.sites if s.text == text]
    assert len(sites) == 1, f"{text!r}: expected 1 site, found {len(sites)}"
    return set(cg.targets(sites[0]))


def _must(fn_src: str, tracked: set[str]) -> frozenset[str]:
    """Compute `scope_must_writes` on the single function defined in `fn_src`."""
    node = ast.parse(textwrap.dedent(fn_src)).body[0]
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    return scope_must_writes(node, node.body, frozenset(tracked))


# ------------------------------------------------------------ local flow sensitivity
def test_del_local_kills_binding_in_flow_mode() -> None:
    src = """
        def a(): pass
        def b(): pass
        def run(flag):
            f = a
            g = a
            del f, g
            f()
            f = b
            f()
            h = a
            if flag:
                del h
            h()
        run(True)
    """
    cg = analyze_source(textwrap.dedent(src), "t.py", "short")
    sites = _site_targets(cg, "run")
    assert sites.get(8, set()) == set()  # deleted: no callee
    assert sites[10] == {"b"}            # rebound after del
    assert sites[14] == {"a"}            # deleted on only one branch

    cg_fi = analyze_source(textwrap.dedent(src), "t.py", "short", flow_sensitive=False)
    assert _site_targets(cg_fi, "run")[8] == {"a", "b"}


def test_walrus_operator_is_weak_and_escapes_comprehension() -> None:
    src = """
        def a(): pass
        def b(): pass
        def c(): pass
        def run(flag):
            f = a
            if flag and (f := b):
                pass
            f()
            _ = [g := x for x in [c]]
            g()
        run(True)
    """
    assert _at(src, "f()") == {"a", "b"}
    assert _at(src, "g()") == {"c"}


def test_annotated_assignment_without_and_with_value() -> None:
    src = """
        def a(): pass
        def b(): pass
        def run():
            f = a
            f: object
            f()
            g = a
            g: object = b
            g()
        run()
    """
    assert _at(src, "f()") == {"a"}
    assert _at(src, "g()") == {"b"}


def test_dead_code_after_jump_is_analyzed_without_leaking_state() -> None:
    src = """
        def a(): pass
        def b(): pass
        def dead_call(): pass
        def run(flag):
            f = a
            if flag:
                return
                f = b
                dead_call()
            f()
        def both_jump(flag):
            if flag:
                return
            else:
                raise RuntimeError()
            dead_call()
        def raise_branch(flag):
            g = a
            if flag:
                g = b
                raise RuntimeError()
            g()
        run(False)
        both_jump(True)
        raise_branch(False)
    """
    cg = analyze_source(textwrap.dedent(src), "t.py", "short")
    assert _at(src, "f()") == {"a"}
    assert _at(src, "g()") == {"a"}
    assert {("run", "dead_call"), ("both_jump", "dead_call")} <= cg.function_edges()


def test_for_and_while_continue_break_and_else() -> None:
    src = """
        def a(): pass
        def b(): pass
        def c(): pass
        def for_else_break(xs):
            f = a
            for x in xs:
                if x:
                    f = b
                    break
            else:
                f = c
            f()
        def while_continue(flag):
            h = a
            while flag:
                h()
                if flag:
                    h = b
                    continue
                h = c
            else:
                h()
        for_else_break([True])
        while_continue(True)
    """
    assert _at(src, "f()") == {"b", "c"}  # `a` killed on both break and else paths
    cg = analyze_source(textwrap.dedent(src), "t.py", "short")
    sites = _site_targets(cg, "while_continue")
    assert sites[17] == {"a", "b", "c"}   # continue feeds `b` back to loop head
    assert sites[23] == {"a", "b", "c"}   # else runs from converged head


def test_try_else_and_finally_with_loop_jumps() -> None:
    src = """
        def a(): pass
        def b(): pass
        def c(): pass
        def else_dead_after_return():
            f = a
            try:
                return
            except ValueError:
                f = b
            else:
                f = c
                c()
            f()
        def else_runs_on_normal_try():
            g = a
            try:
                g = a
            except ValueError:
                g = b
            else:
                g = c
            g()
        def finally_runs_on_break_and_continue(xs):
            h = a
            for x in xs:
                h()
                try:
                    if x:
                        break
                    if x:
                        continue
                finally:
                    h = c
            k = h
            k()
        else_dead_after_return()
        else_runs_on_normal_try()
        finally_runs_on_break_and_continue([True])
    """
    cg = analyze_source(textwrap.dedent(src), "t.py", "short")
    assert _at(src, "f()") == {"b"}
    assert ("else_dead_after_return", "c") in cg.function_edges()
    assert _at(src, "g()") == {"b", "c"}
    assert _at(src, "h()") == {"a", "c"}
    assert _at(src, "k()") == {"a", "c"}


def test_try_star_exception_group() -> None:
    src = """
        def a(): pass
        def b(): pass
        class MyErr(Exception):
            def handle(self):
                return b()
        def run():
            f = a
            try:
                f = b
                raise MyErr()
            except* MyErr as eg:
                f()
                eg.handle()
        run()
    """
    cg = analyze_source(textwrap.dedent(src), "t.py", "short")
    assert _at(src, "f()") == {"a", "b"}
    assert {("run", "MyErr.handle"), ("MyErr.handle", "b")} <= cg.function_edges()


def test_with_exception_suppression_and_multiple_items() -> None:
    src = """
        def a(): pass
        def b(): pass
        def on_enter(): return a
        def on_exit(): pass
        class CM:
            def __enter__(self):
                return on_enter()
            def __exit__(self, *exc):
                on_exit()
                return True
        def suppress_test():
            f = a
            with CM() as x, b as y:
                x()
                y()
                f = b
                return
            f()
        suppress_test()
    """
    cg = analyze_source(textwrap.dedent(src), "t.py", "short")
    assert _at(src, "x()") == {"a"}        # __enter__ return value
    assert _at(src, "y()") == {"b"}        # no __enter__: bound to expr value itself
    assert _at(src, "f()") == {"a", "b"}   # __exit__ may suppress exception before return / f=b
    assert {("suppress_test", "CM.__enter__"), ("suppress_test", "CM.__exit__"),
            ("CM.__enter__", "on_enter"), ("CM.__exit__", "on_exit")} <= cg.function_edges()


def test_match_patterns_guards_and_fallthrough() -> None:
    src = """
        def a(): pass
        def b(): pass
        def c(): pass
        def guard_fn(): return True
        class Holder:
            TAG = a
        def run(subject):
            f = a
            match subject:
                case Holder.TAG if guard_fn():
                    f = b
                case {"k": g, **rest}:
                    g()
                    rest()
                case [head, *tail]:
                    head()
                    tail()
                case x | y:
                    x()
            f()
        run({"k": c})
    """
    cg = analyze_source(textwrap.dedent(src), "t.py", "short")
    assert _at(src, "f()") == {"a", "b"}
    assert _at(src, "g()") == {"c"}
    assert _at(src, "rest()") == {"c"}
    assert _at(src, "head()") == {"c"}
    assert _at(src, "tail()") == {"c"}
    assert _at(src, "x()") == {"c"}
    assert ("run", "guard_fn") in cg.function_edges()


def test_match_sequence_destructuring_reads_mutated_box() -> None:
    src = """
        def f(): pass
        def g(): pass
        xs = []
        xs.append(f)
        match xs:
            case [h]:
                h()
        d = {}
        d["k"] = g
        match d:
            case {"k": h2}:
                h2()
    """
    assert _at(src, "h()") == {"f"}
    assert _at(src, "h2()") == {"g"}


# ------------------------------------------------------------ scope_must_writes
def test_scope_must_writes_branches_loops_try_with_match_and_deletes() -> None:
    # Async functions and generators never must-write (calling them does not run body).
    assert _must("async def f():\n    global x\n    x = 1\n", {"x"}) == frozenset()
    assert _must("def f():\n    global x\n    x = 1\n    yield 1\n", {"x"}) == frozenset()
    assert _must("def f():\n    global x\n    x = 1\n    yield from ()\n", {"x"}) == frozenset()

    # Assignment target forms: unpacking, starred, AnnAssign, def, class, import.
    assert _must("""
        def f():
            global a, b, c, d, e, g, h
            a, [b, *c] = src
            d: int = 1
            e: int
            def g(): pass
            class h: pass
    """, {"a", "b", "c", "d", "e", "g", "h"}) == frozenset({"a", "b", "c", "d", "g", "h"})
    assert _must("def f():\n    global m, n\n    import m\n    from p import q as n\n",
                 {"m", "n"}) == frozenset({"m", "n"})

    # If/else: raise eliminates a branch; both dead -> empty.
    assert _must("""
        def f(c):
            global x
            if c:
                x = 1
            else:
                raise ValueError()
    """, {"x"}) == frozenset({"x"})
    assert _must("""
        def f(c):
            global x
            if c:
                raise ValueError()
            else:
                raise KeyError()
    """, {"x"}) == frozenset()

    # Loops: break + else both write -> must-write; loop without else -> not guaranteed;
    # delete in loop body cancels a pre-loop write.
    assert _must("""
        def f(xs):
            global x, y
            y = 1
            for i in xs:
                if i:
                    x = 1
                    break
                if i == 2:
                    continue
                del y
            else:
                x = 2
    """, {"x", "y"}) == frozenset({"x"})
    assert _must("""
        def f(xs):
            global x
            for _ in xs:
                x = 1
    """, {"x"}) == frozenset()

    # Try / except / else / finally + del in finally canceling an earlier return's write.
    assert _must("""
        def f():
            global x, y
            try:
                x = 1
                y = 1
            except ValueError:
                x = 2
                y = 2
            else:
                x = 3
            finally:
                del y
    """, {"x", "y"}) == frozenset({"x"})
    assert _must("""
        def f():
            global x, y
            try:
                x = 1
                y = 1
                return
            except ValueError:
                return
            finally:
                del y
    """, {"x", "y"}) == frozenset()  # except branch didn't write x, and finally deleted y
    assert _must("""
        def f():
            global x
            try:
                x = 1
                return
            finally:
                del x
    """, {"x"}) == frozenset()
    assert _must("""
        def f():
            global x
            try:
                raise ValueError()
            except ValueError:
                pass
            finally:
                x = 1
    """, {"x"}) == frozenset({"x"})
    # Try/except without finally (both paths write, and both paths jump), and finally returning.
    assert _must("""
        def f():
            global x
            try:
                x = 1
            except ValueError:
                x = 2
    """, {"x"}) == frozenset({"x"})
    assert _must("""
        def f():
            global x
            try:
                x = 1
                return
            except ValueError:
                raise
    """, {"x"}) == frozenset({"x"})
    assert _must("""
        def f():
            global x
            try:
                pass
            finally:
                x = 1
                return
    """, {"x"}) == frozenset({"x"})
    # Loop with returning else and exhaustive match where all cases return.
    assert _must("""
        def f(xs):
            global x
            for _ in xs:
                pass
            else:
                x = 1
                return
    """, {"x"}) == frozenset({"x"})
    assert _must("""
        def f(v):
            global x
            match v:
                case 1:
                    x = 1
                    return
                case _:
                    x = 2
                    return
    """, {"x"}) == frozenset({"x"})

    # With: conservative (body write is not guaranteed, body del cancels pre-with write).
    assert _must("""
        def f(cm):
            global x, y
            y = 1
            with cm:
                x = 1
                del y
    """, {"x", "y"}) == frozenset()

    # Match: exhaustive irrefutable pattern vs guarded / non-exhaustive.
    assert _must("""
        def f(v):
            global x
            match v:
                case 1:
                    x = 1
                case 2 | _:
                    x = 2
    """, {"x"}) == frozenset({"x"})
    assert _must("""
        def f(v):
            global x
            match v:
                case _ if v:
                    x = 1
    """, {"x"}) == frozenset()

    # Delete cancels earlier write, later write restores it.
    assert _must("""
        def f():
            global x, y
            x = 1
            y = 1
            del x, y
            y = 2
    """, {"x", "y"}) == frozenset({"y"})


def test_must_kill_end_to_end_edge_cases() -> None:
    # 1. Async / generator / polymorphic / assert-msg / chained-cmp must NOT strong-kill.
    src = """
        def a(): pass
        def b(): pass
        def outer(flag):
            f = a
            async def async_set():
                nonlocal f
                f = b
            def gen_set():
                nonlocal f
                f = b
                yield 1
            def real_set():
                nonlocal f
                f = b
            def noop():
                pass
            async_set()
            f()
            f = a
            gen_set()
            f()
            f = a
            (real_set if flag else noop)()
            f()
            f = a
            assert flag, real_set()
            f()
            f = a
            _ = 1 < 2 < real_set()
            f()
            f = a
            _ = 1 < real_set() < 2
            f()
        outer(True)
    """
    cg = analyze_source(textwrap.dedent(src), "t.py", "short")
    sites = _site_targets(cg, "outer")
    for line in (19, 22, 25, 28, 31):
        assert sites[line] == {"a", "b"}, f"line {line}: {sites.get(line)}"
    # First comparator of a chained comparison is unconditionally evaluated -> strong kill.
    assert sites[34] == {"b"}

    # 2. Direct nonlocal write of non-callable kills binding; multi-level nonlocal reaches outer.
    src2 = """
        def a(): pass
        def b(): pass
        def outer():
            f = a
            def clear():
                nonlocal f
                f = 123
            clear()
            f()
            g = a
            def mid():
                def set_b():
                    nonlocal g
                    g = b
                return set_b
            mid()()
            g()
        outer()
    """
    cg2 = analyze_source(textwrap.dedent(src2), "t.py", "short")
    sites2 = _site_targets(cg2, "outer")
    assert sites2.get(10, set()) == set()
    assert sites2[18] == {"a", "b"}  # round 1 sees `a` before mid() returns; round 2 adds `b`

    # 3. Widened caller (`max_contexts=0`) skips strong kills.
    src3 = """
        def a(): pass
        def b(): pass
        def outer(tag):
            f = a
            def set_b():
                nonlocal f
                f = b
            set_b()
            f()
        outer(a)
    """
    assert _at(src3, "f()", max_contexts=0) == {"a", "b"}
