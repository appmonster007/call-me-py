//! Stubs for `functools`: reduce, the caching and wrapping decorators, partials,
//! `cached_property`, `total_ordering`, `cmp_to_key` and `singledispatch`.

use crate::model::{Alloc, EMPTY, Site, TokId, Token, Vals, Values};
use crate::name;
use crate::syntax::Name;

use super::objects::property_fn;
use super::registry::{
    Args, Host, Kws, MAX_PARTIAL_ARGS, Registry, boxes_of, builtin, builtin_val, call, fold, shallow,
};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["functools.reduce"], reduce);
    r.add(&["<identity>"], identity);
    r.add(&["functools.cache", "functools.lru_cache"], cache);
    r.add(&["functools.wraps"], wraps);
    r.add(&["functools.update_wrapper"], update_wrapper);
    r.add(&["functools.partial"], partial);
    r.add(&["functools.partialmethod"], partialmethod);
    r.add(&["<partial>", "<partialmethod>"], call_partial);
    r.add(&["<partial*>", "<partialmethod*>"], call_partial_widened);
    r.add(&["functools.cached_property"], cached_property);
    r.add(&["functools.total_ordering"], total_ordering);
    r.add(&["functools.cmp_to_key"], cmp_to_key);
    r.add(&["<cmp_to_key>"], call_cmp_to_key);
    r.add(
        &[
            "<cmp_key>.__lt__",
            "<cmp_key>.__gt__",
            "<cmp_key>.__le__",
            "<cmp_key>.__ge__",
            "<cmp_key>.__eq__",
            "<cmp_key>.__ne__",
        ],
        cmp_key_op,
    );
    r.add(&["functools.singledispatch"], singledispatch);
    r.add(&["functools.singledispatchmethod"], singledispatchmethod);
    r.add(&["<singledispatch>", "<singledispatchmethod>"], call_singledispatch);
    r.add(&["<singledispatch>.register", "<singledispatchmethod>.register"], singledispatch_register);
    r.add(&["<singledispatch_deco>"], singledispatch_deco);
    r.add(&["<singledispatch>.dispatch", "<singledispatchmethod>.dispatch"], singledispatch_dispatch);
}

/// Model `functools.reduce(f, xs, initial)`: fold `f` over the elements (see `fold`).
fn reduce(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = it.values();
    let elems = a.get(v, 1, None);
    let initial = a.get(v, 2, Some(name!("initial")));
    let acc = v.union(elems, initial);
    let f = a.get(v, 0, None);
    fold(it, f, acc, elems, site)
}

/// Return the value holding the `<identity>` decorator.
fn identity_val(v: &mut Values) -> Vals {
    builtin_val(v, name!("<identity>"), Vec::new())
}

/// Return the first argument: the decorator that `wraps(f)` and `lru_cache(...)` return.
fn identity(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.get(it.values(), 0, None)
}

/// Model `functools.cache` and `lru_cache`, which return the decorated function itself.
///
/// `cache(f)` and `lru_cache(f)` return `f`. Called with options only, as in
/// `lru_cache(maxsize=32)`, they return the identity decorator.
fn cache(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let f = a.p(0);
    if f == EMPTY { identity_val(it.values()) } else { f }
}

/// Model `functools.wraps(f)`: return the identity decorator, which keeps the wrapper.
fn wraps(it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    identity_val(it.values())
}

/// Model `functools.update_wrapper(wrapper, wrapped)`: return `wrapper`.
fn update_wrapper(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.get(it.values(), 0, None)
}

/// Build bounded partial tokens (`name` or widened `wide_name`) for each callable in `fn_vals`.
///
/// A partial of a partial is flattened into one. With more than `MAX_PARTIAL_ARGS` bound
/// arguments, or `*` or `**` at construction, the order of arguments is lost and the token is
/// widened to `wide_name`. Too deep argument tokens are dropped (`shallow`).
#[allow(clippy::too_many_arguments)]
pub fn make_partial(
    v: &mut Values,
    fn_vals: Vals,
    pos: &[Vals],
    star: Option<Vals>,
    kws: &Kws,
    kwstar: Option<Vals>,
    name: Name,
    wide_name: Name,
) -> Vals {
    let rest: Vec<Vals> = pos.iter().map(|&p| shallow(v, p)).collect();
    let kw_map: Vec<(Name, Vals)> = kws.iter().map(|(&k, &x)| (k, shallow(v, x))).collect();
    let se = v.union(star.unwrap_or(EMPTY), kwstar.unwrap_or(EMPTY));
    let extra = shallow(v, se);
    let partials = [name!("<partial>"), name!("<partial*>"), name!("<partialmethod>"), name!("<partialmethod*>")];
    let mut out: Vec<TokId> = Vec::new();
    for &t in v.toks(fn_vals).iter() {
        let (f, args, mut kw, widened) = match v.get(t).clone() {
            Token::Builtin { name: n, bound, bound_kw } if partials.contains(&n) => {
                let mut args: Vec<Vals> = bound.get(1..).unwrap_or(&[]).to_vec();
                args.extend(&rest);
                let mut kw: Vec<(Name, Vals)> = bound_kw.to_vec();
                for &(k, x) in &kw_map {
                    match kw.iter_mut().find(|(kk, _)| *kk == k) {
                        Some(slot) => slot.1 = v.union(slot.1, x),
                        None => kw.push((k, x)),
                    }
                }
                (bound.first().copied().unwrap_or(EMPTY), args, kw, n.ends_with("*>"))
            }
            _ => (v.single(t), rest.clone(), kw_map.clone(), false),
        };
        if widened || extra != EMPTY || args.len() > MAX_PARTIAL_ARGS {
            let a = v.union_all(args.iter().copied());
            let k = v.union_all(kw.iter().map(|(_, x)| *x));
            let ak = v.union(a, k);
            let everything = v.union(ak, extra);
            out.push(builtin(v, wide_name, vec![f, everything], Vec::new()));
        } else {
            let mut bound = vec![f];
            bound.extend(args);
            kw.sort_by_key(|x| x.0);
            out.push(builtin(v, name, bound, kw));
        }
    }
    v.of(out)
}

/// Model `functools.partial(f, *args, **kws)`: return a `<partial>` token per function.
fn partial(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let f = a.get(v, 0, None);
    make_partial(v, f, a.from(1), a.star, &a.kws, a.kwstar, name!("<partial>"), name!("<partial*>"))
}

/// Model `functools.partialmethod(f, *args, **kws)`: descriptor binding `self` before `args`.
fn partialmethod(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let f = a.get(v, 0, None);
    make_partial(v, f, a.from(1), a.star, &a.kws, a.kwstar, name!("<partialmethod>"), name!("<partialmethod*>"))
}

/// Call a `<partial>` token: its function gets the bound arguments, then the call's own.
fn call_partial(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    // a.pos = [f, *bound args, *call args]; bound keywords are already merged into a.kws
    it.apply(a.p(0), a.from(1), a.star, &a.kws, a.kwstar, site)
}

/// Call a `<partial*>` token: every bound and call argument goes to the function as `*args`.
fn call_partial_widened(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let rest = a.rest(it.values(), 1);
    it.apply(a.p(0), &[], Some(rest), &a.kws, a.kwstar, site)
}

/// Model `functools.cached_property(f)`: non-data descriptor calling `f(self)` on read.
fn cached_property(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let f = a.get(v, 0, None);
    let fget = property_fn(v, f);
    let t = v.tok(Token::Property { fget, fset: EMPTY, fdel: EMPTY });
    v.single(t)
}

/// Model `@functools.total_ordering`: return the decorated class unchanged.
fn total_ordering(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.get(it.values(), 0, None)
}

/// Model `functools.cmp_to_key(mycmp)`: return a key function wrapping `mycmp`.
fn cmp_to_key(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let f = a.get(v, 0, None);
    let s = shallow(v, f);
    builtin_val(v, name!("<cmp_to_key>"), vec![s])
}

/// Model calling a `cmp_to_key` wrapper on an element: return a comparable key object.
fn call_cmp_to_key(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let e = a.get(v, 1, None);
    let s = shallow(v, e);
    builtin_val(v, name!("<cmp_key>"), vec![a.p(0), s])
}

/// Model comparing two `cmp_to_key` keys: call the underlying comparison function.
fn cmp_key_op(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let elem = a.p(1);
    call(it, a.p(0), &[elem, elem], site, None);
    EMPTY
}

/// Return True if `vals` looks like a registered implementation rather than a type argument.
fn is_dispatch_fn(v: &Values, vals: Vals) -> bool {
    v.slice(vals).iter().any(|&t| match v.get(t) {
        Token::Closure { .. } | Token::BoundMethod { .. } => true,
        Token::Builtin { name, .. } => *name == name!("<partial>") || *name == name!("<partial*>"),
        _ => false,
    })
}

/// Allocate a `singledispatch` registry box holding the default function, and return `kind`.
fn make_singledispatch(it: &mut dyn Host, a: &Args, site: Site, kind: Name) -> Vals {
    let v = it.values();
    let b = v.tok(Token::Box { kind: name!("singledispatch"), alloc: Alloc::of(&site) });
    let f = a.get(v, 0, None);
    if f != EMPTY && !it.readonly() {
        it.state().add_heap(b, f);
    }
    let v = it.values();
    let boxed = v.single(b);
    builtin_val(v, kind, vec![boxed])
}

/// Model `@functools.singledispatch`: wrap the default function in a dispatch registry.
fn singledispatch(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    make_singledispatch(it, a, site, name!("<singledispatch>"))
}

/// Model `@functools.singledispatchmethod`: descriptor wrapping a method dispatch registry.
fn singledispatchmethod(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    make_singledispatch(it, a, site, name!("<singledispatchmethod>"))
}

/// Model calling a `singledispatch` function: call every registered implementation.
fn call_singledispatch(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    it.apply(a.p(0), a.from(1), a.star, &a.kws, a.kwstar, site)
}

/// Join `impl_` into every registry box in `boxes`, unless in read-only mode.
fn store_impl(it: &mut dyn Host, boxes: &[TokId], impl_: Vals) {
    if impl_ != EMPTY && !it.readonly() {
        for &b in boxes {
            it.state().add_heap(b, impl_);
        }
    }
}

/// Model `fn.register(cls, impl)`, `@fn.register(cls)` and bare `@fn.register`.
fn singledispatch_register(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let boxes = boxes_of(it.values(), a.p(0), name!("singledispatch"));
    if a.pos.len() >= 3 || a.kws.contains_key(&name!("func")) {
        let impl_ = a.get(it.values(), 2, Some(name!("func")));
        store_impl(it, &boxes, impl_);
        return impl_;
    }
    let arg = a.get(it.values(), 1, None);
    if is_dispatch_fn(it.values(), arg) {
        store_impl(it, &boxes, arg);
        return arg;
    }
    let v = it.values();
    let bv = v.of(boxes);
    builtin_val(v, name!("<singledispatch_deco>"), vec![bv])
}

/// Model applying `@fn.register(cls)` to an implementation function: store and return it.
fn singledispatch_deco(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let impl_ = a.get(it.values(), 1, None);
    let boxes = boxes_of(it.values(), a.p(0), name!("singledispatch"));
    store_impl(it, &boxes, impl_);
    impl_
}

/// Model `fn.dispatch(cls)`: return all registered implementations.
fn singledispatch_dispatch(_it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.p(0)
}
