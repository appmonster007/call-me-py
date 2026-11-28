//! Stubs for `contextlib`: generator-based context managers, `closing`, `nullcontext`, and
//! `ExitStack` / `AsyncExitStack` with their callback and context-manager boxes.

use crate::model::{Alloc, EMPTY, Site, TokId, Token, Vals};
use crate::name;
use crate::syntax::Name;

use super::STUBS;
use super::functools::make_partial;
use super::registry::{Args, Host, Kws, Registry, boxes_of, builtin_val, call, shallow};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["contextlib.contextmanager"], contextmanager);
    r.add(&["<contextmanager>"], call_contextmanager);
    r.add(
        &["<ctx_value>.__enter__", "<actx_value>.__aenter__", "<nullcontext>.__enter__", "<nullcontext>.__aenter__"],
        ctx_enter,
    );
    r.add(
        &["<ctx_value>.__exit__", "<actx_value>.__aexit__", "<nullcontext>.__exit__", "<nullcontext>.__aexit__"],
        noop,
    );
    r.add(&["contextlib.asynccontextmanager"], asynccontextmanager);
    r.add(&["<asynccontextmanager>"], call_asynccontextmanager);
    r.add(&["contextlib.closing"], closing);
    r.add(&["<closing>.__enter__", "<aclosing>.__aenter__"], ctx_enter);
    r.add(&["<closing>.__exit__"], closing_exit);
    r.add(&["contextlib.aclosing"], aclosing);
    r.add(&["<aclosing>.__aexit__"], aclosing_exit);
    r.add(&["contextlib.nullcontext"], nullcontext);
    r.add(
        &[
            "contextlib.suppress",
            "contextlib.redirect_stdout",
            "contextlib.redirect_stderr",
            "contextlib.chdir",
            "contextlib.AbstractContextManager",
            "contextlib.AbstractAsyncContextManager",
        ],
        noop,
    );
    r.add(&["contextlib.ExitStack"], exit_stack);
    r.add(&["<exit_stack>.__enter__", "<exit_stack>.pop_all"], exit_stack_self);
    r.add(&["<exit_stack>.callback"], exit_stack_callback);
    r.add(&["<exit_stack>.enter_context"], exit_stack_enter_context);
    r.add(&["<exit_stack>.push"], exit_stack_push);
    r.add(&["<exit_stack>.__exit__", "<exit_stack>.close"], exit_stack_exit);
    r.add(&["contextlib.AsyncExitStack"], async_exit_stack);
    r.add(
        &["<async_exit_stack>.__enter__", "<async_exit_stack>.__aenter__", "<async_exit_stack>.pop_all"],
        async_exit_stack_self,
    );
    r.add(&["<async_exit_stack>.callback"], aexit_stack_callback);
    r.add(&["<async_exit_stack>.push_async_callback"], aexit_stack_async_callback);
    r.add(&["<async_exit_stack>.enter_context"], aexit_stack_enter_context);
    r.add(&["<async_exit_stack>.enter_async_context"], aexit_stack_enter_async_context);
    r.add(&["<async_exit_stack>.push"], aexit_stack_push);
    r.add(&["<async_exit_stack>.push_async_exit"], aexit_stack_push_async);
    r.add(&["<async_exit_stack>.__exit__", "<async_exit_stack>.close"], aexit_stack_sync_exit);
    r.add(&["<async_exit_stack>.__aexit__", "<async_exit_stack>.aclose"], aexit_stack_async_exit);
}

/// Return the value holding a `name` token that binds `shallow(arg)`.
fn wrap_shallow(it: &mut dyn Host, name: Name, arg: Vals) -> Vals {
    let v = it.values();
    let s = shallow(v, arg);
    builtin_val(v, name, vec![s])
}

/// Model `@contextlib.contextmanager`: wrap a generator function as a context manager factory.
fn contextmanager(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let f = a.get(it.values(), 0, None);
    wrap_shallow(it, name!("<contextmanager>"), f)
}

/// Model calling a `@contextmanager` function: run its body and wrap the yielded value.
fn call_contextmanager(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let yielded = it.apply(a.p(0), a.from(1), a.star, &a.kws, a.kwstar, site);
    wrap_shallow(it, name!("<ctx_value>"), yielded)
}

/// Model entering a context manager wrapper: return the yielded or supplied value.
fn ctx_enter(_it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.p(0)
}

/// Model stdlib helpers and exits that invoke no user callbacks and return nothing callable.
fn noop(_it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    EMPTY
}

/// Model `@contextlib.asynccontextmanager`: wrap an async generator as an async CM factory.
fn asynccontextmanager(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let f = a.get(it.values(), 0, None);
    wrap_shallow(it, name!("<asynccontextmanager>"), f)
}

/// Model calling an `@asynccontextmanager` function: run its body and wrap the yielded value.
fn call_asynccontextmanager(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let yielded = it.apply(a.p(0), a.from(1), a.star, &a.kws, a.kwstar, site);
    wrap_shallow(it, name!("<actx_value>"), yielded)
}

/// Model `contextlib.closing(thing)`: return a context manager that calls `thing.close()`.
fn closing(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let x = a.get(it.values(), 0, None);
    wrap_shallow(it, name!("<closing>"), x)
}

/// Model exiting `closing(thing)`: call `thing.close()`.
fn closing_exit(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    it.invoke_dunder(a.p(0), name!("close"), &[], site);
    EMPTY
}

/// Model `contextlib.aclosing(thing)`: return an async CM that calls `thing.aclose()`.
fn aclosing(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let x = a.get(it.values(), 0, None);
    wrap_shallow(it, name!("<aclosing>"), x)
}

/// Model exiting `aclosing(thing)`: call `thing.aclose()`.
fn aclosing_exit(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    it.invoke_dunder(a.p(0), name!("aclose"), &[], site);
    EMPTY
}

/// Model `contextlib.nullcontext(enter_result=None)`: yield `enter_result` on enter.
fn nullcontext(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let x = a.get(it.values(), 0, Some(name!("enter_result")));
    wrap_shallow(it, name!("<nullcontext>"), x)
}

/// Store callback `fun` (wrapped as a partial if arguments are given) into `boxes`.
fn store_callback(
    it: &mut dyn Host,
    boxes: &[TokId],
    fun: Vals,
    pos: &[Vals],
    star: Option<Vals>,
    kws: &Kws,
    kwstar: Option<Vals>,
) -> Vals {
    if fun == EMPTY || it.readonly() {
        return fun;
    }
    let stored = if !pos.is_empty() || star.is_some() || !kws.is_empty() || kwstar.is_some() {
        make_partial(it.values(), fun, pos, star, kws, kwstar, name!("<partial>"), name!("<partial*>"))
    } else {
        fun
    };
    for &b in boxes {
        it.state().add_heap(b, stored);
    }
    fun
}

/// Store `obj` into `cm_boxes` (the tokens that define `dunder`) and the rest into `cb_boxes`
/// (as 3-argument exit functions).
fn push_exit(it: &mut dyn Host, cb_boxes: &[TokId], cm_boxes: &[TokId], obj: Vals, dunder: Name) -> Vals {
    if obj == EMPTY || it.readonly() {
        return obj;
    }
    let mut cms = Vec::new();
    let mut cbs = Vec::new();
    for &t in it.values().toks(obj).iter() {
        let is_cm = match it.values().get(t).clone() {
            Token::Instance { cls, .. } => it.state().lookup_class_attr(cls, dunder, None) != EMPTY,
            Token::Builtin { name, .. } => STUBS.contains(Name::new(&format!("{name}.{dunder}"))),
            _ => false,
        };
        if is_cm { cms.push(t) } else { cbs.push(t) }
    }
    if !cms.is_empty() {
        let cm_vals = it.values().of(cms);
        for &b in cm_boxes {
            it.state().add_heap(b, cm_vals);
        }
    }
    if !cbs.is_empty() {
        let v = it.values();
        let fns = v.of(cbs);
        let cb_vals = make_partial(
            v,
            fns,
            &[EMPTY, EMPTY, EMPTY],
            None,
            &Kws::default(),
            None,
            name!("<partial>"),
            name!("<partial*>"),
        );
        for &b in cb_boxes {
            it.state().add_heap(b, cb_vals);
        }
    }
    obj
}

/// Return the value holding a stack token `name` that binds one box value per kind in `kinds`,
/// all allocated at `site`.
fn new_stack(it: &mut dyn Host, name: Name, kinds: &[Name], site: Site) -> Vals {
    let v = it.values();
    let alloc = Alloc::of(&site);
    let bound: Vec<Vals> = kinds
        .iter()
        .map(|&k| {
            let b = v.tok(Token::Box { kind: k, alloc });
            v.single(b)
        })
        .collect();
    builtin_val(v, name, bound)
}

/// Return the value holding a stack token `name` that binds, for each kind, the boxes of that
/// kind in the matching argument.
fn same_stack(it: &mut dyn Host, a: &Args, name: Name, kinds: &[Name]) -> Vals {
    let v = it.values();
    let bound: Vec<Vals> = kinds
        .iter()
        .enumerate()
        .map(|(i, &k)| {
            let bs = boxes_of(v, a.p(i), k);
            v.of(bs)
        })
        .collect();
    builtin_val(v, name, bound)
}

/// The box kinds of an `ExitStack`.
fn sync_kinds() -> [Name; 2] {
    [name!("exit_cb"), name!("exit_cm")]
}

/// The box kinds of an `AsyncExitStack`.
fn async_kinds() -> [Name; 4] {
    [name!("exit_cb"), name!("exit_cm"), name!("aexit_cb"), name!("aexit_cm")]
}

/// Model `contextlib.ExitStack()`: allocate callback and context-manager boxes.
fn exit_stack(it: &mut dyn Host, _a: &Args, site: Site) -> Vals {
    new_stack(it, name!("<exit_stack>"), &sync_kinds(), site)
}

/// Model `ExitStack.__enter__` and `pop_all`: return the stack token itself.
fn exit_stack_self(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    same_stack(it, a, name!("<exit_stack>"), &sync_kinds())
}

/// Model `stack.callback(fn, *args, **kws)`: register `fn` to run on exit and return `fn`.
fn exit_stack_callback(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let boxes = boxes_of(it.values(), a.p(0), name!("exit_cb"));
    let f = a.get(it.values(), 2, None);
    store_callback(it, &boxes, f, a.from(3), a.star, &a.kws, a.kwstar)
}

/// Call `enter` on context manager `cm` and remember `cm` in the `kind` boxes of argument `at`.
fn enter_context(it: &mut dyn Host, a: &Args, site: Site, cm: Vals, enter: Name, at: usize, kind: Name) -> Vals {
    let (found, entered) = it.invoke_dunder(cm, enter, &[], site);
    if cm != EMPTY && !it.readonly() {
        for b in boxes_of(it.values(), a.p(at), kind) {
            it.state().add_heap(b, cm);
        }
    }
    if found { entered } else { cm }
}

/// Model `stack.enter_context(cm)`: call `cm.__enter__`, remember `cm` for `__exit__`.
fn exit_stack_enter_context(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let cm = a.get(it.values(), 2, None);
    enter_context(it, a, site, cm, name!("__enter__"), 1, name!("exit_cm"))
}

/// Model `stack.push(exit)`: register a context manager or 3-argument exit callback.
fn exit_stack_push(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let cb = boxes_of(it.values(), a.p(0), name!("exit_cb"));
    let cm = boxes_of(it.values(), a.p(1), name!("exit_cm"));
    let obj = a.get(it.values(), 2, None);
    push_exit(it, &cb, &cm, obj, name!("__exit__"))
}

/// Model `ExitStack.__exit__` and `close`: invoke all registered callbacks and context exits.
fn exit_stack_exit(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    call(it, a.p(0), &[], site, None);
    it.invoke_dunder(a.p(1), name!("__exit__"), &[EMPTY, EMPTY, EMPTY], site);
    EMPTY
}

/// Model `contextlib.AsyncExitStack()`: allocate sync and async callback/CM boxes.
fn async_exit_stack(it: &mut dyn Host, _a: &Args, site: Site) -> Vals {
    new_stack(it, name!("<async_exit_stack>"), &async_kinds(), site)
}

/// Model entering or `pop_all` on `AsyncExitStack`: return the stack token itself.
fn async_exit_stack_self(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    same_stack(it, a, name!("<async_exit_stack>"), &async_kinds())
}

/// Model `AsyncExitStack.callback(fn, *args, **kws)`: register a sync exit callback.
fn aexit_stack_callback(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let boxes = boxes_of(it.values(), a.p(0), name!("exit_cb"));
    let f = a.get(it.values(), 4, None);
    store_callback(it, &boxes, f, a.from(5), a.star, &a.kws, a.kwstar)
}

/// Model `AsyncExitStack.push_async_callback(fn, *args, **kws)`: register an async callback.
fn aexit_stack_async_callback(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let boxes = boxes_of(it.values(), a.p(2), name!("aexit_cb"));
    let f = a.get(it.values(), 4, None);
    store_callback(it, &boxes, f, a.from(5), a.star, &a.kws, a.kwstar)
}

/// Model `AsyncExitStack.enter_context(cm)`: call `__enter__` and remember `cm`.
fn aexit_stack_enter_context(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let cm = a.get(it.values(), 4, None);
    enter_context(it, a, site, cm, name!("__enter__"), 1, name!("exit_cm"))
}

/// Model `AsyncExitStack.enter_async_context(cm)`: call `__aenter__` and remember `cm`.
fn aexit_stack_enter_async_context(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let cm = a.get(it.values(), 4, None);
    enter_context(it, a, site, cm, name!("__aenter__"), 3, name!("aexit_cm"))
}

/// Model `AsyncExitStack.push(exit)`: register a sync context manager or exit callback.
fn aexit_stack_push(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let cb = boxes_of(it.values(), a.p(0), name!("exit_cb"));
    let cm = boxes_of(it.values(), a.p(1), name!("exit_cm"));
    let obj = a.get(it.values(), 4, None);
    push_exit(it, &cb, &cm, obj, name!("__exit__"))
}

/// Model `AsyncExitStack.push_async_exit(exit)`: register an async CM or exit callback.
fn aexit_stack_push_async(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let cb = boxes_of(it.values(), a.p(2), name!("aexit_cb"));
    let cm = boxes_of(it.values(), a.p(3), name!("aexit_cm"));
    let obj = a.get(it.values(), 4, None);
    push_exit(it, &cb, &cm, obj, name!("__aexit__"))
}

/// Model synchronous exit on `AsyncExitStack`: invoke sync callbacks and context exits.
fn aexit_stack_sync_exit(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    call(it, a.p(0), &[], site, None);
    it.invoke_dunder(a.p(1), name!("__exit__"), &[EMPTY, EMPTY, EMPTY], site);
    EMPTY
}

/// Model async exit on `AsyncExitStack`: invoke all sync and async callbacks and exits.
fn aexit_stack_async_exit(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let cbs = it.values().union(a.p(0), a.p(2));
    call(it, cbs, &[], site, None);
    it.invoke_dunder(a.p(1), name!("__exit__"), &[EMPTY, EMPTY, EMPTY], site);
    it.invoke_dunder(a.p(3), name!("__aexit__"), &[EMPTY, EMPTY, EMPTY], site);
    EMPTY
}
