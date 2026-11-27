"""Stubs for `itertools`: accumulate, starmap, groupby, chain.from_iterable, repeat, compress,
zip_longest, product and the combinatorics, and count."""
from __future__ import annotations

from ..model import EMPTY, Site, Vals
from .registry import Args, Host, call, fold, stub


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
