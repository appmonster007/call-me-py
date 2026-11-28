//! Stubs for `asyncio`: tasks, gather, threads, event loops, task groups and queues.

use crate::model::{Alloc, EMPTY, Site, Token, Vals};
use crate::name;

use super::futures::new_future;
use super::registry::{Args, Host, Kws, Registry, boxes_of, builtin_val};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["asyncio.run", "asyncio.wait_for", "asyncio.shield"], passthrough);
    r.add(
        &["asyncio.create_task", "asyncio.ensure_future", "<event_loop>.create_task", "<task_group>.create_task"],
        create_task,
    );
    r.add(&["asyncio.gather"], gather);
    r.add(&["asyncio.to_thread"], to_thread);
    r.add(&["asyncio.sleep"], sleep);
    r.add(&["asyncio.get_running_loop", "asyncio.get_event_loop", "asyncio.new_event_loop"], event_loop);
    r.add(&["<event_loop>.call_soon", "<event_loop>.call_soon_threadsafe"], call_soon);
    r.add(&["<event_loop>.call_later", "<event_loop>.call_at"], call_later);
    r.add(&["<event_loop>.run_in_executor"], run_in_executor);
    r.add(&["<event_loop>.create_future"], create_future);
    r.add(&["<event_loop>.run_until_complete"], run_until_complete);
    r.add(&["<event_loop>.close", "<event_loop>.stop", "<event_loop>.run_forever"], noop);
    r.add(&["asyncio.TaskGroup"], task_group);
    r.add(&["<task_group>.__aenter__"], task_group);
    r.add(&["<task_group>.__aexit__"], noop);
    r.add(&["asyncio.Queue", "asyncio.LifoQueue", "asyncio.PriorityQueue"], queue);
    r.add(&["<async_queue>.put", "<async_queue>.put_nowait"], queue_put);
    r.add(&["<async_queue>.get", "<async_queue>.get_nowait"], queue_get);
    r.add(
        &[
            "asyncio.Lock",
            "asyncio.Event",
            "asyncio.Condition",
            "asyncio.Semaphore",
            "asyncio.BoundedSemaphore",
            "asyncio.Barrier",
        ],
        noop,
    );
}

/// Model `asyncio.run`, `wait_for` and `shield`: return the coroutine/awaitable's value.
fn passthrough(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.get(it.values(), 0, None)
}

/// Model `create_task(coro)` / `ensure_future(coro)`: wrap the result in a `<future>`.
fn create_task(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let coro = a.get(it.values(), 0, None);
    new_future(it, site, coro)
}

/// Model `asyncio.gather(*aws)`: return all awaitable results plus a list box.
fn gather(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = it.values();
    let all = a.rest(v, 0);
    let b = v.tok(Token::Box { kind: name!("list"), alloc: Alloc::of(&site) });
    v.add(all, b)
}

/// Model `asyncio.to_thread(fn, *args, **kws)`: call `fn` with the given arguments.
fn to_thread(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let f = a.get(it.values(), 0, None);
    it.apply(f, a.from(1), a.star, &a.kws, a.kwstar, site)
}

/// Model `asyncio.sleep(delay, result=None)`: return `result`.
fn sleep(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.get(it.values(), 1, Some(name!("result")))
}

/// Model obtaining an event loop: return an `<event_loop>` token.
fn event_loop(it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    builtin_val(it.values(), name!("<event_loop>"), Vec::new())
}

/// Model `loop.call_soon(cb, *args)` / `call_soon_threadsafe`: invoke `cb(*args)`.
fn call_soon(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let cb = a.get(it.values(), 0, None);
    it.apply(cb, a.from(1), a.star, &Kws::default(), None, site);
    EMPTY
}

/// Model `loop.call_later(delay, cb, *args)` / `call_at`: invoke `cb(*args)`.
fn call_later(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let cb = a.get(it.values(), 1, None);
    it.apply(cb, a.from(2), a.star, &Kws::default(), None, site);
    EMPTY
}

/// Model `loop.run_in_executor(executor, fn, *args)`: call `fn(*args)` and return a future.
fn run_in_executor(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let f = a.get(it.values(), 1, None);
    let res = it.apply(f, a.from(2), a.star, &Kws::default(), None, site);
    new_future(it, site, res)
}

/// Model `loop.create_future()`: return an empty `<future>` token.
fn create_future(it: &mut dyn Host, _a: &Args, site: Site) -> Vals {
    new_future(it, site, EMPTY)
}

/// Model `loop.run_until_complete(future)`: await `future` if awaitable, else return it.
fn run_until_complete(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = a.get(it.values(), 0, None);
    let (found, out) = it.invoke_dunder(v, name!("__await__"), &[], site);
    if found { out } else { v }
}

/// Model calls that invoke no user callbacks and return nothing callable.
fn noop(_it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    EMPTY
}

/// Model `asyncio.TaskGroup()` and entering one: return the task group token.
fn task_group(it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    builtin_val(it.values(), name!("<task_group>"), Vec::new())
}

/// Model `asyncio.Queue` variants: allocate a heap box for queued items.
fn queue(it: &mut dyn Host, _a: &Args, site: Site) -> Vals {
    let v = it.values();
    let b = v.tok(Token::Box { kind: name!("queue"), alloc: Alloc::of(&site) });
    let bv = v.single(b);
    builtin_val(v, name!("<async_queue>"), vec![bv])
}

/// Model `q.put(item)` / `q.put_nowait(item)`: store `item` into the queue's box.
fn queue_put(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let item = a.get(it.values(), 1, None);
    if item != EMPTY && !it.readonly() {
        for b in boxes_of(it.values(), a.p(0), name!("queue")) {
            it.state().add_heap(b, item);
        }
    }
    EMPTY
}

/// Model `q.get()` / `q.get_nowait()`: return the items stored in the queue.
fn queue_get(_it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.p(0)
}
