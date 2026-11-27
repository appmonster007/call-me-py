"""Stub repository: how builtin / stdlib callables use their arguments.

The analyser never sees the C source of `map`, `sorted`, `functools.reduce`, ...
Each stub here replays, on abstract values, the calls such a function makes and
the value it returns. Callbacks are invoked through `Interp.apply` at the HOF's
own call site, so the edge goes from the Python function that called the HOF to
the callback (matching a traced run, where C frames are invisible).

Abstract values are sets of tokens and containers are collapsed to the union of
their elements, so "the elements of iterable `xs`" is just the value of `xs`.

Adding a stub: write `def _x(it, a, site) -> Vals` (unused parameters get a `_`
prefix) and register it with `@stub("name", "module.name", ...)`. Names without a
dot are builtins (looked up when no user binding shadows them); dotted names are
reached via `import module` / `from module import name`; `<...>` names are internal.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .extract import FuncInfo
from .model import (
    EMPTY,
    Alloc,
    BoundMethod,
    Box,
    Builtin,
    ClassId,
    ClassMethod,
    ClassObj,
    Closure,
    Instance,
    Property,
    Site,
    StaticMethod,
    Super,
    Vals,
)

STUB_MODULES = frozenset({
    "asyncio",
    "builtins",
    "collections",
    "concurrent.futures",
    "contextlib",
    "dataclasses",
    "functools",
    "importlib",
    "itertools",
    "threading",
})
STUB_PACKAGES = frozenset(
    ".".join(parts[:i])
    for m in STUB_MODULES if "." in m
    for parts in (m.split("."),)
    for i in range(1, len(parts))
)
METHOD_STUBS = {  # attribute name -> stub (receiver is bound first)
    "sort": "list.sort", "get": "dict.get",
    **{m: "<elements>" for m in ("values", "items", "keys", "copy", "pop", "popleft", "popitem")},
}
MAX_PARTIAL_ARGS = 4  # bound arguments before a partial is widened to <partial*>
MAX_PARTIAL_DEPTH = 3  # nesting of tokens inside a partial's arguments


@dataclass
class Args:
    """Abstract arguments of one stub call, with the token's bound arguments prepended.

    Containers are already dereferenced, so an argument's value includes everything stored
    into the containers it holds; "the elements of `xs`" is just the value of `xs`.

    pos     values of the positional arguments, in order
    star    values passed through `*args`, or None
    kws     values of the keyword arguments, by name
    kwstar  values passed through `**kwargs`, or None
    """

    pos: list[Vals]
    star: Vals | None
    kws: dict[str, Vals]
    kwstar: Vals | None

    def get(self, i: int, kw: str | None = None) -> Vals:
        """Return the value of positional argument `i`, or of keyword `kw` if it is not given.

        If neither is given, return what `*args` and `**kwargs` may supply, which is `EMPTY`
        when there are none.
        """
        if i < len(self.pos):
            return self.pos[i]
        if kw is not None and kw in self.kws:
            return self.kws[kw]
        return (self.star or EMPTY) | (self.kwstar or EMPTY)

    def kw(self, name: str) -> Vals:
        """Return the value of keyword argument `name`, plus what `**kwargs` may supply."""
        return self.kws.get(name, EMPTY) | (self.kwstar or EMPTY)

    def rest(self, i: int) -> Vals:
        """Return the union of the positional arguments from index `i` on, `*args` included."""
        return union(self.pos[i:]) | (self.star or EMPTY)

    def all(self) -> Vals:
        """Return the union of every argument: positional, keyword, `*args` and `**kwargs`."""
        return self.rest(0) | union(self.kws.values()) | (self.kwstar or EMPTY)


class HostState(Protocol):
    """The parts of `transfer.State` that stubs use, as a protocol."""

    @property
    def class_attrs(self) -> Mapping[ClassId, Mapping[str, Vals]]:
        """Return the class attribute table: the values of each attribute of each class."""
        ...

    def mro(self, cls: ClassId) -> tuple[ClassId, ...]:
        """Return the method resolution order of `cls`, starting with `cls` itself."""
        ...

    def add_class_attr(self, cls: ClassId, name: str, vals: Vals) -> None:
        """Join `vals` into attribute `name` of class `cls`."""
        ...

    def add_inst_attr(self, inst: Instance, name: str, vals: Vals) -> None:
        """Join `vals` into attribute `name` of instance `inst`."""
        ...

    def add_heap(self, box: Box, vals: Vals) -> None:
        """Join `vals` into what is stored in container `box`."""
        ...

    def lookup_class_attr(self, cls: ClassId, name: str, *,
                          after: ClassId | None = None) -> Vals:
        """Return attribute `name` of the first class in `cls`'s MRO (after `after`) that has it."""
        ...


class Host(Protocol):
    """The parts of `transfer.Interp` that stubs use, as a protocol.

    A protocol keeps this module free of imports from transfer.py, which imports it. Calls
    made through it come from the node whose code called the stub.
    """

    @property
    def info(self) -> FuncInfo:
        """Return the `FuncInfo` of the function being analysed."""
        ...

    @property
    def readonly(self) -> bool:
        """Return True while default values are evaluated, when no state may be written."""
        ...

    @property
    def state(self) -> HostState:
        """Return the global analysis state."""
        ...

    def lookup(self, name: str) -> Vals:
        """Return the value of variable `name` in the current scope."""
        ...

    def apply(self, callee: Vals, pos: Sequence[Vals], star: Vals | None,
              kws: Mapping[str, Vals], kwstar: Vals | None, site: Site) -> Vals:
        """Call every callable in `callee` with these arguments at `site`; return the results."""
        ...

    def instantiate(self, tok: ClassObj, pos: Sequence[Vals], star: Vals | None,
                    kws: Mapping[str, Vals], kwstar: Vals | None, site: Site) -> Vals:
        """Create instances of class `tok` as `type.__call__` does, and return them."""
        ...

    def invoke_dunder(self, recv: Vals, name: str, pos: Sequence[Vals],
                      site: Site) -> tuple[bool, Vals]:
        """Call special method `name` on the instances in `recv`; return (found, results)."""
        ...

    def invoke_data_descriptor_set(self, obj: Instance, cls_vals: Vals,
                                   vals: Vals, site: Site) -> bool:
        """Call the data descriptor setters in `cls_vals` for `obj`; return True if any ran."""
        ...

    def iterate(self, iterable: Vals, site: Site, *, is_async: bool = False) -> Vals:
        """Return the values that iterating `iterable` yields, calling `__iter__` and `__next__`."""
        ...

    def import_any(self) -> Vals:
        """Return every module of the program, and run each, as an import of an unknown name may."""
        ...


StubFn = Callable[[Host, Args, Site], Vals]
STUBS: dict[str, StubFn] = {}


def stub(*names: str) -> Callable[[StubFn], StubFn]:
    """Return a decorator that registers a stub function under each of `names`.

    A name without a dot is a builtin; it is also added to `BUILTIN_NAMES`, and found by name
    unless user code shadows it. `module.name` is reached through `import module` or
    `from module import name`, and its module must be in `STUB_MODULES`. A name in angle
    brackets, such as `<partial>`, is an internal token that user code cannot name.
    """
    def reg(fn: StubFn) -> StubFn:
        """Register `fn` under every name and return it unchanged."""
        for n in names:
            STUBS[n] = fn
        return fn
    return reg


def union(vs: Iterable[Vals]) -> Vals:
    """Return the union of all the value sets in `vs`."""
    out = EMPTY
    for v in vs:
        out |= v
    return out


def call(it: Host, f: Vals, args: list[Vals], site: Site, star: Vals | None = None) -> Vals:
    """Call every callable in `f` with positional `args` at `site`, and return their results.

    The call is made from the node that called the stub, so its edges go from the Python
    function that called the builtin, as in a traced run where C frames are invisible.
    `star` is passed as `*args`. With an empty `f` nothing is called and `EMPTY` is returned.
    """
    if not f:
        return EMPTY
    return it.apply(f, args, star, {}, None, site)


def fold(it: Host, f: Vals, acc: Vals, elems: Vals, site: Site) -> Vals:
    """Return the accumulator of `acc = f(acc, element)`, iterated until it stops growing.

    Each round calls `f` with the accumulator so far and the elements, and joins the result
    into it; this models `functools.reduce` and `itertools.accumulate`.
    """
    while True:
        r = call(it, f, [acc, elems], site)
        if r <= acc:
            return acc
        acc |= r


# ---------------------------------------------------------------- builtins
@stub("map")
def _map(it: Host, a: Args, site: Site) -> Vals:
    """Model `map(f, *iterables)`: call `f` with an element of each iterable; return its results."""
    f = a.get(0)
    if a.star is not None:  # unknown number of iterables
        return call(it, f, [], site, star=a.rest(1))
    return call(it, f, a.pos[1:], site)


@stub("filter", "itertools.filterfalse", "itertools.takewhile", "itertools.dropwhile")
def _filter(it: Host, a: Args, site: Site) -> Vals:
    """Model `filter(f, xs)` and its itertools variants: call `f` on the elements; return them."""
    elems = a.get(1)
    call(it, a.get(0), [elems], site)
    return elems


@stub("sorted")
def _sorted(it: Host, a: Args, site: Site) -> Vals:
    """Model `sorted(xs, key=k)`: call `k` on the elements, then compare the keys with `__lt__`.

    Without a key the elements themselves are compared. Return the elements.
    """
    elems = a.get(0)
    key = a.kw("key")
    if key:
        keys = call(it, key, [elems], site)
        it.invoke_dunder(keys, "__lt__", [keys], site)
    else:
        it.invoke_dunder(elems, "__lt__", [elems], site)
    return elems


@stub("min", "max")
def _minmax(it: Host, a: Args, site: Site) -> Vals:
    """Model `min` and `max`: call `key` on the elements and compare the keys with `__lt__`.

    The elements come from the single iterable argument, or from all positional arguments
    when there are several. Return the elements, plus the `default` keyword.
    """
    elems = a.pos[0] if len(a.pos) == 1 and a.star is None else a.rest(0)
    key = a.kw("key")
    if key:
        keys = call(it, key, [elems], site)
        it.invoke_dunder(keys, "__lt__", [keys], site)
    else:
        it.invoke_dunder(elems, "__lt__", [elems], site)
    return elems | a.kw("default")


@stub("list.sort")
def _list_sort(it: Host, a: Args, site: Site) -> Vals:
    """Model `xs.sort(key=k)`: call `k` on the elements and compare the keys; return nothing."""
    elems = a.get(0)
    key = a.kw("key")
    if key:
        keys = call(it, key, [elems], site)
        it.invoke_dunder(keys, "__lt__", [keys], site)
    else:
        it.invoke_dunder(elems, "__lt__", [elems], site)
    return EMPTY


@stub("dict.get")
def _dict_get(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `d.get(k, default)`: return the dictionary's values plus `default`."""
    return a.get(0) | a.get(2, "default")


@stub("len")
def _len(it: Host, a: Args, site: Site) -> Vals:
    """Model `len(x)`: call `x.__len__`; return nothing callable."""
    it.invoke_dunder(a.get(0), "__len__", [], site)
    return EMPTY


@stub("str")
def _str(it: Host, a: Args, site: Site) -> Vals:
    """Model `str(x)`: call `x.__str__`, or `x.__repr__` without it; return nothing callable."""
    v = a.get(0)
    found, _ = it.invoke_dunder(v, "__str__", [], site)
    if not found:
        it.invoke_dunder(v, "__repr__", [], site)
    return EMPTY


@stub("repr")
def _repr(it: Host, a: Args, site: Site) -> Vals:
    """Model `repr(x)`: call `x.__repr__`; return nothing callable."""
    it.invoke_dunder(a.get(0), "__repr__", [], site)
    return EMPTY


@stub("bool")
def _bool(it: Host, a: Args, site: Site) -> Vals:
    """Model `bool(x)`: call `x.__bool__`, or `x.__len__` without it; return nothing callable."""
    v = a.get(0)
    found, _ = it.invoke_dunder(v, "__bool__", [], site)
    if not found:
        it.invoke_dunder(v, "__len__", [], site)
    return EMPTY


@stub("hash")
def _hash(it: Host, a: Args, site: Site) -> Vals:
    """Model `hash(x)`: call `x.__hash__`; return nothing callable."""
    it.invoke_dunder(a.get(0), "__hash__", [], site)
    return EMPTY


@stub("iter")
def _iter(it: Host, a: Args, site: Site) -> Vals:
    """Model `iter(x)` and `iter(callable, sentinel)`.

    The one-argument form returns what `x.__iter__` returns, or `x` itself for non-instances.
    The two-argument form calls the callable with no arguments and returns its results.
    """
    if len(a.pos) >= 2:
        return call(it, a.pos[0], [], site)
    v = a.get(0)
    found, out = it.invoke_dunder(v, "__iter__", [], site)
    return out if found else v


@stub("aiter")
def _aiter(it: Host, a: Args, site: Site) -> Vals:
    """Model `aiter(x)`: return what `x.__aiter__` returns, or `x` itself for non-instances."""
    v = a.get(0)
    found, out = it.invoke_dunder(v, "__aiter__", [], site)
    return out if found else v


@stub("next")
def _next(it: Host, a: Args, site: Site) -> Vals:
    """Model `next(it, default)`: return what `it.__next__` returns, plus `default`.

    Values without `__next__`, such as collapsed containers, stand for their own elements and
    are returned as they are.
    """
    v = a.get(0)
    default = a.get(1)
    found, out = it.invoke_dunder(v, "__next__", [], site)
    return (out if found else v) | default


@stub("anext")
def _anext(it: Host, a: Args, site: Site) -> Vals:
    """Model `anext(it, default)`: return what `it.__anext__` returns, plus `default`."""
    v = a.get(0)
    default = a.get(1)
    found, out = it.invoke_dunder(v, "__anext__", [], site)
    return (out if found else v) | default


@stub("any", "all")
def _any_all(it: Host, a: Args, site: Site) -> Vals:
    """Model `any(xs)` and `all(xs)`: iterate `xs` and test truthiness of each element."""
    elems = it.iterate(a.get(0), site)
    found, _ = it.invoke_dunder(elems, "__bool__", [], site)
    if not found:
        it.invoke_dunder(elems, "__len__", [], site)
    return EMPTY


@stub("sum")
def _sum(it: Host, a: Args, site: Site) -> Vals:
    """Model `sum(xs, start=0)`: iterate `xs` for its iterator calls; return nothing callable."""
    it.iterate(a.get(0), site)
    return EMPTY


@stub("abs")
def _abs(it: Host, a: Args, site: Site) -> Vals:
    """Model `abs(x)`: call `x.__abs__`; return nothing callable."""
    it.invoke_dunder(a.get(0), "__abs__", [], site)
    return EMPTY


@stub("round")
def _round(it: Host, a: Args, site: Site) -> Vals:
    """Model `round(x, ndigits)`: call `x.__round__`; return nothing callable."""
    it.invoke_dunder(a.get(0), "__round__", [], site)
    return EMPTY


@stub("format")
def _format(it: Host, a: Args, site: Site) -> Vals:
    """Model `format(x, spec)`: call `__format__`, falling back to `__str__` then `__repr__`."""
    v = a.get(0)
    found, _ = it.invoke_dunder(v, "__format__", [EMPTY], site)
    if not found:
        found, _ = it.invoke_dunder(v, "__str__", [], site)
    if not found:
        it.invoke_dunder(v, "__repr__", [], site)
    return EMPTY


@stub("int")
def _int(it: Host, a: Args, site: Site) -> Vals:
    """Model `int(x)`: call `x.__int__`, falling back to `x.__index__`; return nothing callable."""
    v = a.get(0)
    found, _ = it.invoke_dunder(v, "__int__", [], site)
    if not found:
        it.invoke_dunder(v, "__index__", [], site)
    return EMPTY


@stub("float")
def _float(it: Host, a: Args, site: Site) -> Vals:
    """Model `float(x)`: call `x.__float__`; return nothing callable."""
    it.invoke_dunder(a.get(0), "__float__", [], site)
    return EMPTY


@stub("complex")
def _complex(it: Host, a: Args, site: Site) -> Vals:
    """Model `complex(x)`: call `x.__complex__`; return nothing callable."""
    it.invoke_dunder(a.get(0), "__complex__", [], site)
    return EMPTY


@stub("bytes")
def _bytes(it: Host, a: Args, site: Site) -> Vals:
    """Model `bytes(x)`: call `x.__bytes__`; return nothing callable."""
    it.invoke_dunder(a.get(0), "__bytes__", [], site)
    return EMPTY


@stub("reversed")
def _reversed(it: Host, a: Args, site: Site) -> Vals:
    """Model `reversed(x)`: call `x.__reversed__` and the `__next__` of what it returns.

    Return the elements that `__next__` yields, or the reversed iterator when it yields
    nothing known. Without `__reversed__`, return the elements of iterating `x`.
    """
    v = a.get(0)
    found, out = it.invoke_dunder(v, "__reversed__", [], site)
    if found:
        _, elem = it.invoke_dunder(out, "__next__", [], site)
        return elem or out
    return it.iterate(v, site)


@stub("classmethod")
def _classmethod(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `classmethod(f)`: wrap each function in a `ClassMethod`, dropping too deep tokens."""
    return frozenset(
        t if isinstance(t, ClassMethod) else ClassMethod(t)
        for t in a.get(0) if isinstance(t, ClassMethod) or t.depth <= MAX_PARTIAL_DEPTH
    )


@stub("staticmethod")
def _staticmethod(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `staticmethod(f)`: wrap each function in a `StaticMethod`, dropping too deep tokens."""
    return frozenset(
        t if isinstance(t, StaticMethod) else StaticMethod(t)
        for t in a.get(0) if isinstance(t, StaticMethod) or t.depth <= MAX_PARTIAL_DEPTH
    )


@stub("super")
def _super(it: Host, a: Args, _site: Site) -> Vals:
    """Model `super()` and `super(C, obj)`: return a `Super` token per start class and receiver.

    The zero-argument form uses the class whose body defines the current function, and the
    value of its first positional parameter (`self` or `cls`). It returns `EMPTY` outside a
    method.
    """
    if not a.pos:  # 0-arg super(): enclosing class + first positional param (self / cls)
        owner = it.info.owner
        if owner is None or not it.info.sig.pos:
            return EMPTY
        recv = it.lookup(it.info.sig.pos[0])
        return frozenset(Super(owner, r) for r in recv if isinstance(r, (ClassObj, Instance)))
    starts = [t.cls for t in a.get(0) if isinstance(t, ClassObj)]
    recvs = [r for r in a.get(1) if isinstance(r, (ClassObj, Instance))]
    return frozenset(Super(s, r) for s in starts for r in recvs)


def _prop_fn(v: Vals) -> Vals:
    """Return the accessor functions in `v`: properties and too deep tokens are dropped."""
    return frozenset(t for t in v if not isinstance(t, Property) and t.depth <= MAX_PARTIAL_DEPTH)


@stub("property")
def _property(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `property(fget, fset, fdel)`: return one `Property` holding the three accessors."""
    return frozenset([Property(
        _prop_fn(a.get(0, "fget")),
        _prop_fn(a.get(1, "fset")),
        _prop_fn(a.get(2, "fdel")),
    )])


@stub("<property_getter>")
def _property_getter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `prop.getter(f)`: return a copy of each property with `f` as its getter."""
    fn = _prop_fn(a.get(1))
    return frozenset(Property(fn, p.fset, p.fdel) for p in a.get(0) if isinstance(p, Property))


@stub("<property_setter>")
def _property_setter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `prop.setter(f)`: return a copy of each property with `f` as its setter."""
    fn = _prop_fn(a.get(1))
    return frozenset(Property(p.fget, fn, p.fdel) for p in a.get(0) if isinstance(p, Property))


@stub("<property_deleter>")
def _property_deleter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `prop.deleter(f)`: return a copy of each property with `f` as its deleter."""
    fn = _prop_fn(a.get(1))
    return frozenset(Property(p.fget, p.fset, fn) for p in a.get(0) if isinstance(p, Property))


@stub("object")
def _object(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `object()`: return nothing callable."""
    return EMPTY


@stub("type")
def _type(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `type(...)` as returning nothing: neither `type(x)` nor new classes are tracked."""
    return EMPTY


@stub("type.__call__")
def _type_call(it: Host, a: Args, site: Site) -> Vals:
    """Model the default metaclass call `type.__call__(cls, ...)`: instantiate each class."""
    out = EMPTY
    for tok in a.get(0):
        if isinstance(tok, ClassObj):
            out |= it.instantiate(tok, a.pos[1:], a.star, a.kws, a.kwstar, site)
    return out


@stub("object.__new__")
def _object_new(_it: Host, a: Args, site: Site) -> Vals:
    """Model `object.__new__(cls)`: return a new instance of each class, allocated at this call."""
    return frozenset(Instance(t.cls, Alloc.of(site)) for t in a.get(0) if isinstance(t, ClassObj))


@stub("object.__setattr__")
def _object_setattr(it: Host, a: Args, site: Site) -> Vals:
    """Model `object.__setattr__(obj, name, value)`, which skips a custom `__setattr__`.

    The attribute name is not tracked, so `value` goes to the setter of every data descriptor
    (a property or an object with `__set__`) found in the classes of `obj`'s MRO.
    """
    val = a.get(2)
    for obj in a.get(0):
        if isinstance(obj, Instance):
            seen: set[str] = set()
            for c in it.state.mro(obj.cls):
                for attr, toks in list(it.state.class_attrs.get(c, {}).items()):
                    if attr in seen:
                        continue
                    seen.add(attr)
                    it.invoke_data_descriptor_set(obj, toks, val, site)
    return EMPTY


def _apply_dataclass(it: Host, cls_vals: Vals) -> Vals:
    """Give each class in `cls_vals` the generated dataclass `__init__`, and return the classes.

    A class that defines its own `__init__` keeps it. Nothing is written in read-only mode.
    """
    for t in cls_vals:
        if isinstance(t, ClassObj) and not it.readonly:
            if "__init__" not in it.state.class_attrs.get(t.cls, {}):
                it.state.add_class_attr(t.cls, "__init__", frozenset([Builtin("<dataclass_init>")]))
    return cls_vals


@stub("dataclasses.dataclass")
def _dataclass(it: Host, a: Args, _site: Site) -> Vals:
    """Model `dataclasses.dataclass` used bare or with options.

    `@dataclass` applied to a class turns it into a dataclass at once. `@dataclass(...)` with
    options returns the `<dataclass>` decorator, which does the same when it is applied.
    """
    cls = a.pos[0] if a.pos else EMPTY
    if any(isinstance(t, ClassObj) for t in cls):
        return _apply_dataclass(it, cls)
    return frozenset([Builtin("<dataclass>")])


@stub("<dataclass>")
def _dataclass_deco(it: Host, a: Args, _site: Site) -> Vals:
    """Apply the `<dataclass>` decorator returned by `dataclass(...)` to a class."""
    return _apply_dataclass(it, a.get(0))


@stub("dataclasses.field")
def _dataclass_field(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `dataclasses.field(default=..., default_factory=...)`: record factory and default."""
    factory = _shallow(a.kw("default_factory"))
    default = _shallow(a.kw("default"))
    if not factory and not default:
        return EMPTY
    return frozenset([Builtin("<dataclass_field>", (factory, default))])


@stub("<dataclass_init>")
def _dataclass_init(it: Host, a: Args, site: Site) -> Vals:
    """Model a generated dataclass `__init__`: run field factories, then call `__post_init__`.

    Each `field(default_factory=...)` in the class's MRO has its factory invoked and the result
    joined into that instance attribute. `__post_init__` is then bound as Python would bind it:
    to the class for a classmethod, unbound for a staticmethod, and to the instance otherwise.
    """
    for obj in a.get(0):
        if isinstance(obj, Instance):
            seen: set[str] = set()
            for c in it.state.mro(obj.cls):
                for attr, toks in list(it.state.class_attrs.get(c, {}).items()):
                    if attr in seen:
                        continue
                    seen.add(attr)
                    for tok in toks:
                        if isinstance(tok, Builtin) and tok.name == "<dataclass_field>":
                            val = call(it, tok.bound[0], [], site) | tok.bound[1]
                            if val and not it.readonly:
                                it.state.add_inst_attr(obj, attr, val)
            post = it.state.lookup_class_attr(obj.cls, "__post_init__")
            if post:
                bound = frozenset(
                    BoundMethod(t.func, ClassObj(obj.cls)) if isinstance(t, ClassMethod)
                    else t.func if isinstance(t, StaticMethod)
                    else BoundMethod(t, obj)
                    for t in post
                )
                it.apply(bound, [], None, {}, None, site)
    return EMPTY


@stub("dataclasses.replace")
def _dataclass_replace(it: Host, a: Args, site: Site) -> Vals:
    """Model `dataclasses.replace(obj, **changes)`: re-instantiate `obj`'s class with updates."""
    objs = a.get(0)
    out = objs
    kwstar = union(a.kws.values()) | (a.kwstar or EMPTY)
    for obj in objs:
        if isinstance(obj, Instance):
            out |= it.instantiate(ClassObj(obj.cls), [], None, a.kws, kwstar, site)
    return out


@stub("dataclasses.asdict")
def _dataclass_asdict(it: Host, a: Args, site: Site) -> Vals:
    """Model `dataclasses.asdict(obj, dict_factory=dict)`: invoke `dict_factory` if supplied."""
    factory = a.get(1, "dict_factory")
    if factory:
        return call(it, factory, [EMPTY], site)
    return frozenset([Box("dict", Alloc.of(site))])


@stub("dataclasses.astuple")
def _dataclass_astuple(it: Host, a: Args, site: Site) -> Vals:
    """Model `dataclasses.astuple(obj, tuple_factory=tuple)`: invoke `tuple_factory` if supplied."""
    factory = a.get(1, "tuple_factory")
    if factory:
        return call(it, factory, [EMPTY], site)
    return EMPTY


@stub("dataclasses.make_dataclass")
def _make_dataclass(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `dataclasses.make_dataclass(...)`: dynamic class creation is not tracked."""
    return EMPTY


@stub("tuple", "frozenset", "enumerate", "zip",
      "itertools.chain", "itertools.cycle", "itertools.islice")
def _passthrough(it: Host, a: Args, site: Site) -> Vals:
    """Model `tuple`, `zip`, `enumerate`, `chain` and the like: return every argument's elements."""
    return it.iterate(a.all(), site)


def _container(kind: str) -> StubFn:
    """Return the stub for `list`, `set` or `dict` (by `kind`), which builds a new container."""
    def fn(it: Host, a: Args, site: Site) -> Vals:
        """Model `list(xs)`, `set(xs)` or `dict(...)`: the elements and a `Box` for this call."""
        return it.iterate(a.all(), site) | frozenset([Box(kind, Alloc.of(site))])
    return fn


for _kind in ("list", "set", "dict"):
    stub(_kind)(_container(_kind))


@stub("<elements>")
def _elements(_it: Host, a: Args, _site: Site) -> Vals:
    """Model the element methods of containers: return the receiver's elements and any default.

    Covers `d.values()`, `d.items()`, `d.keys()`, `xs.copy()`, `xs.pop(i)`, `d.pop(k, default)`
    and `d.popitem()`; the receiver is the first argument.
    """
    return a.get(0) | union(a.pos[2:])  # receiver's elements (already deref'd) | default


@stub("__import__", "importlib.import_module")
def _import(it: Host, _a: Args, _site: Site) -> Vals:
    """Model `__import__(name)` and `importlib.import_module(name)`: return any program module.

    Strings are not tracked, so the name is unknown: every module of the program may come
    back, and each one runs (`Host.import_any`). Modules outside the program are not found.
    """
    return it.import_any()


# ---------------------------------------------------------------- functools / itertools
@stub("functools.reduce")
def _reduce(it: Host, a: Args, site: Site) -> Vals:
    """Model `functools.reduce(f, xs, initial)`: fold `f` over the elements (see `fold`)."""
    elems = a.get(1)
    return fold(it, a.get(0), elems | a.get(2, "initial"), elems, site)


@stub("itertools.accumulate")
def _accumulate(it: Host, a: Args, site: Site) -> Vals:
    """Model `itertools.accumulate(xs, func, initial=...)`: fold `func` over the elements."""
    elems = a.get(0)
    return fold(it, a.get(1, "func"), elems | a.kw("initial"), elems, site)


@stub("itertools.starmap")
def _starmap(it: Host, a: Args, site: Site) -> Vals:
    """Model `itertools.starmap(f, xs)`: call `f` with each element unpacked as its arguments."""
    return call(it, a.get(0), [], site, star=a.get(1))


@stub("itertools.groupby")
def _groupby(it: Host, a: Args, site: Site) -> Vals:
    """Model `itertools.groupby(xs, key)`: call `key` on the elements; return the elements."""
    elems = a.get(0)
    call(it, a.get(1, "key"), [elems], site)
    return elems


IDENTITY = Builtin("<identity>")


@stub("<identity>")
def _identity(_it: Host, a: Args, _site: Site) -> Vals:
    """Return the first argument: the decorator that `wraps(f)` and `lru_cache(...)` return."""
    return a.get(0)


@stub("functools.cache", "functools.lru_cache")
def _cache(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `functools.cache` and `lru_cache`, which return the decorated function itself.

    `cache(f)` and `lru_cache(f)` return `f`. Called with options only, as in
    `lru_cache(maxsize=32)`, they return the identity decorator.
    """
    f = a.pos[0] if a.pos else EMPTY
    return f if f else frozenset([IDENTITY])


@stub("functools.wraps")
def _wraps(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `functools.wraps(f)`: return the identity decorator, which keeps the wrapper."""
    return frozenset([IDENTITY])


@stub("functools.update_wrapper")
def _update_wrapper(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `functools.update_wrapper(wrapper, wrapped)`: return `wrapper`."""
    return a.get(0)


def _shallow(v: Vals) -> Vals:
    """Drop tokens nested `MAX_PARTIAL_DEPTH` deep from `v`, so nested tokens stay finite."""
    return frozenset(t for t in v if t.depth < MAX_PARTIAL_DEPTH)


def _boxes(vals: Vals, kind: str) -> list[Box]:
    """Return every `Box` token of `kind` present in `vals`."""
    return [t for t in vals if isinstance(t, Box) and t.kind == kind]


def _make_partial(fn_vals: Vals, pos: Sequence[Vals], star: Vals | None,
                  kws: Mapping[str, Vals], kwstar: Vals | None, *,
                  name: str = "<partial>", wide_name: str = "<partial*>") -> Vals:
    """Build bounded partial tokens (`name` or widened `wide_name`) for each callable in `fn_vals`.

    A partial of a partial is flattened into one. With more than `MAX_PARTIAL_ARGS` bound
    arguments, or `*` or `**` at construction, the order of arguments is lost and the token is
    widened to `wide_name`. Too deep argument tokens are dropped (`_shallow`).
    """
    out: set[Builtin] = set()
    rest = [_shallow(v) for v in pos]
    kw_map = {k: _shallow(v) for k, v in kws.items()}
    extra = _shallow((star or EMPTY) | (kwstar or EMPTY))
    for t in fn_vals:
        if isinstance(t, Builtin) and t.name in ("<partial>", "<partial*>",
                                                 "<partialmethod>", "<partialmethod*>"):
            f, args, kw = t.bound[0], [*t.bound[1:], *rest], dict(t.bound_kw)
            for k, v in kw_map.items():
                kw[k] = kw.get(k, EMPTY) | v
            widened = t.name.endswith("*>")
        else:
            f, args, kw, widened = frozenset([t]), list(rest), dict(kw_map), False
        if widened or extra or len(args) > MAX_PARTIAL_ARGS:
            everything = union(args) | union(kw.values()) | extra
            out.add(Builtin(wide_name, (f, everything)))
        else:
            out.add(Builtin(name, (f, *args), tuple(sorted(kw.items(), key=lambda p: p[0]))))
    return frozenset(out)


@stub("functools.partial")
def _partial(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `functools.partial(f, *args, **kws)`: return a `<partial>` token per function."""
    return _make_partial(a.get(0), a.pos[1:], a.star, a.kws, a.kwstar)


@stub("functools.partialmethod")
def _partialmethod(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `functools.partialmethod(f, *args, **kws)`: descriptor binding `self` before `args`."""
    return _make_partial(a.get(0), a.pos[1:], a.star, a.kws, a.kwstar,
                         name="<partialmethod>", wide_name="<partialmethod*>")


@stub("<partial>", "<partialmethod>")
def _call_partial(it: Host, a: Args, site: Site) -> Vals:
    """Call a `<partial>` token: its function gets the bound arguments, then the call's own."""
    # a.pos = [f, *bound args, *call args]; bound keywords are already merged into a.kws
    return it.apply(a.pos[0], a.pos[1:], a.star, a.kws, a.kwstar, site)


@stub("<partial*>", "<partialmethod*>")
def _call_partial_widened(it: Host, a: Args, site: Site) -> Vals:
    """Call a `<partial*>` token: every bound and call argument goes to the function as `*args`."""
    return it.apply(a.pos[0], [], a.rest(1), a.kws, a.kwstar, site)


@stub("functools.cached_property")
def _cached_property(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `functools.cached_property(f)`: non-data descriptor calling `f(self)` on read."""
    return frozenset([Property(_prop_fn(a.get(0)))])


@stub("functools.total_ordering")
def _total_ordering(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `@functools.total_ordering`: return the decorated class unchanged."""
    return a.get(0)


@stub("functools.cmp_to_key")
def _cmp_to_key(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `functools.cmp_to_key(mycmp)`: return a key function wrapping `mycmp`."""
    return frozenset([Builtin("<cmp_to_key>", (_shallow(a.get(0)),))])


@stub("<cmp_to_key>")
def _call_cmp_to_key(_it: Host, a: Args, _site: Site) -> Vals:
    """Model calling a `cmp_to_key` wrapper on an element: return a comparable key object."""
    return frozenset([Builtin("<cmp_key>", (a.pos[0], _shallow(a.get(1))))])


@stub("<cmp_key>.__lt__", "<cmp_key>.__gt__", "<cmp_key>.__le__",
      "<cmp_key>.__ge__", "<cmp_key>.__eq__", "<cmp_key>.__ne__")
def _cmp_key_op(it: Host, a: Args, site: Site) -> Vals:
    """Model comparing two `cmp_to_key` keys: call the underlying comparison function."""
    elem = a.pos[1]
    call(it, a.pos[0], [elem, elem], site)
    return EMPTY


def _is_dispatch_fn(vals: Vals) -> bool:
    """Return True if `vals` looks like a registered implementation rather than a type argument."""
    return any(
        isinstance(t, (Closure, BoundMethod))
        or (isinstance(t, Builtin) and t.name in ("<partial>", "<partial*>"))
        for t in vals
    )


def _make_singledispatch(it: Host, a: Args, site: Site, kind: str) -> Vals:
    """Allocate a `singledispatch` registry box holding the default function, and return `kind`."""
    box = Box("singledispatch", Alloc.of(site))
    fn = a.get(0)
    if fn and not it.readonly:
        it.state.add_heap(box, fn)
    return frozenset([Builtin(kind, (frozenset([box]),))])


@stub("functools.singledispatch")
def _singledispatch(it: Host, a: Args, site: Site) -> Vals:
    """Model `@functools.singledispatch`: wrap the default function in a dispatch registry."""
    return _make_singledispatch(it, a, site, "<singledispatch>")


@stub("functools.singledispatchmethod")
def _singledispatchmethod(it: Host, a: Args, site: Site) -> Vals:
    """Model `@functools.singledispatchmethod`: descriptor wrapping a method dispatch registry."""
    return _make_singledispatch(it, a, site, "<singledispatchmethod>")


@stub("<singledispatch>", "<singledispatchmethod>")
def _call_singledispatch(it: Host, a: Args, site: Site) -> Vals:
    """Model calling a `singledispatch` function: call every registered implementation."""
    return it.apply(a.pos[0], a.pos[1:], a.star, a.kws, a.kwstar, site)


@stub("<singledispatch>.register", "<singledispatchmethod>.register")
def _singledispatch_register(it: Host, a: Args, _site: Site) -> Vals:
    """Model `fn.register(cls, impl)`, `@fn.register(cls)` and bare `@fn.register`."""
    boxes = _boxes(a.pos[0], "singledispatch")
    if len(a.pos) >= 3 or "func" in a.kws:
        impl = a.get(2, "func")
        if impl and not it.readonly:
            for b in boxes:
                it.state.add_heap(b, impl)
        return impl
    arg = a.get(1)
    if _is_dispatch_fn(arg):
        if not it.readonly:
            for b in boxes:
                it.state.add_heap(b, arg)
        return arg
    return frozenset([Builtin("<singledispatch_deco>", (frozenset(boxes),))])


@stub("<singledispatch_deco>")
def _singledispatch_deco(it: Host, a: Args, _site: Site) -> Vals:
    """Model applying `@fn.register(cls)` to an implementation function: store and return it."""
    impl = a.get(1)
    if impl and not it.readonly:
        for b in _boxes(a.pos[0], "singledispatch"):
            it.state.add_heap(b, impl)
    return impl


@stub("<singledispatch>.dispatch", "<singledispatchmethod>.dispatch")
def _singledispatch_dispatch(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `fn.dispatch(cls)`: return all registered implementations."""
    return a.pos[0]


@stub("itertools.chain.from_iterable")
def _chain_from_iterable(it: Host, a: Args, site: Site) -> Vals:
    """Model `itertools.chain.from_iterable(iterables)`: flatten one level of iterables."""
    return it.iterate(it.iterate(a.get(0), site), site)


@stub("itertools.repeat")
def _repeat(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `itertools.repeat(obj, times)`: yield `obj` itself without iterating it."""
    return a.get(0)


@stub("itertools.compress")
def _compress(it: Host, a: Args, site: Site) -> Vals:
    """Model `itertools.compress(data, selectors)`: iterate both and return elements of `data`."""
    it.iterate(a.get(1, "selectors"), site)
    return it.iterate(a.get(0, "data"), site)


@stub("itertools.zip_longest")
def _zip_longest(it: Host, a: Args, site: Site) -> Vals:
    """Model `itertools.zip_longest(*iterables, fillvalue=None)`: elements plus `fillvalue`."""
    return it.iterate(a.rest(0), site) | a.kw("fillvalue")


@stub("itertools.product")
def _product(it: Host, a: Args, site: Site) -> Vals:
    """Model `itertools.product(*iterables)`: return elements of all input iterables."""
    return it.iterate(a.rest(0), site)


@stub("itertools.permutations", "itertools.combinations",
      "itertools.combinations_with_replacement", "itertools.pairwise",
      "itertools.batched", "itertools.tee")
def _iter_first(it: Host, a: Args, site: Site) -> Vals:
    """Model single-iterable itertools combinators: return the elements of the first argument."""
    return it.iterate(a.get(0), site)


@stub("itertools.count")
def _count(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `itertools.count(...)`: yields numbers, so returns nothing callable."""
    return EMPTY


# ---------------------------------------------------------------- contextlib
@stub("contextlib.contextmanager")
def _contextmanager(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `@contextlib.contextmanager`: wrap a generator function as a context manager factory."""
    return frozenset([Builtin("<contextmanager>", (_shallow(a.get(0)),))])


@stub("<contextmanager>")
def _call_contextmanager(it: Host, a: Args, site: Site) -> Vals:
    """Model calling a `@contextmanager` function: run its body and wrap the yielded value."""
    yielded = it.apply(a.pos[0], a.pos[1:], a.star, a.kws, a.kwstar, site)
    return frozenset([Builtin("<ctx_value>", (_shallow(yielded),))])


@stub("<ctx_value>.__enter__", "<actx_value>.__aenter__",
      "<nullcontext>.__enter__", "<nullcontext>.__aenter__")
def _ctx_enter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model entering a context manager wrapper: return the yielded or supplied value."""
    return a.pos[0]


@stub("<ctx_value>.__exit__", "<actx_value>.__aexit__",
      "<nullcontext>.__exit__", "<nullcontext>.__aexit__")
def _ctx_noop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model exiting a context manager whose cleanup needs no extra calls."""
    return EMPTY


@stub("contextlib.asynccontextmanager")
def _asynccontextmanager(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `@contextlib.asynccontextmanager`: wrap an async generator as an async CM factory."""
    return frozenset([Builtin("<asynccontextmanager>", (_shallow(a.get(0)),))])


@stub("<asynccontextmanager>")
def _call_asynccontextmanager(it: Host, a: Args, site: Site) -> Vals:
    """Model calling an `@asynccontextmanager` function: run its body and wrap the yielded value."""
    yielded = it.apply(a.pos[0], a.pos[1:], a.star, a.kws, a.kwstar, site)
    return frozenset([Builtin("<actx_value>", (_shallow(yielded),))])


@stub("contextlib.closing")
def _closing(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `contextlib.closing(thing)`: return a context manager that calls `thing.close()`."""
    return frozenset([Builtin("<closing>", (_shallow(a.get(0)),))])


@stub("<closing>.__enter__", "<aclosing>.__aenter__")
def _closing_enter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model entering `closing(thing)` / `aclosing(thing)`: return `thing`."""
    return a.pos[0]


@stub("<closing>.__exit__")
def _closing_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model exiting `closing(thing)`: call `thing.close()`."""
    it.invoke_dunder(a.pos[0], "close", [], site)
    return EMPTY


@stub("contextlib.aclosing")
def _aclosing(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `contextlib.aclosing(thing)`: return an async CM that calls `thing.aclose()`."""
    return frozenset([Builtin("<aclosing>", (_shallow(a.get(0)),))])


@stub("<aclosing>.__aexit__")
def _aclosing_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model exiting `aclosing(thing)`: call `thing.aclose()`."""
    it.invoke_dunder(a.pos[0], "aclose", [], site)
    return EMPTY


@stub("contextlib.nullcontext")
def _nullcontext(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `contextlib.nullcontext(enter_result=None)`: yield `enter_result` on enter."""
    return frozenset([Builtin("<nullcontext>", (_shallow(a.get(0, "enter_result")),))])


@stub("contextlib.suppress", "contextlib.redirect_stdout", "contextlib.redirect_stderr",
      "contextlib.chdir", "contextlib.AbstractContextManager",
      "contextlib.AbstractAsyncContextManager")
def _noop_stub(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model stdlib helpers that invoke no user callbacks and return nothing callable."""
    return EMPTY


def _store_callback(it: Host, boxes: Sequence[Box], fn: Vals, pos: Sequence[Vals],
                    star: Vals | None, kws: Mapping[str, Vals], kwstar: Vals | None) -> Vals:
    """Store callback `fn` (wrapped as a partial if arguments are given) into `boxes`."""
    if not fn or it.readonly:
        return fn
    stored = (_make_partial(fn, pos, star, kws, kwstar)
              if (pos or star is not None or kws or kwstar is not None) else fn)
    for b in boxes:
        it.state.add_heap(b, stored)
    return fn


def _push_exit(it: Host, cb_boxes: Sequence[Box], cm_boxes: Sequence[Box],
               obj: Vals, dunder: str) -> Vals:
    """Store `obj` into `cm_boxes` (if it defines `dunder`) and/or `cb_boxes` (as 3-arg exit fn)."""
    if not obj or it.readonly:
        return obj
    cms: set[object] = set()
    cbs: set[object] = set()
    for t in obj:
        if isinstance(t, Instance) and it.state.lookup_class_attr(t.cls, dunder):
            cms.add(t)
        elif isinstance(t, Builtin) and f"{t.name}.{dunder}" in STUBS:
            cms.add(t)
        else:
            cbs.add(t)
    if cms:
        cm_vals = frozenset(t for t in obj if t in cms)
        for b in cm_boxes:
            it.state.add_heap(b, cm_vals)
    if cbs:
        cb_vals = _make_partial(frozenset(t for t in obj if t in cbs),
                                [EMPTY, EMPTY, EMPTY], None, {}, None)
        for b in cb_boxes:
            it.state.add_heap(b, cb_vals)
    return obj


@stub("contextlib.ExitStack")
def _exit_stack(_it: Host, _a: Args, site: Site) -> Vals:
    """Model `contextlib.ExitStack()`: allocate callback and context-manager boxes."""
    alloc = Alloc.of(site)
    return frozenset([Builtin("<exit_stack>", (
        frozenset([Box("exit_cb", alloc)]),
        frozenset([Box("exit_cm", alloc)]),
    ))])


@stub("<exit_stack>.__enter__", "<exit_stack>.pop_all")
def _exit_stack_self(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `ExitStack.__enter__` and `pop_all`: return the stack token itself."""
    return frozenset([Builtin("<exit_stack>", (
        frozenset(_boxes(a.pos[0], "exit_cb")),
        frozenset(_boxes(a.pos[1], "exit_cm")),
    ))])


@stub("<exit_stack>.callback")
def _exit_stack_callback(it: Host, a: Args, _site: Site) -> Vals:
    """Model `stack.callback(fn, *args, **kws)`: register `fn` to run on exit and return `fn`."""
    return _store_callback(it, _boxes(a.pos[0], "exit_cb"), a.get(2),
                           a.pos[3:], a.star, a.kws, a.kwstar)


@stub("<exit_stack>.enter_context")
def _exit_stack_enter_context(it: Host, a: Args, site: Site) -> Vals:
    """Model `stack.enter_context(cm)`: call `cm.__enter__`, remember `cm` for `__exit__`."""
    cm = a.get(2)
    found, entered = it.invoke_dunder(cm, "__enter__", [], site)
    if cm and not it.readonly:
        for b in _boxes(a.pos[1], "exit_cm"):
            it.state.add_heap(b, cm)
    return entered if found else cm


@stub("<exit_stack>.push")
def _exit_stack_push(it: Host, a: Args, _site: Site) -> Vals:
    """Model `stack.push(exit)`: register a context manager or 3-argument exit callback."""
    return _push_exit(it, _boxes(a.pos[0], "exit_cb"), _boxes(a.pos[1], "exit_cm"),
                      a.get(2), "__exit__")


@stub("<exit_stack>.__exit__", "<exit_stack>.close")
def _exit_stack_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model `ExitStack.__exit__` and `close`: invoke all registered callbacks and context exits."""
    call(it, a.pos[0], [], site)
    it.invoke_dunder(a.pos[1], "__exit__", [EMPTY, EMPTY, EMPTY], site)
    return EMPTY


@stub("contextlib.AsyncExitStack")
def _async_exit_stack(_it: Host, _a: Args, site: Site) -> Vals:
    """Model `contextlib.AsyncExitStack()`: allocate sync and async callback/CM boxes."""
    alloc = Alloc.of(site)
    return frozenset([Builtin("<async_exit_stack>", (
        frozenset([Box("exit_cb", alloc)]),
        frozenset([Box("exit_cm", alloc)]),
        frozenset([Box("aexit_cb", alloc)]),
        frozenset([Box("aexit_cm", alloc)]),
    ))])


@stub("<async_exit_stack>.__enter__", "<async_exit_stack>.__aenter__",
      "<async_exit_stack>.pop_all")
def _async_exit_stack_self(_it: Host, a: Args, _site: Site) -> Vals:
    """Model entering or `pop_all` on `AsyncExitStack`: return the stack token itself."""
    return frozenset([Builtin("<async_exit_stack>", (
        frozenset(_boxes(a.pos[0], "exit_cb")),
        frozenset(_boxes(a.pos[1], "exit_cm")),
        frozenset(_boxes(a.pos[2], "aexit_cb")),
        frozenset(_boxes(a.pos[3], "aexit_cm")),
    ))])


@stub("<async_exit_stack>.callback")
def _aexit_stack_callback(it: Host, a: Args, _site: Site) -> Vals:
    """Model `AsyncExitStack.callback(fn, *args, **kws)`: register a sync exit callback."""
    return _store_callback(it, _boxes(a.pos[0], "exit_cb"), a.get(4),
                           a.pos[5:], a.star, a.kws, a.kwstar)


@stub("<async_exit_stack>.push_async_callback")
def _aexit_stack_async_callback(it: Host, a: Args, _site: Site) -> Vals:
    """Model `AsyncExitStack.push_async_callback(fn, *args, **kws)`: register an async callback."""
    return _store_callback(it, _boxes(a.pos[2], "aexit_cb"), a.get(4),
                           a.pos[5:], a.star, a.kws, a.kwstar)


@stub("<async_exit_stack>.enter_context")
def _aexit_stack_enter_context(it: Host, a: Args, site: Site) -> Vals:
    """Model `AsyncExitStack.enter_context(cm)`: call `__enter__` and remember `cm`."""
    cm = a.get(4)
    found, entered = it.invoke_dunder(cm, "__enter__", [], site)
    if cm and not it.readonly:
        for b in _boxes(a.pos[1], "exit_cm"):
            it.state.add_heap(b, cm)
    return entered if found else cm


@stub("<async_exit_stack>.enter_async_context")
def _aexit_stack_enter_async_context(it: Host, a: Args, site: Site) -> Vals:
    """Model `AsyncExitStack.enter_async_context(cm)`: call `__aenter__` and remember `cm`."""
    cm = a.get(4)
    found, entered = it.invoke_dunder(cm, "__aenter__", [], site)
    if cm and not it.readonly:
        for b in _boxes(a.pos[3], "aexit_cm"):
            it.state.add_heap(b, cm)
    return entered if found else cm


@stub("<async_exit_stack>.push")
def _aexit_stack_push(it: Host, a: Args, _site: Site) -> Vals:
    """Model `AsyncExitStack.push(exit)`: register a sync context manager or exit callback."""
    return _push_exit(it, _boxes(a.pos[0], "exit_cb"), _boxes(a.pos[1], "exit_cm"),
                      a.get(4), "__exit__")


@stub("<async_exit_stack>.push_async_exit")
def _aexit_stack_push_async(it: Host, a: Args, _site: Site) -> Vals:
    """Model `AsyncExitStack.push_async_exit(exit)`: register an async CM or exit callback."""
    return _push_exit(it, _boxes(a.pos[2], "aexit_cb"), _boxes(a.pos[3], "aexit_cm"),
                      a.get(4), "__aexit__")


@stub("<async_exit_stack>.__exit__", "<async_exit_stack>.close")
def _aexit_stack_sync_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model synchronous exit on `AsyncExitStack`: invoke sync callbacks and context exits."""
    call(it, a.pos[0], [], site)
    it.invoke_dunder(a.pos[1], "__exit__", [EMPTY, EMPTY, EMPTY], site)
    return EMPTY


@stub("<async_exit_stack>.__aexit__", "<async_exit_stack>.aclose")
def _aexit_stack_async_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model async exit on `AsyncExitStack`: invoke all sync and async callbacks and exits."""
    call(it, a.pos[0] | a.pos[2], [], site)
    it.invoke_dunder(a.pos[1], "__exit__", [EMPTY, EMPTY, EMPTY], site)
    it.invoke_dunder(a.pos[3], "__aexit__", [EMPTY, EMPTY, EMPTY], site)
    return EMPTY


# ---------------------------------------------------------------- collections
@stub("collections.defaultdict")
def _defaultdict(it: Host, a: Args, site: Site) -> Vals:
    """Model `collections.defaultdict(default_factory=None, ...)`: dict box plus factory hook."""
    box = Box("dict", Alloc.of(site))
    factory = _shallow(a.pos[0]) if a.pos else EMPTY
    elems = it.iterate(a.rest(1), site) | union(a.kws.values()) | (a.kwstar or EMPTY)
    if factory:
        return elems | frozenset([box, Builtin("<defaultdict>", (factory, frozenset([box])))])
    return elems | frozenset([box])


@stub("<defaultdict>.__getitem__")
def _defaultdict_getitem(it: Host, a: Args, site: Site) -> Vals:
    """Model `d[k]` on a `defaultdict`: call `default_factory()`, store the result, return it."""
    res = call(it, a.pos[0], [], site)
    if res and not it.readonly:
        for b in _boxes(a.pos[1], "dict"):
            it.state.add_heap(b, res)
    return res


@stub("<defaultdict>.default_factory")
def _defaultdict_factory(it: Host, a: Args, site: Site) -> Vals:
    """Model calling `d.default_factory(...)`: invoke the factory stored on the `defaultdict`."""
    return it.apply(a.pos[0], a.pos[2:], a.star, a.kws, a.kwstar, site)


@stub("collections.deque")
def _deque(it: Host, a: Args, site: Site) -> Vals:
    """Model `collections.deque(iterable=(), maxlen=None)`: elements plus a mutable deque box."""
    return it.iterate(a.get(0), site) | frozenset([Box("deque", Alloc.of(site))])


@stub("collections.OrderedDict", "collections.Counter", "collections.UserDict")
def _dict_like(it: Host, a: Args, site: Site) -> Vals:
    """Model dict-like classes in `collections`: elements plus a mutable dict box."""
    return it.iterate(a.all(), site) | frozenset([Box("dict", Alloc.of(site))])


@stub("collections.UserList")
def _user_list(it: Host, a: Args, site: Site) -> Vals:
    """Model `collections.UserList`: elements plus a mutable list box."""
    return it.iterate(a.all(), site) | frozenset([Box("list", Alloc.of(site))])


@stub("collections.ChainMap")
def _chainmap(_it: Host, a: Args, site: Site) -> Vals:
    """Model `collections.ChainMap(*maps)`: union of all input maps plus a dict box."""
    return a.rest(0) | frozenset([Box("dict", Alloc.of(site))])


@stub("collections.namedtuple")
def _namedtuple(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `collections.namedtuple(...)`: return a tuple-like constructor token."""
    return frozenset([Builtin("<namedtuple_cls>")])


@stub("<namedtuple_cls>")
def _call_namedtuple(_it: Host, a: Args, _site: Site) -> Vals:
    """Model instantiating a `namedtuple` class: collapse its field values like a tuple."""
    return a.all()


@stub("<namedtuple_cls>._make")
def _namedtuple_make(it: Host, a: Args, site: Site) -> Vals:
    """Model `NT._make(iterable)`: return the iterable's elements like a tuple."""
    return it.iterate(a.get(0), site)


# ---------------------------------------------------------------- threading
@stub("threading.Thread")
def _thread(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `threading.Thread(group=None, target=None, name=None, args=(), kwargs=None)`."""
    target = _shallow(a.get(1, "target"))
    targs = _shallow(a.get(3, "args"))
    tkws = _shallow(a.get(4, "kwargs"))
    has_args = (len(a.pos) > 3 or "args" in a.kws or "kwargs" in a.kws
                or a.star is not None or a.kwstar is not None)
    kind = "<thread_args>" if has_args else "<thread>"
    return frozenset([Builtin(kind, (target, targs, tkws))])


@stub("threading.Timer")
def _timer(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `threading.Timer(interval, function, args=None, kwargs=None)`."""
    target = _shallow(a.get(1, "function"))
    targs = _shallow(a.get(2, "args"))
    tkws = _shallow(a.get(3, "kwargs"))
    has_args = (len(a.pos) > 2 or "args" in a.kws or "kwargs" in a.kws
                or a.star is not None or a.kwstar is not None)
    kind = "<thread_args>" if has_args else "<thread>"
    return frozenset([Builtin(kind, (target, targs, tkws))])


@stub("<thread>.start", "<thread>.run")
def _thread_start(it: Host, a: Args, site: Site) -> Vals:
    """Model `t.start()` / `t.run()` when no `args`/`kwargs` were passed: call `target()`."""
    it.apply(a.pos[0], [], None, {}, None, site)
    return EMPTY


@stub("<thread_args>.start", "<thread_args>.run")
def _thread_start_args(it: Host, a: Args, site: Site) -> Vals:
    """Model `t.start()` / `t.run()` with `args`/`kwargs`: call `target(*args, **kwargs)`."""
    it.apply(a.pos[0], [], a.pos[1], {}, a.pos[2], site)
    return EMPTY


@stub("<thread>.join", "<thread_args>.join", "<thread>.cancel", "<thread_args>.cancel")
def _thread_noop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `Thread.join` and `Timer.cancel`: no callbacks."""
    return EMPTY


@stub("threading.Barrier")
def _barrier(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `threading.Barrier(parties, action=None)`: remember the barrier action."""
    return frozenset([Builtin("<barrier>", (_shallow(a.get(1, "action")),))])


@stub("<barrier>.wait")
def _barrier_wait(it: Host, a: Args, site: Site) -> Vals:
    """Model `barrier.wait()`: invoke the barrier's action callback if any."""
    call(it, a.pos[0], [], site)
    return EMPTY


@stub("threading.Lock", "threading.RLock", "threading.Semaphore",
      "threading.BoundedSemaphore", "threading.Event", "threading.Condition",
      "threading.local")
def _threading_sync(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model threading synchronisation primitives: no user callbacks."""
    return EMPTY


# ---------------------------------------------------------------- futures / concurrent.futures
def _new_future(it: Host, site: Site, result: Vals = EMPTY) -> Vals:
    """Create a `<future>` token backed by result and callback boxes at `site`."""
    alloc = Alloc.of(site)
    res_box = Box("future_res", alloc)
    cb_box = Box("future_cb", alloc)
    if result and not it.readonly:
        it.state.add_heap(res_box, result)
    return frozenset([Builtin("<future>", (frozenset([res_box]), frozenset([cb_box])))])


@stub("concurrent.futures.Future", "asyncio.Future")
def _future(it: Host, _a: Args, site: Site) -> Vals:
    """Model `Future()`: return an empty `<future>` token."""
    return _new_future(it, site)


@stub("<future>.result", "<future>.__await__")
def _future_result(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `fut.result()` and `await fut`: return the values stored as the future's result."""
    return a.pos[0]


@stub("<future>.set_result")
def _future_set_result(it: Host, a: Args, site: Site) -> Vals:
    """Model `fut.set_result(val)`: store `val` and invoke any registered done callbacks."""
    res_boxes = _boxes(a.pos[0], "future_res")
    cb_boxes = _boxes(a.pos[1], "future_cb")
    val = a.get(2)
    if val and not it.readonly:
        for b in res_boxes:
            it.state.add_heap(b, val)
    fut = frozenset([Builtin("<future>", (frozenset(res_boxes), frozenset(cb_boxes)))])
    call(it, a.pos[1], [fut], site)
    return EMPTY


@stub("<future>.add_done_callback")
def _future_add_done_callback(it: Host, a: Args, site: Site) -> Vals:
    """Model `fut.add_done_callback(cb)`: store `cb` and invoke `cb(fut)`."""
    res_boxes = _boxes(a.pos[0], "future_res")
    cb_boxes = _boxes(a.pos[1], "future_cb")
    cb = a.get(2)
    if cb and not it.readonly:
        for b in cb_boxes:
            it.state.add_heap(b, cb)
    fut = frozenset([Builtin("<future>", (frozenset(res_boxes), frozenset(cb_boxes)))])
    call(it, cb, [fut], site)
    return EMPTY


@stub("<future>.exception", "<future>.cancel", "<future>.cancelled",
      "<future>.done", "<future>.running")
def _future_noop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model status/cancellation methods of a `Future`: no user callbacks."""
    return EMPTY


@stub("concurrent.futures.ThreadPoolExecutor", "concurrent.futures.ProcessPoolExecutor")
def _executor(it: Host, a: Args, site: Site) -> Vals:
    """Model `ThreadPoolExecutor` / `ProcessPoolExecutor`: run `initializer` if given."""
    init = a.kw("initializer")
    if init:
        has_initargs = "initargs" in a.kws or a.kwstar is not None
        it.apply(init, [], a.kw("initargs") if has_initargs else None, {}, None, site)
    return frozenset([Builtin("<executor>")])


@stub("<executor>.__enter__")
def _executor_enter(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model entering an executor context: return the executor token itself."""
    return frozenset([Builtin("<executor>")])


@stub("<executor>.__exit__", "<executor>.shutdown")
def _executor_exit(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model exiting or shutting down an executor: no extra callbacks."""
    return EMPTY


@stub("<executor>.submit")
def _executor_submit(it: Host, a: Args, site: Site) -> Vals:
    """Model `ex.submit(fn, *args, **kws)`: call `fn` and wrap the result in a `<future>`."""
    res = it.apply(a.get(0), a.pos[1:], a.star, a.kws, a.kwstar, site)
    return _new_future(it, site, res)


@stub("<executor>.map")
def _executor_map(it: Host, a: Args, site: Site) -> Vals:
    """Model `ex.map(fn, *iterables, timeout=..., chunksize=...)`: call `fn` on each iterable."""
    f = a.get(0)
    if a.star is not None:
        return call(it, f, [], site, star=a.rest(1))
    return call(it, f, a.pos[1:], site)


@stub("concurrent.futures.as_completed", "concurrent.futures.wait",
      "asyncio.as_completed", "asyncio.wait")
def _futures_iter(it: Host, a: Args, site: Site) -> Vals:
    """Model `as_completed(fs)` and `wait(fs)`: return the futures in `fs`."""
    return it.iterate(a.get(0), site)


# ---------------------------------------------------------------- asyncio
@stub("asyncio.run", "asyncio.wait_for", "asyncio.shield")
def _asyncio_passthrough(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `asyncio.run`, `wait_for` and `shield`: return the coroutine/awaitable's value."""
    return a.get(0)


@stub("asyncio.create_task", "asyncio.ensure_future",
      "<event_loop>.create_task", "<task_group>.create_task")
def _asyncio_create_task(it: Host, a: Args, site: Site) -> Vals:
    """Model `create_task(coro)` / `ensure_future(coro)`: wrap the result in a `<future>`."""
    return _new_future(it, site, a.get(0))


@stub("asyncio.gather")
def _asyncio_gather(_it: Host, a: Args, site: Site) -> Vals:
    """Model `asyncio.gather(*aws)`: return all awaitable results plus a list box."""
    return a.rest(0) | frozenset([Box("list", Alloc.of(site))])


@stub("asyncio.to_thread")
def _asyncio_to_thread(it: Host, a: Args, site: Site) -> Vals:
    """Model `asyncio.to_thread(fn, *args, **kws)`: call `fn` with the given arguments."""
    return it.apply(a.get(0), a.pos[1:], a.star, a.kws, a.kwstar, site)


@stub("asyncio.sleep")
def _asyncio_sleep(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `asyncio.sleep(delay, result=None)`: return `result`."""
    return a.get(1, "result")


@stub("asyncio.get_running_loop", "asyncio.get_event_loop", "asyncio.new_event_loop")
def _asyncio_loop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model obtaining an event loop: return an `<event_loop>` token."""
    return frozenset([Builtin("<event_loop>")])


@stub("<event_loop>.call_soon", "<event_loop>.call_soon_threadsafe")
def _loop_call_soon(it: Host, a: Args, site: Site) -> Vals:
    """Model `loop.call_soon(cb, *args)` / `call_soon_threadsafe`: invoke `cb(*args)`."""
    it.apply(a.get(0), a.pos[1:], a.star, {}, None, site)
    return EMPTY


@stub("<event_loop>.call_later", "<event_loop>.call_at")
def _loop_call_later(it: Host, a: Args, site: Site) -> Vals:
    """Model `loop.call_later(delay, cb, *args)` / `call_at`: invoke `cb(*args)`."""
    it.apply(a.get(1), a.pos[2:], a.star, {}, None, site)
    return EMPTY


@stub("<event_loop>.run_in_executor")
def _loop_run_in_executor(it: Host, a: Args, site: Site) -> Vals:
    """Model `loop.run_in_executor(executor, fn, *args)`: call `fn(*args)` and return a future."""
    res = it.apply(a.get(1), a.pos[2:], a.star, {}, None, site)
    return _new_future(it, site, res)


@stub("<event_loop>.create_future")
def _loop_create_future(it: Host, _a: Args, site: Site) -> Vals:
    """Model `loop.create_future()`: return an empty `<future>` token."""
    return _new_future(it, site)


@stub("<event_loop>.run_until_complete")
def _loop_run_until_complete(it: Host, a: Args, site: Site) -> Vals:
    """Model `loop.run_until_complete(future)`: await `future` if awaitable, else return it."""
    v = a.get(0)
    found, out = it.invoke_dunder(v, "__await__", [], site)
    return out if found else v


@stub("<event_loop>.close", "<event_loop>.stop", "<event_loop>.run_forever")
def _loop_noop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model loop lifecycle methods that invoke no user callbacks."""
    return EMPTY


@stub("asyncio.TaskGroup")
def _task_group(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `asyncio.TaskGroup()`: return an async context manager for creating tasks."""
    return frozenset([Builtin("<task_group>")])


@stub("<task_group>.__aenter__")
def _task_group_enter(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model entering an `asyncio.TaskGroup`: return the task group token itself."""
    return frozenset([Builtin("<task_group>")])


@stub("<task_group>.__aexit__")
def _task_group_exit(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model exiting an `asyncio.TaskGroup`: no extra callbacks."""
    return EMPTY


@stub("asyncio.Queue", "asyncio.LifoQueue", "asyncio.PriorityQueue")
def _async_queue(_it: Host, _a: Args, site: Site) -> Vals:
    """Model `asyncio.Queue` variants: allocate a heap box for queued items."""
    return frozenset([Builtin("<async_queue>", (frozenset([Box("queue", Alloc.of(site))]),))])


@stub("<async_queue>.put", "<async_queue>.put_nowait")
def _async_queue_put(it: Host, a: Args, _site: Site) -> Vals:
    """Model `q.put(item)` / `q.put_nowait(item)`: store `item` into the queue's box."""
    item = a.get(1)
    if item and not it.readonly:
        for b in _boxes(a.pos[0], "queue"):
            it.state.add_heap(b, item)
    return EMPTY


@stub("<async_queue>.get", "<async_queue>.get_nowait")
def _async_queue_get(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `q.get()` / `q.get_nowait()`: return the items stored in the queue."""
    return a.pos[0]


@stub("asyncio.Lock", "asyncio.Event", "asyncio.Condition",
      "asyncio.Semaphore", "asyncio.BoundedSemaphore", "asyncio.Barrier")
def _asyncio_sync(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model asyncio synchronisation primitives: no user callbacks."""
    return EMPTY


BUILTIN_NAMES = frozenset(n for n in STUBS if "." not in n and not n.startswith("<"))
