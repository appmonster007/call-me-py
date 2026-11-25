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

from .model import EMPTY, Builtin, ClassMethod, Site, StaticMethod, Vals

if TYPE_CHECKING:
    from .transfer import Interp

STUB_MODULES = frozenset({"functools", "itertools"})
METHOD_STUBS = {"sort": "list.sort"}  # attribute name -> stub (receiver is bound first)
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
    call(it, a.kw("key"), [elems], site)
    return elems


@stub("min", "max")
def _minmax(it: Interp, a: Args, site: Site) -> Vals:
    elems = a.pos[0] if len(a.pos) == 1 and a.star is None else a.rest(0)
    call(it, a.kw("key"), [elems], site)
    return elems | a.kw("default")


@stub("list.sort")
def _list_sort(it: Interp, a: Args, site: Site) -> Vals:
    call(it, a.kw("key"), [a.get(0)], site)  # a.pos[0] is the bound receiver
    return EMPTY


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


@stub("list", "tuple", "set", "frozenset", "dict", "iter", "next", "reversed",
      "enumerate", "zip", "itertools.chain", "itertools.cycle", "itertools.islice")
def _passthrough(it: Interp, a: Args, site: Site) -> Vals:
    return a.all()


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
