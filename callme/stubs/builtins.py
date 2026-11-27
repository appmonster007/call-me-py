"""Stubs for builtin functions: map, filter, sorted, min / max, len, str, iter, next, sum, ...

The `itertools` functions that only filter or slice (`filterfalse`, `takewhile`,
`dropwhile`) share their stubs with `filter`.
"""
from __future__ import annotations

from ..model import EMPTY, Site, Vals
from .registry import Args, Host, call, stub


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
