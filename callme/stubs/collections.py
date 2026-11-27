"""Stubs for `collections`: defaultdict, deque, OrderedDict / Counter / UserDict, UserList,
ChainMap and namedtuple."""
from __future__ import annotations

from ..model import EMPTY, Alloc, Box, Builtin, Site, Vals
from .registry import Args, Host, boxes_of, call, shallow, stub, union


@stub("collections.defaultdict")
def _defaultdict(it: Host, a: Args, site: Site) -> Vals:
    """Model `collections.defaultdict(default_factory=None, ...)`: dict box plus factory hook."""
    box = Box("dict", Alloc.of(site))
    factory = shallow(a.pos[0]) if a.pos else EMPTY
    elems = it.iterate(a.rest(1), site) | union(a.kws.values()) | (a.kwstar or EMPTY)
    if factory:
        return elems | frozenset([box, Builtin("<defaultdict>", (factory, frozenset([box])))])
    return elems | frozenset([box])


@stub("<defaultdict>.__getitem__")
def _defaultdict_getitem(it: Host, a: Args, site: Site) -> Vals:
    """Model `d[k]` on a `defaultdict`: call `default_factory()`, store the result, return it."""
    res = call(it, a.pos[0], [], site)
    if res and not it.readonly:
        for b in boxes_of(a.pos[1], "dict"):
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
