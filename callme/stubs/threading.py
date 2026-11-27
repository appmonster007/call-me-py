"""Stubs for `threading`: Thread, Timer, Barrier, the locks and the other synchronisation
primitives, and `local`."""
from __future__ import annotations

from ..model import EMPTY, Builtin, Site, Vals
from .registry import Args, Host, call, shallow, stub


@stub("threading.Thread")
def _thread(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `threading.Thread(group=None, target=None, name=None, args=(), kwargs=None)`."""
    target = shallow(a.get(1, "target"))
    targs = shallow(a.get(3, "args"))
    tkws = shallow(a.get(4, "kwargs"))
    has_args = (len(a.pos) > 3 or "args" in a.kws or "kwargs" in a.kws
                or a.star is not None or a.kwstar is not None)
    kind = "<thread_args>" if has_args else "<thread>"
    return frozenset([Builtin(kind, (target, targs, tkws))])


@stub("threading.Timer")
def _timer(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `threading.Timer(interval, function, args=None, kwargs=None)`."""
    target = shallow(a.get(1, "function"))
    targs = shallow(a.get(2, "args"))
    tkws = shallow(a.get(3, "kwargs"))
    has_args = (len(a.pos) > 2 or "args" in a.kws or "kwargs" in a.kws
                or a.star is not None or a.kwstar is not None)
    kind = "<thread_args>" if has_args else "<thread>"
    return frozenset([Builtin(kind, (target, targs, tkws))])


@stub("<thread>.start", "<thread>.run")
def _thread_start(it: Host, a: Args, site: Site) -> Vals:
    """Model `t.start()` / `t.run()` when no `args`/`kwargs` were passed: call `target()`."""
    it.apply(a.pos[0], [], None, {}, None, site)
    return EMPTY


@stub("<thread_args>.start", "<thread_args>.run")
def _thread_start_args(it: Host, a: Args, site: Site) -> Vals:
    """Model `t.start()` / `t.run()` with `args`/`kwargs`: call `target(*args, **kwargs)`."""
    it.apply(a.pos[0], [], a.pos[1], {}, a.pos[2], site)
    return EMPTY


@stub("<thread>.join", "<thread_args>.join", "<thread>.cancel", "<thread_args>.cancel")
def _thread_noop(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `Thread.join` and `Timer.cancel`: no callbacks."""
    return EMPTY


@stub("threading.Barrier")
def _barrier(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `threading.Barrier(parties, action=None)`: remember the barrier action."""
    return frozenset([Builtin("<barrier>", (shallow(a.get(1, "action")),))])


@stub("<barrier>.wait")
def _barrier_wait(it: Host, a: Args, site: Site) -> Vals:
    """Model `barrier.wait()`: invoke the barrier's action callback if any."""
    call(it, a.pos[0], [], site)
    return EMPTY


@stub("threading.Lock", "threading.RLock", "threading.Semaphore",
      "threading.BoundedSemaphore", "threading.Event", "threading.Condition",
      "threading.local")
def _threading_sync(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model threading synchronisation primitives: no user callbacks."""
    return EMPTY
