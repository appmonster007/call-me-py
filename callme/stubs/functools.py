"""Stubs for `functools`: reduce, cache, wraps, partial, partialmethod, cached_property,
total_ordering, cmp_to_key and singledispatch."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..model import EMPTY, Alloc, BoundMethod, Box, Builtin, Closure, Property, Site, Vals
from .objects import property_fn
from .registry import MAX_PARTIAL_ARGS, Args, Host, boxes_of, call, fold, shallow, stub, union


@stub("functools.reduce")
def _reduce(it: Host, a: Args, site: Site) -> Vals:
    """Model `functools.reduce(f, xs, initial)`: fold `f` over the elements (see `fold`)."""
    elems = a.get(1)
    return fold(it, a.get(0), elems | a.get(2, "initial"), elems, site)


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


def make_partial(fn_vals: Vals, pos: Sequence[Vals], star: Vals | None,
                  kws: Mapping[str, Vals], kwstar: Vals | None, *,
                  name: str = "<partial>", wide_name: str = "<partial*>") -> Vals:
    """Build bounded partial tokens (`name` or widened `wide_name`) for each callable in `fn_vals`.

    A partial of a partial is flattened into one. With more than `MAX_PARTIAL_ARGS` bound
    arguments, or `*` or `**` at construction, the order of arguments is lost and the token is
    widened to `wide_name`. Too deep argument tokens are dropped (`shallow`).
    """
    out: set[Builtin] = set()
    rest = [shallow(v) for v in pos]
    kw_map = {k: shallow(v) for k, v in kws.items()}
    extra = shallow((star or EMPTY) | (kwstar or EMPTY))
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
    return make_partial(a.get(0), a.pos[1:], a.star, a.kws, a.kwstar)


@stub("functools.partialmethod")
def _partialmethod(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `functools.partialmethod(f, *args, **kws)`: descriptor binding `self` before `args`."""
    return make_partial(a.get(0), a.pos[1:], a.star, a.kws, a.kwstar,
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
    return frozenset([Property(property_fn(a.get(0)))])


@stub("functools.total_ordering")
def _total_ordering(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `@functools.total_ordering`: return the decorated class unchanged."""
    return a.get(0)


@stub("functools.cmp_to_key")
def _cmp_to_key(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `functools.cmp_to_key(mycmp)`: return a key function wrapping `mycmp`."""
    return frozenset([Builtin("<cmp_to_key>", (shallow(a.get(0)),))])


@stub("<cmp_to_key>")
def _call_cmp_to_key(_it: Host, a: Args, _site: Site) -> Vals:
    """Model calling a `cmp_to_key` wrapper on an element: return a comparable key object."""
    return frozenset([Builtin("<cmp_key>", (a.pos[0], shallow(a.get(1))))])


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
    boxes = boxes_of(a.pos[0], "singledispatch")
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
        for b in boxes_of(a.pos[0], "singledispatch"):
            it.state.add_heap(b, impl)
    return impl


@stub("<singledispatch>.dispatch", "<singledispatchmethod>.dispatch")
def _singledispatch_dispatch(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `fn.dispatch(cls)`: return all registered implementations."""
    return a.pos[0]
