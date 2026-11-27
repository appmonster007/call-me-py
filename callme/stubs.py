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
    Instance,
    Property,
    Site,
    StaticMethod,
    Super,
    Vals,
)

STUB_MODULES = frozenset({"dataclasses", "functools", "itertools"})
METHOD_STUBS = {  # attribute name -> stub (receiver is bound first)
    "sort": "list.sort", "get": "dict.get",
    **{m: "<elements>" for m in ("values", "items", "keys", "copy", "pop", "popitem")},
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
    """Model `iter(x)`: return what `x.__iter__` returns, or `x` itself for non-instances."""
    v = a.get(0)
    found, out = it.invoke_dunder(v, "__iter__", [], site)
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


@stub("<dataclass_init>")
def _dataclass_init(it: Host, a: Args, site: Site) -> Vals:
    """Model a generated dataclass `__init__`: call the instance's `__post_init__`, if any.

    The method is bound as Python would bind it: to the class for a classmethod, unbound for
    a staticmethod, and to the instance otherwise. Field values are not tracked.
    """
    for obj in a.get(0):
        if isinstance(obj, Instance):
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
    """Drop tokens nested `MAX_PARTIAL_DEPTH` deep from `v`, so partial chains stay finite."""
    return frozenset(t for t in v if t.depth < MAX_PARTIAL_DEPTH)


@stub("functools.partial")
def _partial(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `functools.partial(f, *args, **kws)`: return a `<partial>` token per function.

    The token binds `f` and the arguments; a partial of a partial is flattened into one. With
    more than `MAX_PARTIAL_ARGS` bound arguments, or `*` or `**` at construction, the order of
    arguments is lost: the token is widened to `<partial*>`, which passes every bound value to
    `f` through `*args`. Too deep argument tokens are dropped (`_shallow`).
    """
    out: set[Builtin] = set()
    rest = [_shallow(v) for v in a.pos[1:]]
    kws = {k: _shallow(v) for k, v in a.kws.items()}
    extra = _shallow((a.star or EMPTY) | (a.kwstar or EMPTY))
    for t in a.get(0):
        if isinstance(t, Builtin) and t.name in ("<partial>", "<partial*>"):
            f, args, kw = t.bound[0], [*t.bound[1:], *rest], dict(t.bound_kw)
            for k, v in kws.items():
                kw[k] = kw.get(k, EMPTY) | v
            widened = t.name == "<partial*>"
        else:
            f, args, kw, widened = frozenset([t]), list(rest), dict(kws), False
        if widened or extra or len(args) > MAX_PARTIAL_ARGS:
            everything = union(args) | union(kw.values()) | extra
            out.add(Builtin("<partial*>", (f, everything)))
        else:
            out.add(Builtin("<partial>", (f, *args), tuple(sorted(kw.items(), key=lambda p: p[0]))))
    return frozenset(out)


@stub("<partial>")
def _call_partial(it: Host, a: Args, site: Site) -> Vals:
    """Call a `<partial>` token: its function gets the bound arguments, then the call's own."""
    # a.pos = [f, *bound args, *call args]; bound keywords are already merged into a.kws
    return it.apply(a.pos[0], a.pos[1:], a.star, a.kws, a.kwstar, site)


@stub("<partial*>")
def _call_partial_widened(it: Host, a: Args, site: Site) -> Vals:
    """Call a `<partial*>` token: every bound and call argument goes to the function as `*args`."""
    return it.apply(a.pos[0], [], a.rest(1), a.kws, a.kwstar, site)


BUILTIN_NAMES = frozenset(n for n in STUBS if "." not in n and not n.startswith("<"))
