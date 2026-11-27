"""Stubs for `contextlib`: contextmanager, asynccontextmanager, closing, aclosing,
nullcontext, the managers that call nothing (suppress, redirect_stdout, chdir, ...), ExitStack
and AsyncExitStack."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..model import EMPTY, Alloc, Box, Builtin, Instance, Site, Vals
from .functools import make_partial
from .registry import STUBS, Args, Host, boxes_of, call, shallow, stub


@stub("contextlib.contextmanager")
def _contextmanager(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `@contextlib.contextmanager`: wrap a generator function as a context manager factory."""
    return frozenset([Builtin("<contextmanager>", (shallow(a.get(0)),))])


@stub("<contextmanager>")
def _call_contextmanager(it: Host, a: Args, site: Site) -> Vals:
    """Model calling a `@contextmanager` function: run its body and wrap the yielded value."""
    yielded = it.apply(a.pos[0], a.pos[1:], a.star, a.kws, a.kwstar, site)
    return frozenset([Builtin("<ctx_value>", (shallow(yielded),))])


@stub("<ctx_value>.__enter__", "<actx_value>.__aenter__",
      "<nullcontext>.__enter__", "<nullcontext>.__aenter__")
def _ctx_enter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model entering a context manager wrapper: return the yielded or supplied value."""
    return a.pos[0]


@stub("<ctx_value>.__exit__", "<actx_value>.__aexit__",
      "<nullcontext>.__exit__", "<nullcontext>.__aexit__")
def _ctx_noop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model exiting a context manager whose cleanup needs no extra calls."""
    return EMPTY


@stub("contextlib.asynccontextmanager")
def _asynccontextmanager(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `@contextlib.asynccontextmanager`: wrap an async generator as an async CM factory."""
    return frozenset([Builtin("<asynccontextmanager>", (shallow(a.get(0)),))])


@stub("<asynccontextmanager>")
def _call_asynccontextmanager(it: Host, a: Args, site: Site) -> Vals:
    """Model calling an `@asynccontextmanager` function: run its body and wrap the yielded value."""
    yielded = it.apply(a.pos[0], a.pos[1:], a.star, a.kws, a.kwstar, site)
    return frozenset([Builtin("<actx_value>", (shallow(yielded),))])


@stub("contextlib.closing")
def _closing(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `contextlib.closing(thing)`: return a context manager that calls `thing.close()`."""
    return frozenset([Builtin("<closing>", (shallow(a.get(0)),))])


@stub("<closing>.__enter__", "<aclosing>.__aenter__")
def _closing_enter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model entering `closing(thing)` / `aclosing(thing)`: return `thing`."""
    return a.pos[0]


@stub("<closing>.__exit__")
def _closing_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model exiting `closing(thing)`: call `thing.close()`."""
    it.invoke_dunder(a.pos[0], "close", [], site)
    return EMPTY


@stub("contextlib.aclosing")
def _aclosing(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `contextlib.aclosing(thing)`: return an async CM that calls `thing.aclose()`."""
    return frozenset([Builtin("<aclosing>", (shallow(a.get(0)),))])


@stub("<aclosing>.__aexit__")
def _aclosing_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model exiting `aclosing(thing)`: call `thing.aclose()`."""
    it.invoke_dunder(a.pos[0], "aclose", [], site)
    return EMPTY


@stub("contextlib.nullcontext")
def _nullcontext(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `contextlib.nullcontext(enter_result=None)`: yield `enter_result` on enter."""
    return frozenset([Builtin("<nullcontext>", (shallow(a.get(0, "enter_result")),))])


@stub("contextlib.suppress", "contextlib.redirect_stdout", "contextlib.redirect_stderr",
      "contextlib.chdir", "contextlib.AbstractContextManager",
      "contextlib.AbstractAsyncContextManager")
def _noop_stub(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model stdlib helpers that invoke no user callbacks and return nothing callable."""
    return EMPTY


def _store_callback(it: Host, boxes: Sequence[Box], fn: Vals, pos: Sequence[Vals],
                    star: Vals | None, kws: Mapping[str, Vals], kwstar: Vals | None) -> Vals:
    """Store callback `fn` (wrapped as a partial if arguments are given) into `boxes`."""
    if not fn or it.readonly:
        return fn
    stored = (make_partial(fn, pos, star, kws, kwstar)
              if (pos or star is not None or kws or kwstar is not None) else fn)
    for b in boxes:
        it.state.add_heap(b, stored)
    return fn


def _push_exit(it: Host, cb_boxes: Sequence[Box], cm_boxes: Sequence[Box],
               obj: Vals, dunder: str) -> Vals:
    """Store `obj` into `cm_boxes` (if it defines `dunder`) and/or `cb_boxes` (as 3-arg exit fn)."""
    if not obj or it.readonly:
        return obj
    cms: set[object] = set()
    cbs: set[object] = set()
    for t in obj:
        if isinstance(t, Instance) and it.state.lookup_class_attr(t.cls, dunder):
            cms.add(t)
        elif isinstance(t, Builtin) and f"{t.name}.{dunder}" in STUBS:
            cms.add(t)
        else:
            cbs.add(t)
    if cms:
        cm_vals = frozenset(t for t in obj if t in cms)
        for b in cm_boxes:
            it.state.add_heap(b, cm_vals)
    if cbs:
        cb_vals = make_partial(frozenset(t for t in obj if t in cbs),
                                [EMPTY, EMPTY, EMPTY], None, {}, None)
        for b in cb_boxes:
            it.state.add_heap(b, cb_vals)
    return obj


@stub("contextlib.ExitStack")
def _exit_stack(_it: Host, _a: Args, site: Site) -> Vals:
    """Model `contextlib.ExitStack()`: allocate callback and context-manager boxes."""
    alloc = Alloc.of(site)
    return frozenset([Builtin("<exit_stack>", (
        frozenset([Box("exit_cb", alloc)]),
        frozenset([Box("exit_cm", alloc)]),
    ))])


@stub("<exit_stack>.__enter__", "<exit_stack>.pop_all")
def _exit_stack_self(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `ExitStack.__enter__` and `pop_all`: return the stack token itself."""
    return frozenset([Builtin("<exit_stack>", (
        frozenset(boxes_of(a.pos[0], "exit_cb")),
        frozenset(boxes_of(a.pos[1], "exit_cm")),
    ))])


@stub("<exit_stack>.callback")
def _exit_stack_callback(it: Host, a: Args, _site: Site) -> Vals:
    """Model `stack.callback(fn, *args, **kws)`: register `fn` to run on exit and return `fn`."""
    return _store_callback(it, boxes_of(a.pos[0], "exit_cb"), a.get(2),
                           a.pos[3:], a.star, a.kws, a.kwstar)


@stub("<exit_stack>.enter_context")
def _exit_stack_enter_context(it: Host, a: Args, site: Site) -> Vals:
    """Model `stack.enter_context(cm)`: call `cm.__enter__`, remember `cm` for `__exit__`."""
    cm = a.get(2)
    found, entered = it.invoke_dunder(cm, "__enter__", [], site)
    if cm and not it.readonly:
        for b in boxes_of(a.pos[1], "exit_cm"):
            it.state.add_heap(b, cm)
    return entered if found else cm


@stub("<exit_stack>.push")
def _exit_stack_push(it: Host, a: Args, _site: Site) -> Vals:
    """Model `stack.push(exit)`: register a context manager or 3-argument exit callback."""
    return _push_exit(it, boxes_of(a.pos[0], "exit_cb"), boxes_of(a.pos[1], "exit_cm"),
                      a.get(2), "__exit__")


@stub("<exit_stack>.__exit__", "<exit_stack>.close")
def _exit_stack_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model `ExitStack.__exit__` and `close`: invoke all registered callbacks and context exits."""
    call(it, a.pos[0], [], site)
    it.invoke_dunder(a.pos[1], "__exit__", [EMPTY, EMPTY, EMPTY], site)
    return EMPTY


@stub("contextlib.AsyncExitStack")
def _async_exit_stack(_it: Host, _a: Args, site: Site) -> Vals:
    """Model `contextlib.AsyncExitStack()`: allocate sync and async callback/CM boxes."""
    alloc = Alloc.of(site)
    return frozenset([Builtin("<async_exit_stack>", (
        frozenset([Box("exit_cb", alloc)]),
        frozenset([Box("exit_cm", alloc)]),
        frozenset([Box("aexit_cb", alloc)]),
        frozenset([Box("aexit_cm", alloc)]),
    ))])


@stub("<async_exit_stack>.__enter__", "<async_exit_stack>.__aenter__",
      "<async_exit_stack>.pop_all")
def _async_exit_stack_self(_it: Host, a: Args, _site: Site) -> Vals:
    """Model entering or `pop_all` on `AsyncExitStack`: return the stack token itself."""
    return frozenset([Builtin("<async_exit_stack>", (
        frozenset(boxes_of(a.pos[0], "exit_cb")),
        frozenset(boxes_of(a.pos[1], "exit_cm")),
        frozenset(boxes_of(a.pos[2], "aexit_cb")),
        frozenset(boxes_of(a.pos[3], "aexit_cm")),
    ))])


@stub("<async_exit_stack>.callback")
def _aexit_stack_callback(it: Host, a: Args, _site: Site) -> Vals:
    """Model `AsyncExitStack.callback(fn, *args, **kws)`: register a sync exit callback."""
    return _store_callback(it, boxes_of(a.pos[0], "exit_cb"), a.get(4),
                           a.pos[5:], a.star, a.kws, a.kwstar)


@stub("<async_exit_stack>.push_async_callback")
def _aexit_stack_async_callback(it: Host, a: Args, _site: Site) -> Vals:
    """Model `AsyncExitStack.push_async_callback(fn, *args, **kws)`: register an async callback."""
    return _store_callback(it, boxes_of(a.pos[2], "aexit_cb"), a.get(4),
                           a.pos[5:], a.star, a.kws, a.kwstar)


@stub("<async_exit_stack>.enter_context")
def _aexit_stack_enter_context(it: Host, a: Args, site: Site) -> Vals:
    """Model `AsyncExitStack.enter_context(cm)`: call `__enter__` and remember `cm`."""
    cm = a.get(4)
    found, entered = it.invoke_dunder(cm, "__enter__", [], site)
    if cm and not it.readonly:
        for b in boxes_of(a.pos[1], "exit_cm"):
            it.state.add_heap(b, cm)
    return entered if found else cm


@stub("<async_exit_stack>.enter_async_context")
def _aexit_stack_enter_async_context(it: Host, a: Args, site: Site) -> Vals:
    """Model `AsyncExitStack.enter_async_context(cm)`: call `__aenter__` and remember `cm`."""
    cm = a.get(4)
    found, entered = it.invoke_dunder(cm, "__aenter__", [], site)
    if cm and not it.readonly:
        for b in boxes_of(a.pos[3], "aexit_cm"):
            it.state.add_heap(b, cm)
    return entered if found else cm


@stub("<async_exit_stack>.push")
def _aexit_stack_push(it: Host, a: Args, _site: Site) -> Vals:
    """Model `AsyncExitStack.push(exit)`: register a sync context manager or exit callback."""
    return _push_exit(it, boxes_of(a.pos[0], "exit_cb"), boxes_of(a.pos[1], "exit_cm"),
                      a.get(4), "__exit__")


@stub("<async_exit_stack>.push_async_exit")
def _aexit_stack_push_async(it: Host, a: Args, _site: Site) -> Vals:
    """Model `AsyncExitStack.push_async_exit(exit)`: register an async CM or exit callback."""
    return _push_exit(it, boxes_of(a.pos[2], "aexit_cb"), boxes_of(a.pos[3], "aexit_cm"),
                      a.get(4), "__aexit__")


@stub("<async_exit_stack>.__exit__", "<async_exit_stack>.close")
def _aexit_stack_sync_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model synchronous exit on `AsyncExitStack`: invoke sync callbacks and context exits."""
    call(it, a.pos[0], [], site)
    it.invoke_dunder(a.pos[1], "__exit__", [EMPTY, EMPTY, EMPTY], site)
    return EMPTY


@stub("<async_exit_stack>.__aexit__", "<async_exit_stack>.aclose")
def _aexit_stack_async_exit(it: Host, a: Args, site: Site) -> Vals:
    """Model async exit on `AsyncExitStack`: invoke all sync and async callbacks and exits."""
    call(it, a.pos[0] | a.pos[2], [], site)
    it.invoke_dunder(a.pos[1], "__exit__", [EMPTY, EMPTY, EMPTY], site)
    it.invoke_dunder(a.pos[3], "__aexit__", [EMPTY, EMPTY, EMPTY], site)
    return EMPTY
