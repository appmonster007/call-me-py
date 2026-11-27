"""Stubs for `asyncio`: run, wait_for, shield, create_task, gather, to_thread, sleep, the event
loop, TaskGroup, the queues and the synchronisation primitives."""
from __future__ import annotations

from ..model import EMPTY, Alloc, Box, Builtin, Site, Vals
from .futures import new_future
from .registry import Args, Host, boxes_of, stub


@stub("asyncio.run", "asyncio.wait_for", "asyncio.shield")
def _asyncio_passthrough(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `asyncio.run`, `wait_for` and `shield`: return the coroutine/awaitable's value."""
    return a.get(0)


@stub("asyncio.create_task", "asyncio.ensure_future",
      "<event_loop>.create_task", "<task_group>.create_task")
def _asyncio_create_task(it: Host, a: Args, site: Site) -> Vals:
    """Model `create_task(coro)` / `ensure_future(coro)`: wrap the result in a `<future>`."""
    return new_future(it, site, a.get(0))


@stub("asyncio.gather")
def _asyncio_gather(_it: Host, a: Args, site: Site) -> Vals:
    """Model `asyncio.gather(*aws)`: return all awaitable results plus a list box."""
    return a.rest(0) | frozenset([Box("list", Alloc.of(site))])


@stub("asyncio.to_thread")
def _asyncio_to_thread(it: Host, a: Args, site: Site) -> Vals:
    """Model `asyncio.to_thread(fn, *args, **kws)`: call `fn` with the given arguments."""
    return it.apply(a.get(0), a.pos[1:], a.star, a.kws, a.kwstar, site)


@stub("asyncio.sleep")
def _asyncio_sleep(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `asyncio.sleep(delay, result=None)`: return `result`."""
    return a.get(1, "result")


@stub("asyncio.get_running_loop", "asyncio.get_event_loop", "asyncio.new_event_loop")
def _asyncio_loop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model obtaining an event loop: return an `<event_loop>` token."""
    return frozenset([Builtin("<event_loop>")])


@stub("<event_loop>.call_soon", "<event_loop>.call_soon_threadsafe")
def _loop_call_soon(it: Host, a: Args, site: Site) -> Vals:
    """Model `loop.call_soon(cb, *args)` / `call_soon_threadsafe`: invoke `cb(*args)`."""
    it.apply(a.get(0), a.pos[1:], a.star, {}, None, site)
    return EMPTY


@stub("<event_loop>.call_later", "<event_loop>.call_at")
def _loop_call_later(it: Host, a: Args, site: Site) -> Vals:
    """Model `loop.call_later(delay, cb, *args)` / `call_at`: invoke `cb(*args)`."""
    it.apply(a.get(1), a.pos[2:], a.star, {}, None, site)
    return EMPTY


@stub("<event_loop>.run_in_executor")
def _loop_run_in_executor(it: Host, a: Args, site: Site) -> Vals:
    """Model `loop.run_in_executor(executor, fn, *args)`: call `fn(*args)` and return a future."""
    res = it.apply(a.get(1), a.pos[2:], a.star, {}, None, site)
    return new_future(it, site, res)


@stub("<event_loop>.create_future")
def _loop_create_future(it: Host, _a: Args, site: Site) -> Vals:
    """Model `loop.create_future()`: return an empty `<future>` token."""
    return new_future(it, site)


@stub("<event_loop>.run_until_complete")
def _loop_run_until_complete(it: Host, a: Args, site: Site) -> Vals:
    """Model `loop.run_until_complete(future)`: await `future` if awaitable, else return it."""
    v = a.get(0)
    found, out = it.invoke_dunder(v, "__await__", [], site)
    return out if found else v


@stub("<event_loop>.close", "<event_loop>.stop", "<event_loop>.run_forever")
def _loop_noop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model loop lifecycle methods that invoke no user callbacks."""
    return EMPTY


@stub("asyncio.TaskGroup")
def _task_group(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `asyncio.TaskGroup()`: return an async context manager for creating tasks."""
    return frozenset([Builtin("<task_group>")])


@stub("<task_group>.__aenter__")
def _task_group_enter(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model entering an `asyncio.TaskGroup`: return the task group token itself."""
    return frozenset([Builtin("<task_group>")])


@stub("<task_group>.__aexit__")
def _task_group_exit(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model exiting an `asyncio.TaskGroup`: no extra callbacks."""
    return EMPTY


@stub("asyncio.Queue", "asyncio.LifoQueue", "asyncio.PriorityQueue")
def _async_queue(_it: Host, _a: Args, site: Site) -> Vals:
    """Model `asyncio.Queue` variants: allocate a heap box for queued items."""
    return frozenset([Builtin("<async_queue>", (frozenset([Box("queue", Alloc.of(site))]),))])


@stub("<async_queue>.put", "<async_queue>.put_nowait")
def _async_queue_put(it: Host, a: Args, _site: Site) -> Vals:
    """Model `q.put(item)` / `q.put_nowait(item)`: store `item` into the queue's box."""
    item = a.get(1)
    if item and not it.readonly:
        for b in boxes_of(a.pos[0], "queue"):
            it.state.add_heap(b, item)
    return EMPTY


@stub("<async_queue>.get", "<async_queue>.get_nowait")
def _async_queue_get(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `q.get()` / `q.get_nowait()`: return the items stored in the queue."""
    return a.pos[0]


@stub("asyncio.Lock", "asyncio.Event", "asyncio.Condition",
      "asyncio.Semaphore", "asyncio.BoundedSemaphore", "asyncio.Barrier")
def _asyncio_sync(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model asyncio synchronisation primitives: no user callbacks."""
    return EMPTY
