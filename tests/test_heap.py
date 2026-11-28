"""Functions that flow through the heap: containers, object fields, global / nonlocal writes.

Each test checks the callees of one call site (found by its source text), not the
function-level edge set: a function-level check passes as soon as *some* call in
the caller reaches the callee, which hides sites that resolve to nothing.
"""
from __future__ import annotations

import textwrap

from callme import analyze_source


def at(src: str, text: str, *, flow_sensitive: bool = True) -> set[str]:
    """Callees of the single call site whose source text is `text`."""
    cg = analyze_source(textwrap.dedent(src), id_style="short", flow_sensitive=flow_sensitive)
    sites = [s for s in cg.sites if s.text == text]
    assert len(sites) == 1, f"{text!r}: {len(sites)} call sites"
    return set(cg.targets(sites[0]))


def test_append_through_alias() -> None:
    src = """
    def f(): pass
    def g(): pass
    a = [g]
    b = a
    b.append(f)
    a[0]()
    """
    assert at(src, "a[0]()") == {"f", "g"}


def test_callee_appends_to_its_argument() -> None:
    src = """
    def f(): pass
    def register(reg, fn):
        reg.append(fn)
    handlers = []
    register(handlers, f)
    for h in handlers:
        h()
    """
    assert at(src, "h()") == {"f"}


def test_registry_decorator_fills_module_dict() -> None:
    src = """
    REG = {}
    def register(name):
        def deco(fn):
            REG[name] = fn
            return fn
        return deco
    @register("a")
    def handle_a(): pass
    @register("b")
    def handle_b(): pass
    def dispatch(name):
        return REG[name]()
    dispatch("a")
    """
    assert at(src, "REG[name]()") == {"handle_a", "handle_b"}


def test_dict_read_methods() -> None:
    src = """
    def f(): pass
    def g(): pass
    vals = {}
    vals["a"] = f
    for fn in vals.values():
        fn()
    items = {"a": f}
    for _, fn2 in items.items():
        fn2()
    pops = {}
    pops["a"] = f
    pops.pop("a")()
    dflt = {}
    dflt.setdefault("a", g)()
    """
    assert at(src, "fn()") == {"f"}
    assert at(src, "fn2()") == {"f"}
    assert at(src, "pops.pop('a')()") == {"f"}
    assert at(src, "dflt.setdefault('a', g)()") == {"g"}


def test_nested_container() -> None:
    src = """
    def f(): pass
    outer = [[]]
    outer[0].append(f)
    outer[0][0]()
    """
    assert at(src, "outer[0][0]()") == {"f"}


def test_list_constructor_and_copy() -> None:
    src = """
    def f(): pass
    xs = list()
    xs.append(f)
    ys = list(xs)
    xs[0]()
    ys[0]()
    """
    assert at(src, "xs[0]()") == {"f"}
    assert at(src, "ys[0]()") == {"f"}


def test_unpacking_and_star_read_stored_elements() -> None:
    src = """
    def f(): pass
    def call(fn):
        return fn()
    xs = []
    xs.append(f)
    a, b = xs
    a()
    call(*xs)
    [*xs][0]()
    """
    assert at(src, "a()") == {"f"}
    assert at(src, "fn()") == {"f"}
    assert at(src, "[*xs][0]()") == {"f"}


def test_augmented_add_mutates_in_place() -> None:
    src = """
    def f(): pass
    xs = []
    alias = xs
    alias += [f]
    xs[0]()
    """
    assert at(src, "xs[0]()") == {"f"}


def test_instances_keep_separate_fields() -> None:
    src = """
    def f(): pass
    def g(): pass
    class C:
        def __init__(self, cb):
            self.cb = cb
    a = C(f)
    b = C(g)
    a.cb()
    b.cb()
    """
    assert at(src, "a.cb()") == {"f"}
    assert at(src, "b.cb()") == {"g"}


def test_observer_list_on_self() -> None:
    src = """
    def add(): pass
    def sub(): pass
    class Bus:
        def __init__(self):
            self.subs = []
        def on(self, cb):
            self.subs.append(cb)
        def emit(self):
            for cb in self.subs:
                cb()
    bus = Bus()
    bus.on(add)
    bus.on(sub)
    bus.emit()
    """
    assert at(src, "cb()") == {"add", "sub"}


def test_global_write_reaches_module_readers() -> None:
    src = """
    def a(): pass
    def b(): pass
    handler = a
    def set_handler():
        global handler
        handler = b
    def run():
        return handler()
    set_handler()
    run()
    """
    assert at(src, "handler()") == {"a", "b"}


def test_global_declared_only_inside_function() -> None:
    src = """
    def f(): pass
    def init():
        global CB
        CB = f
    init()
    CB()
    """
    assert at(src, "CB()") == {"f"}


def test_nonlocal_write_reaches_outer_function() -> None:
    src = """
    def a(): pass
    def b(): pass
    def outer():
        fn = a
        def inner():
            nonlocal fn
            fn = b
        inner()
        return fn()
    outer()
    """
    assert at(src, "fn()") == {"b"}  # strong update on unconditional nonlocal write
    assert at(src, "fn()", flow_sensitive=False) == {"a", "b"}


def test_calling_a_container_is_harmless() -> None:
    src = """
    xs = []
    xs()
    {}["k"]()
    """
    assert at(src, "xs()") == set()
    assert at(src, "{}['k']()") == set()


def test_container_mutations_extend_insert_set_dict_update_and_or() -> None:
    src = """
    def f1(): pass
    def f2(): pass
    def f3(): pass
    def f4(): pass
    def f5(): pass
    def f6(): pass
    def f7(): pass
    def f8(): pass

    src_list = []
    src_list.append(f1)
    dst_list = []
    dst_list.extend(src_list)
    dst_list.insert(0, f2)
    dst_list[0]()

    s = set()
    s.add(f3)
    s.update([f4])
    s |= {f5}
    for fn_s in s:
        fn_s()

    d = {}
    d.update({"a": f6}, b=f7)
    d |= {"c": f8}
    for fn_d in d.values():
        fn_d()

    d2 = {}
    d2.setdefault("k", default=f2)()
    """
    assert at(src, "dst_list[0]()") == {"f1", "f2"}
    assert at(src, "fn_s()") == {"f3", "f4", "f5"}
    assert at(src, "fn_d()") == {"f6", "f7", "f8"}
    assert at(src, "d2.setdefault('k', default=f2)()") == {"f2"}


def test_displays_unpacking_comprehensions_and_chained_assign() -> None:
    src = """
    def f(): pass
    def g(): pass
    def kfn(): return "k"
    def vfn(): return f
    def make(): return g

    s = {f, g}
    sc = {x for x in s}
    for h1 in sc:
        h1()

    d1 = {"x": f}
    d2 = {**d1, kfn(): vfn()}
    d2["x"]()

    multi = [fn() for row in [[f], [g]] for fn in row]
    a = b = make()
    a()
    b()

    xs = []
    xs.append(f)
    xs.append(g)
    head, *tail = xs
    head()
    tail[0]()
    """
    assert at(src, "h1()") == {"f", "g"}
    assert at(src, "d2['x']()") == {"f"}
    assert at(src, "fn()") == {"f", "g"}
    assert at(src, "a()") == {"g"}
    assert at(src, "b()") == {"g"}
    assert at(src, "head()") == {"f", "g"}
    assert at(src, "tail[0]()") == {"f", "g"}
    cg = analyze_source(textwrap.dedent(src), id_style="short")
    assert {("<module>", "kfn"), ("<module>", "vfn")} <= cg.function_edges()


def test_cyclic_box_deref_terminates() -> None:
    src = """
    def f(): pass

    # Self-referential box cycle must terminate in State.deref.
    cyc = []
    cyc.append(cyc)
    cyc.append(f)
    cyc[0][0]()
    """
    assert at(src, "cyc[0][0]()") == {"f"}


def test_boxless_store_fallback_flow_insensitive() -> None:
    src = """
    def f(): pass
    def g(): pass

    # Container without a Box (e.g. parameter given a tuple) falls back to widening the target.
    def push_and_call(c, fn):
        c.append(fn)
        c[0]()
    push_and_call((f,), g)
    """
    assert at(src, "c[0]()", flow_sensitive=False) == {"f", "g"}


def test_boxless_store_fallback_preserves_existing_elements_in_flow_mode() -> None:
    src = """
    def f(): pass
    def g(): pass

    def push_and_call(c, fn):
        c.append(fn)
        c[0]()
    push_and_call((f,), g)

    # Nested subscript fallback preserves existing elements too.
    def push_nested(m, fn):
        m[0].append(fn)
        m[0][0]()
    push_nested(((f,),), g)
    """
    assert at(src, "c[0]()", flow_sensitive=True) == {"f", "g"}
    assert at(src, "m[0][0]()", flow_sensitive=True) == {"f", "g"}


def test_yield_yield_from_and_custom_await() -> None:
    src = """
    def f(): pass
    def g(): pass
    def h(): pass

    def gen():
        xs = []
        xs.append(f)
        yield from xs
        yield g

    for fn in gen():
        fn()

    class Awaitable:
        def __await__(self):
            return h

    async def runner():
        got = await Awaitable()
        got()
    runner()
    """
    assert at(src, "fn()") == {"f", "g"}
    assert at(src, "got()") == {"h"}
    cg = analyze_source(textwrap.dedent(src), id_style="short")
    assert ("runner", "Awaitable.__await__") in cg.function_edges()
