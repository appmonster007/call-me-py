//! Stubs for `concurrent.futures` and `asyncio.Future`: futures with result and callback
//! boxes, the executors, and the helpers that iterate futures.

use crate::model::{Alloc, EMPTY, Site, Token, Vals};
use crate::name;

use super::registry::{Args, Host, Kws, Registry, boxes_of, builtin_val, call};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["concurrent.futures.Future", "asyncio.Future"], future);
    r.add(&["<future>.result", "<future>.__await__"], future_result);
    r.add(&["<future>.set_result"], future_set_result);
    r.add(&["<future>.add_done_callback"], future_add_done_callback);
    r.add(&["<future>.exception", "<future>.cancel", "<future>.cancelled", "<future>.done", "<future>.running"], noop);
    r.add(&["concurrent.futures.ThreadPoolExecutor", "concurrent.futures.ProcessPoolExecutor"], executor);
    r.add(&["<executor>.__enter__"], executor_enter);
    r.add(&["<executor>.__exit__", "<executor>.shutdown"], noop);
    r.add(&["<executor>.submit"], executor_submit);
    r.add(&["<executor>.map"], executor_map);
    r.add(
        &["concurrent.futures.as_completed", "concurrent.futures.wait", "asyncio.as_completed", "asyncio.wait"],
        futures_iter,
    );
}

/// Create a `<future>` token backed by result and callback boxes at `site`, holding `result`.
pub fn new_future(it: &mut dyn Host, site: Site, result: Vals) -> Vals {
    let alloc = Alloc::of(&site);
    let v = it.values();
    let res_box = v.tok(Token::Box { kind: name!("future_res"), alloc });
    let cb_box = v.tok(Token::Box { kind: name!("future_cb"), alloc });
    if result != EMPTY && !it.readonly() {
        it.state().add_heap(res_box, result);
    }
    let v = it.values();
    let r = v.single(res_box);
    let c = v.single(cb_box);
    builtin_val(v, name!("<future>"), vec![r, c])
}

/// Model `Future()`: return an empty `<future>` token.
fn future(it: &mut dyn Host, _a: &Args, site: Site) -> Vals {
    new_future(it, site, EMPTY)
}

/// Model `fut.result()` and `await fut`: return the values stored as the future's result.
fn future_result(_it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.p(0)
}

/// Return the `<future>` token for the future whose bound boxes are arguments 0 and 1.
fn same_future(it: &mut dyn Host, a: &Args) -> Vals {
    let v = it.values();
    let res = boxes_of(v, a.p(0), name!("future_res"));
    let cbs = boxes_of(v, a.p(1), name!("future_cb"));
    let r = v.of(res);
    let c = v.of(cbs);
    builtin_val(v, name!("<future>"), vec![r, c])
}

/// Model `fut.set_result(val)`: store `val` and invoke any registered done callbacks.
fn future_set_result(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let val = a.get(it.values(), 2, None);
    if val != EMPTY && !it.readonly() {
        for b in boxes_of(it.values(), a.p(0), name!("future_res")) {
            it.state().add_heap(b, val);
        }
    }
    let fut = same_future(it, a);
    call(it, a.p(1), &[fut], site, None);
    EMPTY
}

/// Model `fut.add_done_callback(cb)`: store `cb` and invoke `cb(fut)`.
fn future_add_done_callback(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let cb = a.get(it.values(), 2, None);
    if cb != EMPTY && !it.readonly() {
        for b in boxes_of(it.values(), a.p(1), name!("future_cb")) {
            it.state().add_heap(b, cb);
        }
    }
    let fut = same_future(it, a);
    call(it, cb, &[fut], site, None);
    EMPTY
}

/// Model future status methods and executor exits: no user callbacks.
fn noop(_it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    EMPTY
}

/// Model `ThreadPoolExecutor` / `ProcessPoolExecutor`: run `initializer` if given.
fn executor(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let init = a.kw(it.values(), name!("initializer"));
    if init != EMPTY {
        let has_initargs = a.kws.contains_key(&name!("initargs")) || a.kwstar.is_some();
        let star = if has_initargs { Some(a.kw(it.values(), name!("initargs"))) } else { None };
        it.apply(init, &[], star, &Kws::default(), None, site);
    }
    builtin_val(it.values(), name!("<executor>"), Vec::new())
}

/// Model entering an executor context: return the executor token itself.
fn executor_enter(it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    builtin_val(it.values(), name!("<executor>"), Vec::new())
}

/// Model `ex.submit(fn, *args, **kws)`: call `fn` and wrap the result in a `<future>`.
fn executor_submit(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let f = a.get(it.values(), 0, None);
    let res = it.apply(f, a.from(1), a.star, &a.kws, a.kwstar, site);
    new_future(it, site, res)
}

/// Model `ex.map(fn, *iterables, timeout=..., chunksize=...)`: call `fn` on each iterable.
fn executor_map(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let f = a.get(it.values(), 0, None);
    if a.star.is_some() {
        let rest = a.rest(it.values(), 1);
        return call(it, f, &[], site, Some(rest));
    }
    call(it, f, a.from(1), site, None)
}

/// Model `as_completed(fs)` and `wait(fs)`: return the futures in `fs`.
fn futures_iter(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let fs = a.get(it.values(), 0, None);
    it.iterate(fs, site, false)
}
