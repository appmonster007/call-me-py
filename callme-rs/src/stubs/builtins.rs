//! Stubs for builtin functions that call back into the program or call special methods.

use crate::model::{EMPTY, Site, Vals};
use crate::name;

use super::registry::{Args, Host, Registry, call};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["map"], map);
    r.add(&["filter", "itertools.filterfalse", "itertools.takewhile", "itertools.dropwhile"], filter);
    r.add(&["sorted"], sorted);
    r.add(&["min", "max"], minmax);
    r.add(&["list.sort"], list_sort);
    r.add(&["dict.get"], dict_get);
    r.add(&["len"], len);
    r.add(&["str"], str_);
    r.add(&["repr"], repr);
    r.add(&["bool"], bool_);
    r.add(&["hash"], hash);
    r.add(&["iter"], iter);
    r.add(&["aiter"], aiter);
    r.add(&["next"], next);
    r.add(&["anext"], anext);
    r.add(&["any", "all"], any_all);
    r.add(&["sum"], sum);
    r.add(&["abs"], abs);
    r.add(&["round"], round);
    r.add(&["format"], format);
    r.add(&["int"], int);
    r.add(&["float"], float);
    r.add(&["complex"], complex);
    r.add(&["bytes"], bytes);
    r.add(&["reversed"], reversed);
}

/// Model `map(f, *iterables)`: call `f` with an element of each iterable; return its results.
fn map(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let f = a.get(it.values(), 0, None);
    if a.star.is_some() {
        // unknown number of iterables
        let rest = a.rest(it.values(), 1);
        return call(it, f, &[], site, Some(rest));
    }
    call(it, f, a.from(1), site, None)
}

/// Model `filter(f, xs)` and its itertools variants: call `f` on the elements; return them.
fn filter(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let elems = a.get(it.values(), 1, None);
    let f = a.get(it.values(), 0, None);
    call(it, f, &[elems], site, None);
    elems
}

/// Call `key` on `elems` and compare the keys with `__lt__`; without a key, compare `elems`.
fn compare_sorted(it: &mut dyn Host, elems: Vals, key: Vals, site: Site) {
    if key == EMPTY {
        it.invoke_dunder(elems, name!("__lt__"), &[elems], site);
    } else {
        let keys = call(it, key, &[elems], site, None);
        it.invoke_dunder(keys, name!("__lt__"), &[keys], site);
    }
}

/// Model `sorted(xs, key=k)`: call `k` on the elements, then compare the keys with `__lt__`.
///
/// Without a key the elements themselves are compared. Return the elements.
fn sorted(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let elems = a.get(it.values(), 0, None);
    let key = a.kw(it.values(), name!("key"));
    compare_sorted(it, elems, key, site);
    elems
}

/// Model `min` and `max`: call `key` on the elements and compare the keys with `__lt__`.
///
/// The elements come from the single iterable argument, or from all positional arguments
/// when there are several. Return the elements, plus the `default` keyword.
fn minmax(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let elems = if a.pos.len() == 1 && a.star.is_none() { a.pos[0] } else { a.rest(it.values(), 0) };
    let key = a.kw(it.values(), name!("key"));
    compare_sorted(it, elems, key, site);
    let default = a.kw(it.values(), name!("default"));
    it.values().union(elems, default)
}

/// Model `xs.sort(key=k)`: call `k` on the elements and compare the keys; return nothing.
fn list_sort(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let elems = a.get(it.values(), 0, None);
    let key = a.kw(it.values(), name!("key"));
    compare_sorted(it, elems, key, site);
    EMPTY
}

/// Model `d.get(k, default)`: return the dictionary's values plus `default`.
fn dict_get(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let d = a.get(it.values(), 0, None);
    let default = a.get(it.values(), 2, Some(name!("default")));
    it.values().union(d, default)
}

/// Call `name` on argument 0 and return nothing callable.
fn dunder_only(it: &mut dyn Host, a: &Args, site: Site, name: crate::syntax::Name) -> Vals {
    let v = a.get(it.values(), 0, None);
    it.invoke_dunder(v, name, &[], site);
    EMPTY
}

/// Call `first` on argument 0, or `second` if no class defines `first`; return nothing.
fn dunder_or(it: &mut dyn Host, a: &Args, site: Site, first: crate::syntax::Name, second: crate::syntax::Name) -> Vals {
    let v = a.get(it.values(), 0, None);
    let (found, _) = it.invoke_dunder(v, first, &[], site);
    if !found {
        it.invoke_dunder(v, second, &[], site);
    }
    EMPTY
}

/// Model `len(x)`: call `x.__len__`; return nothing callable.
fn len(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_only(it, a, site, name!("__len__"))
}

/// Model `str(x)`: call `x.__str__`, or `x.__repr__` without it; return nothing callable.
fn str_(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_or(it, a, site, name!("__str__"), name!("__repr__"))
}

/// Model `repr(x)`: call `x.__repr__`; return nothing callable.
fn repr(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_only(it, a, site, name!("__repr__"))
}

/// Model `bool(x)`: call `x.__bool__`, or `x.__len__` without it; return nothing callable.
fn bool_(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_or(it, a, site, name!("__bool__"), name!("__len__"))
}

/// Model `hash(x)`: call `x.__hash__`; return nothing callable.
fn hash(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_only(it, a, site, name!("__hash__"))
}

/// Model `iter(x)` and `iter(callable, sentinel)`.
///
/// The one-argument form returns what `x.__iter__` returns, or `x` itself for non-instances.
/// The two-argument form calls the callable with no arguments and returns its results.
fn iter(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    if a.pos.len() >= 2 {
        return call(it, a.pos[0], &[], site, None);
    }
    let v = a.get(it.values(), 0, None);
    let (found, out) = it.invoke_dunder(v, name!("__iter__"), &[], site);
    if found { out } else { v }
}

/// Model `aiter(x)`: return what `x.__aiter__` returns, or `x` itself for non-instances.
fn aiter(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = a.get(it.values(), 0, None);
    let (found, out) = it.invoke_dunder(v, name!("__aiter__"), &[], site);
    if found { out } else { v }
}

/// Model `next(it, default)`: return what `it.__next__` returns, plus `default`.
///
/// Values without `__next__`, such as collapsed containers, stand for their own elements and
/// are returned as they are.
fn next(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = a.get(it.values(), 0, None);
    let default = a.get(it.values(), 1, None);
    let (found, out) = it.invoke_dunder(v, name!("__next__"), &[], site);
    it.values().union(if found { out } else { v }, default)
}

/// Model `anext(it, default)`: return what `it.__anext__` returns, plus `default`.
fn anext(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = a.get(it.values(), 0, None);
    let default = a.get(it.values(), 1, None);
    let (found, out) = it.invoke_dunder(v, name!("__anext__"), &[], site);
    it.values().union(if found { out } else { v }, default)
}

/// Model `any(xs)` and `all(xs)`: iterate `xs` and test truthiness of each element.
fn any_all(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let x = a.get(it.values(), 0, None);
    let elems = it.iterate(x, site, false);
    let (found, _) = it.invoke_dunder(elems, name!("__bool__"), &[], site);
    if !found {
        it.invoke_dunder(elems, name!("__len__"), &[], site);
    }
    EMPTY
}

/// Model `sum(xs, start=0)`: iterate `xs` for its iterator calls; return nothing callable.
fn sum(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let x = a.get(it.values(), 0, None);
    it.iterate(x, site, false);
    EMPTY
}

/// Model `abs(x)`: call `x.__abs__`; return nothing callable.
fn abs(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_only(it, a, site, name!("__abs__"))
}

/// Model `round(x, ndigits)`: call `x.__round__`; return nothing callable.
fn round(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_only(it, a, site, name!("__round__"))
}

/// Model `format(x, spec)`: call `__format__`, falling back to `__str__` then `__repr__`.
fn format(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = a.get(it.values(), 0, None);
    let (mut found, _) = it.invoke_dunder(v, name!("__format__"), &[EMPTY], site);
    if !found {
        found = it.invoke_dunder(v, name!("__str__"), &[], site).0;
    }
    if !found {
        it.invoke_dunder(v, name!("__repr__"), &[], site);
    }
    EMPTY
}

/// Model `int(x)`: call `x.__int__`, falling back to `x.__index__`; return nothing callable.
fn int(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_or(it, a, site, name!("__int__"), name!("__index__"))
}

/// Model `float(x)`: call `x.__float__`; return nothing callable.
fn float(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_only(it, a, site, name!("__float__"))
}

/// Model `complex(x)`: call `x.__complex__`; return nothing callable.
fn complex(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_only(it, a, site, name!("__complex__"))
}

/// Model `bytes(x)`: call `x.__bytes__`; return nothing callable.
fn bytes(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    dunder_only(it, a, site, name!("__bytes__"))
}

/// Model `reversed(x)`: call `x.__reversed__` and the `__next__` of what it returns.
///
/// Return the elements that `__next__` yields, or the reversed iterator when it yields
/// nothing known. Without `__reversed__`, return the elements of iterating `x`.
fn reversed(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = a.get(it.values(), 0, None);
    let (found, out) = it.invoke_dunder(v, name!("__reversed__"), &[], site);
    if found {
        let (_, elem) = it.invoke_dunder(out, name!("__next__"), &[], site);
        return if elem == EMPTY { out } else { elem };
    }
    it.iterate(v, site, false)
}
