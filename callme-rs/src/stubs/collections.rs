//! Stubs for `collections`: defaultdict, deque, the dict- and list-like classes, ChainMap and
//! namedtuple.

use crate::model::{Alloc, EMPTY, Site, Token, Vals};
use crate::name;
use crate::syntax::Name;

use super::registry::{Args, Host, Registry, boxes_of, builtin, builtin_val, call, shallow};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["collections.defaultdict"], defaultdict);
    r.add(&["<defaultdict>.__getitem__"], defaultdict_getitem);
    r.add(&["<defaultdict>.default_factory"], defaultdict_factory);
    r.add(&["collections.deque"], deque);
    r.add(&["collections.OrderedDict", "collections.Counter", "collections.UserDict"], dict_like);
    r.add(&["collections.UserList"], user_list);
    r.add(&["collections.ChainMap"], chainmap);
    r.add(&["collections.namedtuple"], namedtuple);
    r.add(&["<namedtuple_cls>"], call_namedtuple);
    r.add(&["<namedtuple_cls>._make"], namedtuple_make);
}

/// Return `vals` plus a new container token of `kind` allocated at `site`.
fn with_box(it: &mut dyn Host, vals: Vals, kind: Name, site: Site) -> Vals {
    let v = it.values();
    let b = v.tok(Token::Box { kind, alloc: Alloc::of(&site) });
    v.add(vals, b)
}

/// Model `collections.defaultdict(default_factory=None, ...)`: dict box plus factory hook.
fn defaultdict(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = it.values();
    let b = v.tok(Token::Box { kind: name!("dict"), alloc: Alloc::of(&site) });
    let factory = if a.pos.is_empty() { EMPTY } else { shallow(v, a.pos[0]) };
    let rest = a.rest(v, 1);
    let iterated = it.iterate(rest, site, false);
    let v = it.values();
    let kv = v.union_all(a.kws.values().copied());
    let e1 = v.union(iterated, kv);
    let elems = v.union(e1, a.kwstar.unwrap_or(EMPTY));
    let with = v.add(elems, b);
    if factory == EMPTY {
        return with;
    }
    let bv = v.single(b);
    let hook = builtin(v, name!("<defaultdict>"), vec![factory, bv], Vec::new());
    v.add(with, hook)
}

/// Model `d[k]` on a `defaultdict`: call `default_factory()`, store the result, return it.
fn defaultdict_getitem(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let res = call(it, a.p(0), &[], site, None);
    if res != EMPTY && !it.readonly() {
        for b in boxes_of(it.values(), a.p(1), name!("dict")) {
            it.state().add_heap(b, res);
        }
    }
    res
}

/// Model calling `d.default_factory(...)`: invoke the factory stored on the `defaultdict`.
fn defaultdict_factory(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    it.apply(a.p(0), a.from(2), a.star, &a.kws, a.kwstar, site)
}

/// Model `collections.deque(iterable=(), maxlen=None)`: elements plus a mutable deque box.
fn deque(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let x = a.get(it.values(), 0, None);
    let elems = it.iterate(x, site, false);
    with_box(it, elems, name!("deque"), site)
}

/// Model dict-like classes in `collections`: elements plus a mutable dict box.
fn dict_like(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let all = a.all(it.values());
    let elems = it.iterate(all, site, false);
    with_box(it, elems, name!("dict"), site)
}

/// Model `collections.UserList`: elements plus a mutable list box.
fn user_list(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let all = a.all(it.values());
    let elems = it.iterate(all, site, false);
    with_box(it, elems, name!("list"), site)
}

/// Model `collections.ChainMap(*maps)`: union of all input maps plus a dict box.
fn chainmap(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let maps = a.rest(it.values(), 0);
    with_box(it, maps, name!("dict"), site)
}

/// Model `collections.namedtuple(...)`: return a tuple-like constructor token.
fn namedtuple(it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    builtin_val(it.values(), name!("<namedtuple_cls>"), Vec::new())
}

/// Model instantiating a `namedtuple` class: collapse its field values like a tuple.
fn call_namedtuple(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.all(it.values())
}

/// Model `NT._make(iterable)`: return the iterable's elements like a tuple.
fn namedtuple_make(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let x = a.get(it.values(), 0, None);
    it.iterate(x, site, false)
}
