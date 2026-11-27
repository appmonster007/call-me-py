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
