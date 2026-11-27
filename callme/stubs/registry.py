"""The stub registry: `@stub`, `STUBS`, the arguments a stub gets, and what it may call.

A stub is a function `(it: Host, a: Args, site: Site) -> Vals` registered under one or more
names with `@stub`. `Host` and `HostState` are protocols for the parts of the interpreter and
of the solver state that stubs use, so this package never imports `callme.analysis`.
`call` and `fold` make calls back into the program; `shallow` and `boxes_of` are helpers shared
by several stub modules.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..frontend import FuncInfo
from ..model import EMPTY, Box, ClassId, ClassObj, Instance, Site, Vals

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
    """The parts of `analysis.State` that stubs use, as a protocol."""

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
    """The parts of `analysis.Interp` that stubs use, as a protocol.

    A protocol keeps this package free of imports from `callme.analysis`, which imports it.
    Calls made through it come from the node whose code called the stub.
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


def shallow(v: Vals) -> Vals:
    """Drop tokens nested `MAX_PARTIAL_DEPTH` deep from `v`, so nested tokens stay finite."""
    return frozenset(t for t in v if t.depth < MAX_PARTIAL_DEPTH)


def boxes_of(vals: Vals, kind: str) -> list[Box]:
    """Return every `Box` token of `kind` present in `vals`."""
    return [t for t in vals if isinstance(t, Box) and t.kind == kind]
