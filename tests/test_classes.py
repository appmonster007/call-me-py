"""Unit tests for Step 1 class & method support."""
from __future__ import annotations

from callme import analyze_source, to_dict, to_dot
from callme.extract import build_program, call_sites


def edges(src: str) -> set[tuple[str, str]]:
    return set(analyze_source(src, id_style="short").function_edges())


def test_init_and_self_method_calls() -> None:
    src = """
def helper():
    return 1

class Counter:
    def __init__(self):
        self.reset()

    def reset(self):
        return helper()

    def run(self):
        return self.reset()

c = Counter()
c.run()
"""
    assert edges(src) == {
        ("<module>", "Counter.__init__"),
        ("<module>", "Counter.run"),
        ("Counter.__init__", "Counter.reset"),
        ("Counter.reset", "helper"),
        ("Counter.run", "Counter.reset"),
    }


def test_bound_method_value_and_unbound_class_call() -> None:
    src = """
def call(fn):
    return fn()

class A:
    def leaf(self):
        return 1

    def inst(self):
        return self.leaf()

a = A()
m = a.inst
m()
call(a.inst)
A.inst(a)
"""
    assert edges(src) == {
        ("<module>", "A.inst"),
        ("<module>", "call"),
        ("call", "A.inst"),
        ("A.inst", "A.leaf"),
    }


def test_class_body_scope_and_method_alias() -> None:
    src = """
def a():
    return 1

def b():
    return 2

x = a

class K:
    x = b  # class scope: invisible to K.m and module
    y = x  # class body sees class-local x

    def m(self):
        return x()  # resolves to module-level a, not K.x

    alias = m

k = K()
k.m()
k.alias()
K.y()
"""
    assert edges(src) == {
        ("<module>", "K.m"),
        ("<module>", "b"),
        ("K.m", "a"),
    }


def test_class_body_noncallable_shadowing() -> None:
    src = """
def a():
    return 1

x = a

class K:
    before = x
    x = 123
    after = x

K.before()
K.after()
"""
    assert edges(src) == {
        ("<module>", "a"),
    }


def test_local_and_nested_classes() -> None:
    src = """
def helper():
    return 1

def make_cls():
    class Local:
        def hi(self):
            return helper()
    return Local

class Outer:
    class Inner:
        def go(self):
            return helper()

    def build(self):
        return self.Inner()

make_cls()().hi()
Outer().build().go()
Outer.Inner().go()
"""
    assert edges(src) == {
        ("<module>", "make_cls"),
        ("<module>", "make_cls.Local.hi"),
        ("<module>", "Outer.build"),
        ("<module>", "Outer.Inner.go"),
        ("make_cls.Local.hi", "helper"),
        ("Outer.Inner.go", "helper"),
    }


def test_per_receiver_precision() -> None:
    src = """
class Dog:
    def run(self):
        return self.bark()

    def bark(self):
        return "woof"

class Cat:
    def run(self):
        return self.meow()

    def meow(self):
        return "meow"

    def bark(self):
        return "unreachable"

for pet in (Dog(), Cat()):
    pet.run()
"""
    assert edges(src) == {
        ("<module>", "Dog.run"),
        ("<module>", "Cat.run"),
        ("Dog.run", "Dog.bark"),
        ("Cat.run", "Cat.meow"),
    }


def test_class_passed_to_hof_and_except_binding() -> None:
    src = """
class Err(Exception):
    def __init__(self, msg):
        self.log()

    def log(self):
        return 1

    def fmt(self):
        return "err"

class Box:
    def __init__(self, x):
        pass

    def show(self):
        return 0

def handle(e):
    return e.fmt()

boxes = list(map(Box, [1, 2]))
boxes[0].show()

try:
    raise Err("boom")
except Err as e:
    handle(e)
"""
    assert edges(src) == {
        ("<module>", "Box.__init__"),
        ("<module>", "Box.show"),
        ("<module>", "Err.__init__"),
        ("<module>", "handle"),
        ("Err.__init__", "Err.log"),
        ("handle", "Err.fmt"),
    }


def test_extraction_metadata_call_sites_and_exports() -> None:
    src = """
def deco(fn):
    return fn

def init_val():
    return 1

class Outer:
    v = init_val()

    @deco
    def m(self):
        return 0

    class Inner:
        def n(self):
            return 0

o = Outer()
o.m()
Outer.Inner().n()
"""
    prog = build_program(src)
    class_names = {c.qualname for c in prog.classes}
    assert class_names == {"Outer", "Outer.Inner"}

    owners = {f.qualname: (info.owner.qualname if info.owner else None)
              for f, info in prog.funcs.items()}
    assert owners["Outer.m"] == "Outer"
    assert owners["Outer.Inner.n"] == "Outer.Inner"
    assert owners["deco"] is None

    mod_sites = {s.text for s in call_sites(prog, prog.module)}
    assert "init_val()" in mod_sites
    assert "@deco" in mod_sites

    cg = analyze_source(src, id_style="short")
    d = to_dict(cg)
    assert d["edges"]
    dot = to_dot(cg, contexts=True, returns=True)
    assert "Outer()" in dot
    assert "Outer.Inner()" in dot
