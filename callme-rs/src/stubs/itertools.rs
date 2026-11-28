//! Stubs for `itertools` functions that call back into the program or pass elements on.

use crate::model::{EMPTY, Site, Vals};
use crate::name;

use super::registry::{Args, Host, Registry, call, fold};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["itertools.accumulate"], accumulate);
    r.add(&["itertools.starmap"], starmap);
    r.add(&["itertools.groupby"], groupby);
    r.add(&["itertools.chain.from_iterable"], chain_from_iterable);
    r.add(&["itertools.repeat"], repeat);
    r.add(&["itertools.compress"], compress);
    r.add(&["itertools.zip_longest"], zip_longest);
    r.add(&["itertools.product"], product);
    r.add(
        &[
            "itertools.permutations",
            "itertools.combinations",
            "itertools.combinations_with_replacement",
            "itertools.pairwise",
            "itertools.batched",
            "itertools.tee",
        ],
        iter_first,
    );
    r.add(&["itertools.count"], count);
}

/// Model `itertools.accumulate(xs, func, initial=...)`: fold `func` over the elements.
fn accumulate(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = it.values();
    let elems = a.get(v, 0, None);
    let initial = a.kw(v, name!("initial"));
    let acc = v.union(elems, initial);
    let f = a.get(v, 1, Some(name!("func")));
    fold(it, f, acc, elems, site)
}

/// Model `itertools.starmap(f, xs)`: call `f` with each element unpacked as its arguments.
fn starmap(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let f = a.get(it.values(), 0, None);
    let xs = a.get(it.values(), 1, None);
    call(it, f, &[], site, Some(xs))
}

/// Model `itertools.groupby(xs, key)`: call `key` on the elements; return the elements.
fn groupby(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let elems = a.get(it.values(), 0, None);
    let key = a.get(it.values(), 1, Some(name!("key")));
    call(it, key, &[elems], site, None);
    elems
}

/// Model `itertools.chain.from_iterable(iterables)`: flatten one level of iterables.
fn chain_from_iterable(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let x = a.get(it.values(), 0, None);
    let inner = it.iterate(x, site, false);
    it.iterate(inner, site, false)
}

/// Model `itertools.repeat(obj, times)`: yield `obj` itself without iterating it.
fn repeat(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    a.get(it.values(), 0, None)
}

/// Model `itertools.compress(data, selectors)`: iterate both and return elements of `data`.
fn compress(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let sel = a.get(it.values(), 1, Some(name!("selectors")));
    it.iterate(sel, site, false);
    let data = a.get(it.values(), 0, Some(name!("data")));
    it.iterate(data, site, false)
}

/// Model `itertools.zip_longest(*iterables, fillvalue=None)`: elements plus `fillvalue`.
fn zip_longest(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let rest = a.rest(it.values(), 0);
    let elems = it.iterate(rest, site, false);
    let fill = a.kw(it.values(), name!("fillvalue"));
    it.values().union(elems, fill)
}

/// Model `itertools.product(*iterables)`: return elements of all input iterables.
fn product(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let rest = a.rest(it.values(), 0);
    it.iterate(rest, site, false)
}

/// Model single-iterable itertools combinators: return the elements of the first argument.
fn iter_first(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let x = a.get(it.values(), 0, None);
    it.iterate(x, site, false)
}

/// Model `itertools.count(...)`: yields numbers, so returns nothing callable.
fn count(_it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    EMPTY
}
