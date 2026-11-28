//! Stubs for `threading`: Thread, Timer and Barrier callbacks, and the lock types.

use crate::model::{EMPTY, Site, Vals};
use crate::name;

use crate::syntax::Name;

use super::registry::{Args, Host, Kws, Registry, builtin_val, call, shallow};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["threading.Thread"], thread);
    r.add(&["threading.Timer"], timer);
    r.add(&["<thread>.start", "<thread>.run"], thread_start);
    r.add(&["<thread_args>.start", "<thread_args>.run"], thread_start_args);
    r.add(&["<thread>.join", "<thread_args>.join", "<thread>.cancel", "<thread_args>.cancel"], noop);
    r.add(&["threading.Barrier"], barrier);
    r.add(&["<barrier>.wait"], barrier_wait);
    r.add(
        &[
            "threading.Lock",
            "threading.RLock",
            "threading.Semaphore",
            "threading.BoundedSemaphore",
            "threading.Event",
            "threading.Condition",
            "threading.local",
        ],
        noop,
    );
}

/// Return a `<thread>` or `<thread_args>` token binding the target and its arguments.
///
/// ```text
/// target  index and keyword of the callback parameter (`target` or `function`); `args` and
///         `kwargs` are the next two positional parameters. The token is `<thread_args>` when
///         arguments for the callback are given at all.
/// ```
fn thread_token(it: &mut dyn Host, a: &Args, target: usize, target_kw: Name) -> Vals {
    let v = it.values();
    let t = a.get(v, target, Some(target_kw));
    let target_v = shallow(v, t);
    let ta = a.get(v, target + 1, Some(name!("args")));
    let targs = shallow(v, ta);
    let tk = a.get(v, target + 2, Some(name!("kwargs")));
    let tkws = shallow(v, tk);
    let has_args = a.pos.len() > target + 1
        || a.kws.contains_key(&name!("args"))
        || a.kws.contains_key(&name!("kwargs"))
        || a.star.is_some()
        || a.kwstar.is_some();
    let kind = if has_args { name!("<thread_args>") } else { name!("<thread>") };
    builtin_val(v, kind, vec![target_v, targs, tkws])
}

/// Model `threading.Thread(group=None, target=None, name=None, args=(), kwargs=None)`.
///
/// `name` sits between `target` and `args`, so `args` is positional argument 3.
fn thread(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let t = a.get(v, 1, Some(name!("target")));
    let target = shallow(v, t);
    let ta = a.get(v, 3, Some(name!("args")));
    let targs = shallow(v, ta);
    let tk = a.get(v, 4, Some(name!("kwargs")));
    let tkws = shallow(v, tk);
    let has_args = a.pos.len() > 3
        || a.kws.contains_key(&name!("args"))
        || a.kws.contains_key(&name!("kwargs"))
        || a.star.is_some()
        || a.kwstar.is_some();
    let kind = if has_args { name!("<thread_args>") } else { name!("<thread>") };
    builtin_val(v, kind, vec![target, targs, tkws])
}

/// Model `threading.Timer(interval, function, args=None, kwargs=None)`.
fn timer(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    thread_token(it, a, 1, name!("function"))
}

/// Model `t.start()` / `t.run()` when no `args`/`kwargs` were passed: call `target()`.
fn thread_start(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    it.apply(a.p(0), &[], None, &Kws::default(), None, site);
    EMPTY
}

/// Model `t.start()` / `t.run()` with `args`/`kwargs`: call `target(*args, **kwargs)`.
fn thread_start_args(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    it.apply(a.p(0), &[], Some(a.p(1)), &Kws::default(), Some(a.p(2)), site);
    EMPTY
}

/// Model calls that run no user callbacks: `join`, `cancel` and the lock types.
fn noop(_it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    EMPTY
}

/// Model `threading.Barrier(parties, action=None)`: remember the barrier action.
fn barrier(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let act = a.get(v, 1, Some(name!("action")));
    let s = shallow(v, act);
    builtin_val(v, name!("<barrier>"), vec![s])
}

/// Model `barrier.wait()`: invoke the barrier's action callback if any.
fn barrier_wait(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    call(it, a.p(0), &[], site, None);
    EMPTY
}
