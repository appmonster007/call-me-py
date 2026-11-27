"""Stub repository: how builtin / stdlib callables use their arguments.

The analyser never sees the C source of `map`, `sorted`, `functools.reduce`, ...
Each stub here replays, on abstract values, the calls such a function makes and
the value it returns. Callbacks are invoked through `Interp.apply` at the HOF's
own call site, so the edge goes from the Python function that called the HOF to
the callback (matching a traced run, where C frames are invisible).

Abstract values are sets of tokens and containers are collapsed to the union of
their elements, so "the elements of iterable `xs`" is just the value of `xs`.

Adding a stub: write `def _x(it, a, site) -> Vals` (unused parameters get a `_`
prefix) in the module for its library, and register it with `@stub("name",
"module.name", ...)` from `registry`. Names without a dot are builtins (looked up when
no user binding shadows them); dotted names are reached via `import module` /
`from module import name`; `<...>` names are internal. A new library also needs its
module in `STUB_MODULES` and in `MODULES` below.
"""
from __future__ import annotations

from . import (
    builtins,
    objects,
    dataclasses,
    containers,
    functools,
    itertools,
    contextlib,
    collections,
    threading,
    futures,
    asyncio,
)
from .registry import (
    METHOD_STUBS,
    STUB_MODULES,
    STUB_PACKAGES,
    STUBS,
    Args,
    Host,
    HostState,
    StubFn,
    call,
    fold,
    stub,
    union,
)

# Importing a module registers its stubs. `from m import *` binds a stub module's names in
# registration order (`Interp.s_ImportFrom`), so keep this order when adding a module.
MODULES = (builtins, objects, dataclasses, containers, functools, itertools, contextlib,
           collections, threading, futures, asyncio)
BUILTIN_NAMES = frozenset(n for n in STUBS if "." not in n and not n.startswith("<"))

__all__ = [
    "BUILTIN_NAMES", "METHOD_STUBS", "MODULES", "STUB_MODULES", "STUB_PACKAGES", "STUBS", "Args",
    "Host", "HostState", "StubFn", "call", "fold", "stub", "union",
]
