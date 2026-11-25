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


def test_instance_and_class_attribute_assignments() -> None:
    src = """
def f(x):
    return x

def g(x):
    return x

def patched(self):
    return g(1)

class Base:
    def __init__(self, cb):
        self.cb = cb

    def run(self):
        return self.cb(0)

class SubA(Base):
    pass

class SubB(Base):
    pass

a = SubA(f)
a.run()
b = SubB(g)
b.run()
SubA.extra = patched
a.extra()
"""
    assert edges(src) == {
        ("<module>", "Base.__init__"),
        ("<module>", "Base.run"),
        ("<module>", "patched"),
        ("Base.run", "f"),
        ("Base.run", "g"),
        ("patched", "g"),
    }


def test_mutating_container_fields_and_user_append_get() -> None:
    src = """
def cb1():
    return 1

def cb2():
    return 2

def cb3():
    return 3

class Bus:
    def __init__(self):
        self.hooks = []
        self.table = {}

    def register(self, h, k):
        self.hooks.append(h)
        self.table[k] = h

    def fire(self):
        for h in self.hooks:
            h()
        self.table.get("missing", cb3)()

    def tick(self):
        return 0

class CustomStore:
    def append(self, x):
        return cb1()

    def get(self, k, default=None):
        return cb2

bus = Bus()
bus.register(cb1, "a")
bus.register(bus.tick, "b")
bus.fire()

store = CustomStore()
store.append(cb3)
store.get("k", cb3)()
"""
    assert edges(src) == {
        ("<module>", "Bus.__init__"),
        ("<module>", "Bus.register"),
        ("<module>", "Bus.fire"),
        ("Bus.fire", "cb1"),
        ("Bus.fire", "cb3"),
        ("Bus.fire", "Bus.tick"),
        ("<module>", "CustomStore.append"),
        ("<module>", "CustomStore.get"),
        ("<module>", "cb2"),
        ("CustomStore.append", "cb1"),
    }


def test_callable_instances_and_hofs() -> None:
    src = """
import functools

def target(x):
    return x

class Wrapper:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, x):
        return self.fn(x)

class Key:
    def __call__(self, x):
        return target(x)

@Wrapper
def wrapped(x):
    return x

wrapped(1)
sorted([2, 1], key=Key())
functools.partial(Wrapper(target), 1)()
"""
    assert edges(src) == {
        ("<module>", "Wrapper.__init__"),
        ("<module>", "Wrapper.__call__"),
        ("<module>", "Key.__call__"),
        ("Wrapper.__call__", "wrapped"),
        ("Wrapper.__call__", "target"),
        ("Key.__call__", "target"),
    }


def test_property_getter_setter_deleter_and_call_form() -> None:
    src = """
def on_get():
    return 1

def on_set(v):
    return v

def on_del():
    return 0

def raw_get(self):
    return on_get()

class Box:
    @property
    def val(self):
        return on_get()

    @val.setter
    def val(self, v):
        on_set(v)

    @val.deleter
    def val(self):
        on_del()

    raw = property(raw_get)

b = Box()
b.val
b.val = 10
del b.val
b.raw
"""
    assert edges(src) == {
        ("<module>", "Box.val"),
        ("<module>", "raw_get"),
        ("Box.val", "on_get"),
        ("Box.val", "on_set"),
        ("Box.val", "on_del"),
        ("raw_get", "on_get"),
    }


def test_descriptors_and_getattr_precision() -> None:
    src = """
def load():
    return 1

def store(v):
    return v

def drop():
    return 0

def fallback(n):
    return n

class Desc:
    def __get__(self, obj, owner):
        return load()

    def __set__(self, obj, v):
        store(v)

    def __delete__(self, obj):
        drop()

class Safe:
    d = Desc()

    def __init__(self):
        self.x = 123

    def __getattr__(self, name):
        return fallback(name)

    def __setattr__(self, name, v):
        super().__setattr__(name, v)

s = Safe()
s.x       # exists via self.x: must NOT trigger __getattr__
s.d
Safe.d
s.d = 1
del s.d
"""
    assert edges(src) == {
        ("<module>", "Safe.__init__"),
        ("<module>", "Desc.__get__"),
        ("<module>", "Safe.__setattr__"),
        ("<module>", "Desc.__delete__"),
        ("Safe.__init__", "Safe.__setattr__"),
        ("Safe.__setattr__", "Desc.__set__"),
        ("Desc.__get__", "load"),
        ("Desc.__set__", "store"),
        ("Desc.__delete__", "drop"),
    }


def test_getattr_fires_on_missing_and_records_attr_site() -> None:
    src = """
def miss(n):
    return n

class Dyn:
    def __getattr__(self, name):
        return miss(name)

d = Dyn()
d.unknown
"""
    cg = analyze_source(src, id_style="short")
    assert set(cg.function_edges()) == {
        ("<module>", "Dyn.__getattr__"),
        ("Dyn.__getattr__", "miss"),
    }
    attr_sites = [s for s in cg.sites if s.kind == "attr"]
    assert any(s.text == "d.unknown" and "Dyn.__getattr__" in cg.targets(s) for s in attr_sites)


def test_dunder_operators_reflected_and_augassign() -> None:
    src = """
def ping():
    return 1

class Num:
    def __add__(self, other):
        return Num()

    def __rsub__(self, other):
        return Num()

    def __iadd__(self, other):
        return self

    def __neg__(self):
        return self

    def __bool__(self):
        return True

    def touch(self):
        return ping()

a = Num()
b = Num()
(a + b).touch()
(1 - a).touch()
(-a).touch()
not a
a += b
"""
    assert edges(src) == {
        ("<module>", "Num.__add__"),
        ("<module>", "Num.__rsub__"),
        ("<module>", "Num.__neg__"),
        ("<module>", "Num.__bool__"),
        ("<module>", "Num.__iadd__"),
        ("<module>", "Num.touch"),
        ("Num.touch", "ping"),
    }


def test_dunder_subscript_context_manager_and_iteration() -> None:
    src = """
def leaf():
    return 1

class Resource:
    def use(self):
        return leaf()

class Ctx:
    def __enter__(self):
        return Resource()

    def __exit__(self, *exc):
        return False

class MyIter:
    def __next__(self):
        return Resource()

class Seq:
    def __getitem__(self, idx):
        return Resource()

    def __setitem__(self, idx, v):
        leaf()

    def __delitem__(self, idx):
        leaf()

    def __iter__(self):
        return MyIter()

    def __len__(self):
        return 1

    def __repr__(self):
        return "Seq"

with Ctx() as r:
    r.use()

s = Seq()
s[0].use()
s[0] = 1
del s[0]
for item in s:
    item.use()
it = iter(s)
next(it).use()
bool(s)      # falls back to __len__
str(s)       # falls back to __repr__
f"{s!r}"
"""
    assert edges(src) == {
        ("<module>", "Ctx.__enter__"),
        ("<module>", "Ctx.__exit__"),
        ("<module>", "Resource.use"),
        ("<module>", "Seq.__getitem__"),
        ("<module>", "Seq.__setitem__"),
        ("<module>", "Seq.__delitem__"),
        ("<module>", "Seq.__iter__"),
        ("<module>", "MyIter.__next__"),
        ("<module>", "Seq.__len__"),
        ("<module>", "Seq.__repr__"),
        ("Resource.use", "leaf"),
        ("Seq.__setitem__", "leaf"),
        ("Seq.__delitem__", "leaf"),
    }


def test_init_subclass_with_kwargs_and_super() -> None:
    src = """
def on_reg():
    return 1

def extra_cb():
    return 2

class Root:
    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        on_reg()

class Mid(Root):
    def __init_subclass__(cls, cb=None, **kw):
        super().__init_subclass__(**kw)
        if cb:
            cb()

class Leaf(Mid, cb=extra_cb):
    pass
"""
    assert edges(src) == {
        ("<module>", "Root.__init_subclass__"),
        ("<module>", "Mid.__init_subclass__"),
        ("Mid.__init_subclass__", "Root.__init_subclass__"),
        ("Mid.__init_subclass__", "extra_cb"),
        ("Root.__init_subclass__", "on_reg"),
    }


def test_metaclass_inheritance_submeta_and_meta_methods() -> None:
    src = """
def leaf():
    return 1

class Meta(type):
    def __call__(cls, *a):
        return super().__call__(*a)

    def meta_util(cls):
        return leaf()

class SubMeta(Meta):
    def __call__(cls, *a):
        return super().__call__(*a)

class Base(metaclass=SubMeta):
    def __init__(self):
        self.setup()

    def setup(self):
        return leaf()

class Child(Base):
    pass

Child()
Child.meta_util()
"""
    assert edges(src) == {
        ("<module>", "SubMeta.__call__"),
        ("<module>", "Meta.meta_util"),
        ("SubMeta.__call__", "Meta.__call__"),
        ("Meta.__call__", "Base.__init__"),
        ("Base.__init__", "Base.setup"),
        ("Base.setup", "leaf"),
        ("Meta.meta_util", "leaf"),
    }
