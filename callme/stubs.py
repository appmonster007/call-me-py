"""Stub repository: how builtin / stdlib callables use their arguments.

The analyser never sees the C source of `map`, `sorted`, `functools.reduce`, ...
Each stub here replays, on abstract values, the calls such a function makes and
the value it returns. Callbacks are invoked through `Interp.apply` at the HOF's
own call site, so the edge goes from the Python function that called the HOF to
the callback (matching a traced run, where C frames are invisible).

Abstract values are sets of tokens and containers are collapsed to the union of
their elements, so "the elements of iterable `xs`" is just the value of `xs`.

Adding a stub: write `def _x(it, a, site) -> Vals` and register it with
`@stub("name", "module.name", ...)`. Names without a dot are builtins (looked up
when no user binding shadows them); dotted names are reached via
`import module` / `from module import name`; `<...>` names are internal.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .model import (
    EMPTY,
    BoundMethod,
    Builtin,
    ClassMethod,
    ClassObj,
    Instance,
    Property,
    Site,
    StaticMethod,
    Super,
    Vals,
)

if TYPE_CHECKING:
    from .transfer import Interp

STUB_MODULES = frozenset({"dataclasses", "functools", "itertools"})
METHOD_STUBS = {"sort": "list.sort", "get": "dict.get"}  # attribute name -> stub (receiver is bound first)
MAX_PARTIAL_ARGS = 4  # bound arguments before a partial is widened to <partial*>
MAX_PARTIAL_DEPTH = 3  # nesting of tokens inside a partial's arguments


@dataclass
class Args:
    """Abstract arguments of one call (bound arguments already prepended)."""

    pos: list[Vals]
    star: Vals | None
    kws: dict[str, Vals]
    kwstar: Vals | None

    def get(self, i: int, kw: str | None = None) -> Vals:
        """Positional `i` (or keyword `kw`); falls back to whatever */** may supply."""
        if i < len(self.pos):
            return self.pos[i]
        if kw is not None and kw in self.kws:
            return self.kws[kw]
        return (self.star or EMPTY) | (self.kwstar or EMPTY)

    def kw(self, name: str) -> Vals:
        return self.kws.get(name, EMPTY) | (self.kwstar or EMPTY)

    def rest(self, i: int) -> Vals:
        return union(self.pos[i:]) | (self.star or EMPTY)

    def all(self) -> Vals:
        return self.rest(0) | union(self.kws.values()) | (self.kwstar or EMPTY)


StubFn = Callable[["Interp", Args, Site], Vals]
STUBS: dict[str, StubFn] = {}


def stub(*names: str) -> Callable[[StubFn], StubFn]:
    def reg(fn: StubFn) -> StubFn:
        for n in names:
            STUBS[n] = fn
        return fn
    return reg


def union(vs: Iterable[Vals]) -> Vals:
    out = EMPTY
    for v in vs:
        out |= v
    return out


def call(it: Interp, f: Vals, args: list[Vals], site: Site, star: Vals | None = None) -> Vals:
    """Invoke callback(s) `f` from the current node at `site`; returns their result."""
    if not f:
        return EMPTY
    return it.apply(f, args, star, {}, None, site)


def fold(it: Interp, f: Vals, acc: Vals, elems: Vals, site: Site) -> Vals:
    """acc = f(acc, e) repeated: iterate the accumulator to a local fixpoint."""
    while True:
        r = call(it, f, [acc, elems], site)
        if r <= acc:
            return acc
        acc |= r


# ---------------------------------------------------------------- builtins
@stub("map")
def _map(it: Interp, a: Args, site: Site) -> Vals:
    f = a.get(0)
    if a.star is not None:  # unknown number of iterables
        return call(it, f, [], site, star=a.rest(1))
    return call(it, f, a.pos[1:], site)


@stub("filter", "itertools.filterfalse", "itertools.takewhile", "itertools.dropwhile")
def _filter(it: Interp, a: Args, site: Site) -> Vals:
    elems = a.get(1)
    call(it, a.get(0), [elems], site)
    return elems


@stub("sorted")
def _sorted(it: Interp, a: Args, site: Site) -> Vals:
    elems = a.get(0)
    key = a.kw("key")
    if key:
        keys = call(it, key, [elems], site)
        it.invoke_dunder(keys, "__lt__", [keys], site)
    else:
        it.invoke_dunder(elems, "__lt__", [elems], site)
    return elems


@stub("min", "max")
def _minmax(it: Interp, a: Args, site: Site) -> Vals:
    elems = a.pos[0] if len(a.pos) == 1 and a.star is None else a.rest(0)
    key = a.kw("key")
    if key:
        keys = call(it, key, [elems], site)
        it.invoke_dunder(keys, "__lt__", [keys], site)
    else:
        it.invoke_dunder(elems, "__lt__", [elems], site)
    return elems | a.kw("default")


@stub("list.sort")
def _list_sort(it: Interp, a: Args, site: Site) -> Vals:
    elems = a.get(0)
    key = a.kw("key")
    if key:
        keys = call(it, key, [elems], site)
        it.invoke_dunder(keys, "__lt__", [keys], site)
    else:
        it.invoke_dunder(elems, "__lt__", [elems], site)
    return EMPTY


@stub("dict.get")
def _dict_get(it: Interp, a: Args, site: Site) -> Vals:
    return a.get(0) | a.get(2, "default")


@stub("len")
def _len(it: Interp, a: Args, site: Site) -> Vals:
    it.invoke_dunder(a.get(0), "__len__", [], site)
    return EMPTY


@stub("str")
def _str(it: Interp, a: Args, site: Site) -> Vals:
    v = a.get(0)
    found, _ = it.invoke_dunder(v, "__str__", [], site)
    if not found:
        it.invoke_dunder(v, "__repr__", [], site)
    return EMPTY


@stub("repr")
def _repr(it: Interp, a: Args, site: Site) -> Vals:
    it.invoke_dunder(a.get(0), "__repr__", [], site)
    return EMPTY


@stub("bool")
def _bool(it: Interp, a: Args, site: Site) -> Vals:
    v = a.get(0)
    found, _ = it.invoke_dunder(v, "__bool__", [], site)
    if not found:
        it.invoke_dunder(v, "__len__", [], site)
    return EMPTY


@stub("hash")
def _hash(it: Interp, a: Args, site: Site) -> Vals:
    it.invoke_dunder(a.get(0), "__hash__", [], site)
    return EMPTY


@stub("iter")
def _iter(it: Interp, a: Args, site: Site) -> Vals:
    v = a.get(0)
    found, out = it.invoke_dunder(v, "__iter__", [], site)
    return out if found else v


@stub("next")
def _next(it: Interp, a: Args, site: Site) -> Vals:
    v = a.get(0)
    default = a.get(1)
    found, out = it.invoke_dunder(v, "__next__", [], site)
    return (out if found else v) | default


@stub("reversed")
def _reversed(it: Interp, a: Args, site: Site) -> Vals:
    v = a.get(0)
    found, out = it.invoke_dunder(v, "__reversed__", [], site)
    if found:
        _, elem = it.invoke_dunder(out, "__next__", [], site)
        return elem or out
    return it.iterate(v, site)


@stub("classmethod")
def _classmethod(it: Interp, a: Args, site: Site) -> Vals:
    return frozenset(
        t if isinstance(t, ClassMethod) else ClassMethod(t)
        for t in a.get(0) if isinstance(t, ClassMethod) or t.depth <= MAX_PARTIAL_DEPTH
    )


@stub("staticmethod")
def _staticmethod(it: Interp, a: Args, site: Site) -> Vals:
    return frozenset(
        t if isinstance(t, StaticMethod) else StaticMethod(t)
        for t in a.get(0) if isinstance(t, StaticMethod) or t.depth <= MAX_PARTIAL_DEPTH
    )


@stub("super")
def _super(it: Interp, a: Args, site: Site) -> Vals:
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
    return frozenset(t for t in v if not isinstance(t, Property) and t.depth <= MAX_PARTIAL_DEPTH)


@stub("property")
def _property(it: Interp, a: Args, site: Site) -> Vals:
    return frozenset([Property(
        _prop_fn(a.get(0, "fget")),
        _prop_fn(a.get(1, "fset")),
        _prop_fn(a.get(2, "fdel")),
    )])


@stub("<property_getter>")
def _property_getter(it: Interp, a: Args, site: Site) -> Vals:
    fn = _prop_fn(a.get(1))
    return frozenset(Property(fn, p.fset, p.fdel) for p in a.get(0) if isinstance(p, Property))


@stub("<property_setter>")
def _property_setter(it: Interp, a: Args, site: Site) -> Vals:
    fn = _prop_fn(a.get(1))
    return frozenset(Property(p.fget, fn, p.fdel) for p in a.get(0) if isinstance(p, Property))


@stub("<property_deleter>")
def _property_deleter(it: Interp, a: Args, site: Site) -> Vals:
    fn = _prop_fn(a.get(1))
    return frozenset(Property(p.fget, p.fset, fn) for p in a.get(0) if isinstance(p, Property))


@stub("object")
def _object(it: Interp, a: Args, site: Site) -> Vals:
    return EMPTY


@stub("type")
def _type(it: Interp, a: Args, site: Site) -> Vals:
    return EMPTY


@stub("type.__call__")
def _type_call(it: Interp, a: Args, site: Site) -> Vals:
    out = EMPTY
    for tok in a.get(0):
        if isinstance(tok, ClassObj):
            out |= it.instantiate(tok, a.pos[1:], a.star, a.kws, a.kwstar, site)
    return out


@stub("object.__new__")
def _object_new(it: Interp, a: Args, site: Site) -> Vals:
    return frozenset(Instance(t.cls) for t in a.get(0) if isinstance(t, ClassObj))


@stub("object.__setattr__")
def _object_setattr(it: Interp, a: Args, site: Site) -> Vals:
    """Bypass custom __setattr__: dispatch to data descriptors on the target's MRO."""
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


def _apply_dataclass(it: Interp, cls_vals: Vals) -> Vals:
    for t in cls_vals:
        if isinstance(t, ClassObj) and not it.readonly:
            if "__init__" not in it.state.class_attrs.get(t.cls, {}):
                it.state.add_class_attr(t.cls, "__init__", frozenset([Builtin("<dataclass_init>")]))
    return cls_vals


@stub("dataclasses.dataclass")
def _dataclass(it: Interp, a: Args, site: Site) -> Vals:
    cls = a.pos[0] if a.pos else EMPTY
    if any(isinstance(t, ClassObj) for t in cls):
        return _apply_dataclass(it, cls)
    return frozenset([Builtin("<dataclass>")])


@stub("<dataclass>")
def _dataclass_deco(it: Interp, a: Args, site: Site) -> Vals:
    return _apply_dataclass(it, a.get(0))


@stub("<dataclass_init>")
def _dataclass_init(it: Interp, a: Args, site: Site) -> Vals:
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


@stub("list", "tuple", "set", "frozenset", "dict", "enumerate", "zip",
      "itertools.chain", "itertools.cycle", "itertools.islice")
def _passthrough(it: Interp, a: Args, site: Site) -> Vals:
    return it.iterate(a.all(), site)


# ---------------------------------------------------------------- functools / itertools
@stub("functools.reduce")
def _reduce(it: Interp, a: Args, site: Site) -> Vals:
    elems = a.get(1)
    return fold(it, a.get(0), elems | a.get(2, "initial"), elems, site)


@stub("itertools.accumulate")
def _accumulate(it: Interp, a: Args, site: Site) -> Vals:
    elems = a.get(0)
    return fold(it, a.get(1, "func"), elems | a.kw("initial"), elems, site)


@stub("itertools.starmap")
def _starmap(it: Interp, a: Args, site: Site) -> Vals:
    return call(it, a.get(0), [], site, star=a.get(1))


@stub("itertools.groupby")
def _groupby(it: Interp, a: Args, site: Site) -> Vals:
    elems = a.get(0)
    call(it, a.get(1, "key"), [elems], site)
    return elems


IDENTITY = Builtin("<identity>")


@stub("<identity>")
def _identity(it: Interp, a: Args, site: Site) -> Vals:
    return a.get(0)


@stub("functools.cache", "functools.lru_cache")
def _cache(it: Interp, a: Args, site: Site) -> Vals:
    """cache(f) / lru_cache(f) -> f;  lru_cache(maxsize=...) -> identity decorator."""
    f = a.pos[0] if a.pos else EMPTY
    return f if f else frozenset([IDENTITY])


@stub("functools.wraps")
def _wraps(it: Interp, a: Args, site: Site) -> Vals:
    return frozenset([IDENTITY])


@stub("functools.update_wrapper")
def _update_wrapper(it: Interp, a: Args, site: Site) -> Vals:
    return a.get(0)


def _shallow(v: Vals) -> Vals:
    """Drop over-nested builtin tokens so partial-of-partial chains stay finite."""
    return frozenset(t for t in v if t.depth < MAX_PARTIAL_DEPTH)


@stub("functools.partial")
def _partial(it: Interp, a: Args, site: Site) -> Vals:
    """partial(f, *args, **kws) -> <partial> token holding f and the bound args.

    A partial of a partial is flattened. Too many bound args (or */** at the
    construction) widen it to <partial*>, which hands every argument to f via *.
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
def _call_partial(it: Interp, a: Args, site: Site) -> Vals:
    # a.pos = [f, *bound args, *call args]; bound keywords are already merged into a.kws
    return it.apply(a.pos[0], a.pos[1:], a.star, a.kws, a.kwstar, site)


@stub("<partial*>")
def _call_partial_widened(it: Interp, a: Args, site: Site) -> Vals:
    return it.apply(a.pos[0], [], a.rest(1), a.kws, a.kwstar, site)


BUILTIN_NAMES = frozenset(n for n in STUBS if "." not in n and not n.startswith("<"))
