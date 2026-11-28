"""Unit tests for Step 1 class & method support."""
from __future__ import annotations

import pytest

from callme import analyze_source, to_dict, to_dot
from callme.frontend import build_program, call_sites

pytestmark = pytest.mark.family("classes")


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


def test_cyclic_and_inconsistent_mro_and_self_metaclass() -> None:
    from callme.analysis import State
    from callme.model import ClassId

    # Inconsistent hierarchy triggers C3 DFS fallback without crashing and still resolves methods.
    src = """
def from_x(): return 1
def from_y(): return 2
class X:
    def mx(self): return from_x()
class Y:
    def my(self): return from_y()
class A(X, Y): pass
class B(Y, X): pass
class C(A, B): pass
c = C()
c.mx()
c.my()
"""
    assert {("<module>", "X.mx"), ("<module>", "Y.my"),
            ("X.mx", "from_x"), ("Y.my", "from_y")} <= edges(src)

    # Cyclic bases and self-metaclass terminate safely.
    st = State()
    ca = ClassId("A", "t.py", 1, 0)
    cb = ClassId("B", "t.py", 2, 0)
    st.add_bases(ca, [cb])
    st.add_bases(cb, [ca])
    assert set(st.mro(ca)) == {ca, cb}
    st.add_metaclass(ca, ca)
    assert ca not in st.metaclasses


def test_super_edge_cases_and_metaclass_super_fallbacks() -> None:
    src = """
def leaf(): return 1
def zero_param():
    return super()
def outside(x):
    return super()

class Unrelated:
    def m(self): return leaf()

class Target:
    def m(self): return 0

class BaseMeta(type):
    @staticmethod
    def sm(): return leaf()

class SubMeta(BaseMeta):
    def __new__(mcls, name, bases, ns):
        return super().__new__(mcls)
    def __setattr__(cls, name, val):
        super().__setattr__(name, val)
    def run_sm(cls):
        super().__new__(cls)
        super().__setattr__("a", 1)
        return super().sm()

class StaticCallMeta(type):
    @staticmethod
    def __call__(*args):
        return leaf()

class K(metaclass=SubMeta):
    pass

class S(metaclass=StaticCallMeta):
    pass

zero_param()
outside(Target())
super(Unrelated, Target()).m()
K.sm()
K.run_sm()
K.x = 1
S()
"""
    got = edges(src)
    assert {("<module>", "SubMeta.__new__"), ("<module>", "BaseMeta.sm"),
            ("<module>", "SubMeta.run_sm"), ("SubMeta.run_sm", "BaseMeta.sm"),
            ("BaseMeta.sm", "leaf"), ("<module>", "StaticCallMeta.__call__"),
            ("StaticCallMeta.__call__", "leaf")} <= got
    assert ("<module>", "Unrelated.m") not in got


def test_property_accessors_non_descriptor_attr_class_kwstar_and_known_attrs() -> None:
    src = """
def g1(): return 1
def g2(): return 2
def s1(v): return v
def d1(): return 0
def miss(): return 9
def sub_hook(): return 3
def helper_fn(): return 4

class Helper:
    def run(self): return helper_fn()

class Base:
    ann: int
    def __init__(self):
        self.inherited = 1
    def __init_subclass__(cls, cb=None, **kw):
        if cb:
            cb()
    def __getattr__(self, name):
        return miss()

kw = {"cb": sub_hook}
class Child(Base, **kw):
    helper = Helper()
    @property
    def p(self):
        return g1()
    @p.setter
    def p(self, v):
        s1(v)
    @p.deleter
    def p(self):
        d1()
    p2 = p.getter(lambda self: g2())

c = Child()
c.ann          # annotated on Base: must NOT trigger __getattr__
c.inherited    # assigned on self in Base: must NOT trigger __getattr__
c.helper.run() # non-descriptor instance stored on class
Child.p.fget(c)
Child.p.fset(c, 1)
Child.p.fdel(c)
c.p2
"""
    got = edges(src)
    assert ("<module>", "Base.__getattr__") not in got
    assert ("Base.__getattr__", "miss") not in got
    assert {("<module>", "Base.__init_subclass__"), ("Base.__init_subclass__", "sub_hook"),
            ("<module>", "Helper.run"), ("Helper.run", "helper_fn"),
            ("<module>", "Child.p"), ("Child.p", "g1"), ("Child.p", "s1"), ("Child.p", "d1")} <= got
    assert any(callee == "g2" for _, callee in got)


def test_dataclass_default_inheritance_helpers_and_object_setattr_shadow() -> None:
    src = """
from dataclasses import asdict, astuple, dataclass, field, make_dataclass

def dflt_base(): return 1
def dflt_sub(): return 2
def post_cls(): return 3
def post_stat(): return 4
def on_set_sub(): return 5
def on_set_base(): return 6

@dataclass
class BaseDC:
    a: int = field()
    cb: object = field(default=dflt_base)

@dataclass
class SubDC(BaseDC):
    cb: object = field(default=dflt_sub)
    @classmethod
    def __post_init__(cls):
        post_cls()

@dataclass
class StatDC:
    @staticmethod
    def __post_init__():
        post_stat()

s = SubDC()
s.cb()
StatDC()
d = asdict(s)
d["k"] = post_stat
d["k"]()
astuple(s)
make_dataclass("Dyn", [("x", int)])

# object.__setattr__ skips shadowed descriptor from base class.
class BaseDesc:
    def __set__(self, obj, v): on_set_base()
class SubDesc:
    def __set__(self, obj, v): on_set_sub()
class P:
    x = BaseDesc()
class Q(P):
    x = SubDesc()
    def __setattr__(self, name, v):
        object.__setattr__(self, name, v)
Q().x = 1
"""
    got = edges(src)
    assert {("<module>", "dflt_sub"), ("<module>", "SubDC.__post_init__"),
            ("SubDC.__post_init__", "post_cls"), ("<module>", "StatDC.__post_init__"),
            ("StatDC.__post_init__", "post_stat"), ("<module>", "post_stat"),
            ("Q.__setattr__", "SubDesc.__set__"), ("SubDesc.__set__", "on_set_sub")} <= got
    assert ("Q.__setattr__", "BaseDesc.__set__") not in got
    assert ("<module>", "dflt_base") not in got


def test_remaining_dunder_operators_fstrings_and_augassign_fallback() -> None:
    src = """
def ping(): return 1
def spec_fn(): return ""

class LenOnly:
    def __len__(self): return 1

class ReprOnly:
    def __repr__(self): return "R"

class StrOnly:
    def __str__(self): return "S"

class Ops:
    def __pos__(self): return self
    def __invert__(self): return self
    def __matmul__(self, o): return self
    def __rpow__(self, o): return self
    def __gt__(self, o): return True
    def __lt__(self, o): return True
    def __contains__(self, o): return True
    def __add__(self, o): return self
    def touch(self): return ping()

o = Ops()
(+o).touch()
(~o).touch()
(o @ o).touch()
(2 ** o).touch()
1 < o < 3
0 not in o
o += 1  # falls back from missing __iadd__ to __add__
not LenOnly()  # falls back from missing __bool__ to __len__
_ = f"{ReprOnly()}"
_ = f"{ReprOnly()!s}"
_ = f"{StrOnly()!s}"
_ = f"{StrOnly():{spec_fn()}}"
"""
    assert {
        ("<module>", "Ops.__pos__"),
        ("<module>", "Ops.__invert__"),
        ("<module>", "Ops.__matmul__"),
        ("<module>", "Ops.__rpow__"),
        ("<module>", "Ops.__gt__"),
        ("<module>", "Ops.__lt__"),
        ("<module>", "Ops.__contains__"),
        ("<module>", "Ops.__add__"),
        ("<module>", "Ops.touch"),
        ("Ops.touch", "ping"),
        ("<module>", "LenOnly.__len__"),
        ("<module>", "ReprOnly.__repr__"),
        ("<module>", "StrOnly.__str__"),
        ("<module>", "spec_fn"),
    } <= edges(src)


def test_augassign_on_attribute_subscript_property_and_reflected() -> None:
    src = """
def leaf(): return 1
def on_get(): return Num()
def on_set(v): return v

class Num:
    def __iadd__(self, other): return self

class RAdd:
    def __radd__(self, other): return self

class Mapping:
    def __getitem__(self, k): return Num()
    def __setitem__(self, k, v): leaf()

class Holder:
    def __init__(self, xs):
        self.n = Num()
        self.xs = xs
    @property
    def p(self):
        return on_get()
    @p.setter
    def p(self, v):
        on_set(v)

shared = []
h = Holder(shared)
h.n += 1
h.xs += [leaf]
shared[0]()
h.p += 1
m = Mapping()
m[0] += 1
x = 1
x += RAdd()
"""
    assert {
        ("<module>", "Holder.__init__"),
        ("<module>", "Num.__iadd__"),
        ("<module>", "leaf"),
        ("<module>", "Holder.p"),
        ("Holder.p", "on_get"),
        ("Holder.p", "on_set"),
        ("<module>", "Mapping.__getitem__"),
        ("<module>", "Mapping.__setitem__"),
        ("Mapping.__setitem__", "leaf"),
        ("<module>", "RAdd.__radd__"),
    } <= edges(src)
