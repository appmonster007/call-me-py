//! Stubs for tuple / zip / list / set / dict and similar constructors, container methods, and
//! `__import__`.

use crate::model::{Alloc, Site, Token, Vals};
use crate::name;
use crate::syntax::Name;

use super::registry::{Args, Host, Registry};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(
        &["tuple", "frozenset", "enumerate", "zip", "itertools.chain", "itertools.cycle", "itertools.islice"],
        passthrough,
    );
    r.add(&["list"], list);
    r.add(&["set"], set);
    r.add(&["dict"], dict);
    r.add(&["<elements>"], elements);
    r.add(&["__import__", "importlib.import_module"], import);
}

/// Model `tuple`, `zip`, `enumerate`, `chain` and the like: return every argument's elements.
fn passthrough(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let all = a.all(it.values());
    it.iterate(all, site, false)
}

/// Model `list(xs)`, `set(xs)` or `dict(...)`: the elements and a `Box` of `kind` for this call.
fn container(it: &mut dyn Host, a: &Args, site: Site, kind: Name) -> Vals {
    let all = a.all(it.values());
    let elems = it.iterate(all, site, false);
    let v = it.values();
    let b = v.tok(Token::Box { kind, alloc: Alloc::of(&site) });
    v.add(elems, b)
}

/// Model `list(xs)`.
fn list(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    container(it, a, site, name!("list"))
}

/// Model `set(xs)`.
fn set(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    container(it, a, site, name!("set"))
}

/// Model `dict(...)`.
fn dict(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    container(it, a, site, name!("dict"))
}

/// Model the element methods of containers: return the receiver's elements and any default.
///
/// Covers `d.values()`, `d.items()`, `d.keys()`, `xs.copy()`, `xs.pop(i)`, `d.pop(k, default)`
/// and `d.popitem()`; the receiver is the first argument.
fn elements(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let recv = a.get(v, 0, None); // receiver's elements (already dereferenced)
    let defaults = v.union_all(a.from(2).iter().copied());
    v.union(recv, defaults)
}

/// Model `__import__(name)` and `importlib.import_module(name)`: return any program module.
///
/// Strings are not tracked, so the name is unknown: every module of the program may come
/// back, and each one runs (`Host::import_any`). Modules outside the program are not found.
fn import(it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    it.import_any()
}
