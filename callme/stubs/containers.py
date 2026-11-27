"""Stubs for the remaining builtins: tuple, frozenset, enumerate, zip (and itertools.chain,
cycle and islice), list / set / dict, the element methods of containers, and `__import__`.

These are registered after `objects`, as they were before the stubs were split into modules,
so the order of `STUBS` is unchanged.
"""
from __future__ import annotations

from ..model import Alloc, Box, Site, Vals
from .registry import Args, Host, StubFn, stub, union


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
