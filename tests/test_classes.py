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


def test_classmethod_and_staticmethod_variants() -> None:
    src = """
def util(x):
    return x

class A:
    handler = staticmethod(util)

    def __init__(self):
        self.init_helper()

    def init_helper(self):
        return 1

    @classmethod
    def make(cls):
        return cls()

    @classmethod
    def via_cls(cls):
        return cls.make()

    @staticmethod
    def stat(x):
        return util(x)

    def run(self):
        self.via_cls()
        return self.stat(1)

a = A()
a.run()
cm = a.via_cls
cm()
a.handler(2)
A.handler(3)
staticmethod(util)(4)
"""
    assert edges(src) == {
        ("<module>", "A.__init__"),
        ("<module>", "A.run"),
        ("<module>", "A.via_cls"),
        ("<module>", "util"),
        ("A.__init__", "A.init_helper"),
        ("A.run", "A.via_cls"),
        ("A.run", "A.stat"),
        ("A.via_cls", "A.make"),
        ("A.make", "A.__init__"),
        ("A.stat", "util"),
    }


def test_decorated_instance_and_class_methods() -> None:
    src = """
import functools

def trace(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return fn(*a, **k)
    return wrapper

class K:
    def leaf(self):
        return 1

    @trace
    def m(self):
        return self.leaf()

    @classmethod
    @trace
    def build(cls):
        return cls()

K().m()
K.build().leaf()
"""
    assert edges(src) == {
        ("<module>", "trace"),
        ("<module>", "trace.wrapper"),
        ("<module>", "K.leaf"),
        ("trace.wrapper", "K.m"),
        ("trace.wrapper", "K.build"),
        ("K.m", "K.leaf"),
    }


def test_implicit_new_and_init_subclass_wrapping() -> None:
    src = """
def leaf():
    return 1

class C:
    def __new__(cls):
        return cls.make()

    def __init_subclass__(cls):
        return cls.hook()

    @classmethod
    def make(cls):
        return leaf()

    @classmethod
    def hook(cls):
        return leaf()

c = C()
C.__new__(C)
c.__new__(C)
C.__init_subclass__()
c.__init_subclass__()
"""
    assert edges(src) == {
        ("<module>", "C.__new__"),
        ("<module>", "C.__init_subclass__"),
        ("C.__new__", "C.make"),
        ("C.__init_subclass__", "C.hook"),
        ("C.make", "leaf"),
        ("C.hook", "leaf"),
    }


def test_inheritance_override_and_two_arg_super() -> None:
    src = """
class Base:
    def run(self):
        return self.step()

    def step(self):
        return 1

    @classmethod
    def build(cls):
        return cls()

class Left(Base):
    def step(self):
        return super().step()

class Right(Base):
    def step(self):
        return super().step()

class Diamond(Left, Right):
    def step(self):
        return super(Diamond, self).step()

    @classmethod
    def build(cls):
        return super().build()

Diamond.build().run()
"""
    assert edges(src) == {
        ("<module>", "Diamond.build"),
        ("<module>", "Base.run"),
        ("Diamond.build", "Base.build"),
        ("Base.run", "Diamond.step"),
        ("Diamond.step", "Left.step"),
        ("Left.step", "Right.step"),
        ("Right.step", "Base.step"),
    }


def test_new_returning_other_class_and_object_new() -> None:
    src = """
class Target:
    def __init__(self):
        self.work()

    def work(self):
        return 1

class Proxy:
    def __new__(cls):
        return Target()

    def __init__(self):
        self.never()

    def never(self):
        return 0

class Explicit:
    def __new__(cls):
        return object.__new__(cls)

    def __init__(self):
        self.ok()

    def ok(self):
        return 2

Proxy().work()
Explicit()
"""
    assert edges(src) == {
        ("<module>", "Proxy.__new__"),
        ("<module>", "Target.work"),
        ("Proxy.__new__", "Target.__init__"),
        ("Target.__init__", "Target.work"),
        ("<module>", "Explicit.__new__"),
        ("<module>", "Explicit.__init__"),
        ("Explicit.__init__", "Explicit.ok"),
    }


def test_dataclass_plain_parameterized_and_custom_init() -> None:
    src = """
from dataclasses import dataclass

@dataclass
class Plain:
    x: int

    def __post_init__(self):
        self.check()

    def check(self):
        return 1

@dataclass(frozen=True)
class Frozen:
    y: int

    def __post_init__(self):
        self.validate()

    def validate(self):
        return 2

@dataclass
class CustomInit:
    z: int

    def __init__(self, z):
        self.setup()

    def setup(self):
        return 3

    def __post_init__(self):
        self.unused()

    def unused(self):
        return 4

Plain(1)
Frozen(2)
CustomInit(3)
"""
    assert edges(src) == {
        ("<module>", "Plain.__post_init__"),
        ("Plain.__post_init__", "Plain.check"),
        ("<module>", "Frozen.__post_init__"),
        ("Frozen.__post_init__", "Frozen.validate"),
        ("<module>", "CustomInit.__init__"),
        ("CustomInit.__init__", "CustomInit.setup"),
    }


def test_class_decorator_called_and_returns_class() -> None:
    src = """
def mark():
    return 1

def deco(cls):
    mark()
    return cls

@deco
class Item:
    def __init__(self):
        self.go()

    def go(self):
        return 0

Item()
"""
    assert edges(src) == {
        ("<module>", "deco"),
        ("deco", "mark"),
        ("<module>", "Item.__init__"),
        ("Item.__init__", "Item.go"),
    }
