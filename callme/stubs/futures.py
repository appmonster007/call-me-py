"""Stubs for futures: `concurrent.futures.Future` and `asyncio.Future`, the executors,
and `as_completed` and `wait` of both modules."""
from __future__ import annotations

from ..model import EMPTY, Alloc, Box, Builtin, Site, Vals
from .registry import Args, Host, boxes_of, call, stub


def new_future(it: Host, site: Site, result: Vals = EMPTY) -> Vals:
    """Create a `<future>` token backed by result and callback boxes at `site`."""
    alloc = Alloc.of(site)
    res_box = Box("future_res", alloc)
    cb_box = Box("future_cb", alloc)
    if result and not it.readonly:
        it.state.add_heap(res_box, result)
    return frozenset([Builtin("<future>", (frozenset([res_box]), frozenset([cb_box])))])


@stub("concurrent.futures.Future", "asyncio.Future")
def _future(it: Host, _a: Args, site: Site) -> Vals:
    """Model `Future()`: return an empty `<future>` token."""
    return new_future(it, site)


@stub("<future>.result", "<future>.__await__")
def _future_result(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `fut.result()` and `await fut`: return the values stored as the future's result."""
    return a.pos[0]


@stub("<future>.set_result")
def _future_set_result(it: Host, a: Args, site: Site) -> Vals:
    """Model `fut.set_result(val)`: store `val` and invoke any registered done callbacks."""
    res_boxes = boxes_of(a.pos[0], "future_res")
    cb_boxes = boxes_of(a.pos[1], "future_cb")
    val = a.get(2)
    if val and not it.readonly:
        for b in res_boxes:
            it.state.add_heap(b, val)
    fut = frozenset([Builtin("<future>", (frozenset(res_boxes), frozenset(cb_boxes)))])
    call(it, a.pos[1], [fut], site)
    return EMPTY


@stub("<future>.add_done_callback")
def _future_add_done_callback(it: Host, a: Args, site: Site) -> Vals:
    """Model `fut.add_done_callback(cb)`: store `cb` and invoke `cb(fut)`."""
    res_boxes = boxes_of(a.pos[0], "future_res")
    cb_boxes = boxes_of(a.pos[1], "future_cb")
    cb = a.get(2)
    if cb and not it.readonly:
        for b in cb_boxes:
            it.state.add_heap(b, cb)
    fut = frozenset([Builtin("<future>", (frozenset(res_boxes), frozenset(cb_boxes)))])
    call(it, cb, [fut], site)
    return EMPTY


@stub("<future>.exception", "<future>.cancel", "<future>.cancelled",
      "<future>.done", "<future>.running")
def _future_noop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model status/cancellation methods of a `Future`: no user callbacks."""
    return EMPTY


@stub("concurrent.futures.ThreadPoolExecutor", "concurrent.futures.ProcessPoolExecutor")
def _executor(it: Host, a: Args, site: Site) -> Vals:
    """Model `ThreadPoolExecutor` / `ProcessPoolExecutor`: run `initializer` if given."""
    init = a.kw("initializer")
    if init:
        has_initargs = "initargs" in a.kws or a.kwstar is not None
        it.apply(init, [], a.kw("initargs") if has_initargs else None, {}, None, site)
    return frozenset([Builtin("<executor>")])


@stub("<executor>.__enter__")
def _executor_enter(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model entering an executor context: return the executor token itself."""
    return frozenset([Builtin("<executor>")])


@stub("<executor>.__exit__", "<executor>.shutdown")
def _executor_exit(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model exiting or shutting down an executor: no extra callbacks."""
    return EMPTY


@stub("<executor>.submit")
def _executor_submit(it: Host, a: Args, site: Site) -> Vals:
    """Model `ex.submit(fn, *args, **kws)`: call `fn` and wrap the result in a `<future>`."""
    res = it.apply(a.get(0), a.pos[1:], a.star, a.kws, a.kwstar, site)
    return new_future(it, site, res)


@stub("<executor>.map")
def _executor_map(it: Host, a: Args, site: Site) -> Vals:
    """Model `ex.map(fn, *iterables, timeout=..., chunksize=...)`: call `fn` on each iterable."""
    f = a.get(0)
    if a.star is not None:
        return call(it, f, [], site, star=a.rest(1))
    return call(it, f, a.pos[1:], site)


@stub("concurrent.futures.as_completed", "concurrent.futures.wait",
      "asyncio.as_completed", "asyncio.wait")
def _futures_iter(it: Host, a: Args, site: Site) -> Vals:
    """Model `as_completed(fs)` and `wait(fs)`: return the futures in `fs`."""
    return it.iterate(a.get(0), site)
