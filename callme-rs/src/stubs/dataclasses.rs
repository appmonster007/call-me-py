//! Stubs for `dataclasses`: the decorator, `field`, the generated `__init__`, and helpers.

use crate::model::{Alloc, EMPTY, Site, TokId, Token, Vals};
use crate::name;

use super::registry::{Args, Host, Kws, Registry, builtin_val, call, shallow};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["dataclasses.dataclass"], dataclass);
    r.add(&["<dataclass>"], dataclass_deco);
    r.add(&["dataclasses.field"], field);
    r.add(&["<dataclass_init>"], dataclass_init);
    r.add(&["dataclasses.replace"], replace);
    r.add(&["dataclasses.asdict"], asdict);
    r.add(&["dataclasses.astuple"], astuple);
    r.add(&["dataclasses.make_dataclass"], make_dataclass);
}

/// Give each class in `cls_vals` the generated dataclass `__init__`, and return the classes.
///
/// A class that defines its own `__init__` keeps it. Nothing is written in read-only mode.
fn apply_dataclass(it: &mut dyn Host, cls_vals: Vals) -> Vals {
    for &t in it.values().toks(cls_vals).iter() {
        if let Token::ClassObj(c) = *it.values().get(t)
            && !it.readonly()
            && !it.state().has_own_class_attr(c, name!("__init__"))
        {
            let init = builtin_val(it.values(), name!("<dataclass_init>"), Vec::new());
            it.state().add_class_attr(c, name!("__init__"), init);
        }
    }
    cls_vals
}

/// Model `dataclasses.dataclass` used bare or with options.
///
/// `@dataclass` applied to a class turns it into a dataclass at once. `@dataclass(...)` with
/// options returns the `<dataclass>` decorator, which does the same when it is applied.
fn dataclass(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let cls = a.p(0);
    let v = it.values();
    if v.slice(cls).iter().any(|&t| matches!(v.get(t), Token::ClassObj(_))) {
        return apply_dataclass(it, cls);
    }
    builtin_val(it.values(), name!("<dataclass>"), Vec::new())
}

/// Apply the `<dataclass>` decorator returned by `dataclass(...)` to a class.
fn dataclass_deco(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let c = a.get(it.values(), 0, None);
    apply_dataclass(it, c)
}

/// Model `dataclasses.field(default=..., default_factory=...)`: record factory and default.
fn field(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let f = a.kw(v, name!("default_factory"));
    let factory = shallow(v, f);
    let d = a.kw(v, name!("default"));
    let default = shallow(v, d);
    if factory == EMPTY && default == EMPTY {
        return EMPTY;
    }
    builtin_val(v, name!("<dataclass_field>"), vec![factory, default])
}

/// Model a generated dataclass `__init__`: run field factories, then call `__post_init__`.
///
/// Each `field(default_factory=...)` in the class's MRO has its factory invoked and the result
/// joined into that instance attribute. `__post_init__` is then bound as Python would bind it:
/// to the class for a classmethod, unbound for a staticmethod, and to the instance otherwise.
fn dataclass_init(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let objs = a.get(it.values(), 0, None);
    for &obj in it.values().toks(objs).iter() {
        let Token::Instance { cls, .. } = *it.values().get(obj) else {
            continue;
        };
        let mut seen = Vec::new();
        for &c in it.state().mro(cls).iter() {
            for (attr, toks) in it.state().class_attr_items(c) {
                if seen.contains(&attr) {
                    continue;
                }
                seen.push(attr);
                for &tok in it.values().toks(toks).iter() {
                    let Token::Builtin { name, bound, .. } = it.values().get(tok).clone() else {
                        continue;
                    };
                    if name != name!("<dataclass_field>") {
                        continue;
                    }
                    let made = call(it, bound[0], &[], site, None);
                    let val = it.values().union(made, bound[1]);
                    if val != EMPTY && !it.readonly() {
                        it.state().add_inst_attr(obj, attr, val);
                    }
                }
            }
        }
        let post = it.state().lookup_class_attr(cls, name!("__post_init__"), None);
        if post != EMPTY {
            let v = it.values();
            let cls_obj = v.tok(Token::ClassObj(cls));
            let mut bound: Vec<TokId> = Vec::new();
            for &t in v.toks(post).iter() {
                let b = match *v.get(t) {
                    Token::ClassMethod(f) => v.tok(Token::BoundMethod { func: f, receiver: cls_obj }),
                    Token::StaticMethod(f) => f,
                    _ => v.tok(Token::BoundMethod { func: t, receiver: obj }),
                };
                bound.push(b);
            }
            let callee = v.of(bound);
            it.apply(callee, &[], None, &Kws::default(), None, site);
        }
    }
    EMPTY
}

/// Model `dataclasses.replace(obj, **changes)`: re-instantiate `obj`'s class with updates.
fn replace(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let objs = a.get(it.values(), 0, None);
    let mut out = objs;
    let v = it.values();
    let kv = v.union_all(a.kws.values().copied());
    let kwstar = v.union(kv, a.kwstar.unwrap_or(EMPTY));
    for &obj in it.values().toks(objs).iter() {
        if let Token::Instance { cls, .. } = *it.values().get(obj) {
            let cls_tok = it.values().tok(Token::ClassObj(cls));
            let r = it.instantiate(cls_tok, &[], None, &a.kws, Some(kwstar), site);
            out = it.values().union(out, r);
        }
    }
    out
}

/// Model `dataclasses.asdict(obj, dict_factory=dict)`: invoke `dict_factory` if supplied.
fn asdict(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let factory = a.get(it.values(), 1, Some(name!("dict_factory")));
    if factory != EMPTY {
        return call(it, factory, &[EMPTY], site, None);
    }
    let v = it.values();
    let b = v.tok(Token::Box { kind: name!("dict"), alloc: Alloc::of(&site) });
    v.single(b)
}

/// Model `dataclasses.astuple(obj, tuple_factory=tuple)`: invoke `tuple_factory` if supplied.
fn astuple(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let factory = a.get(it.values(), 1, Some(name!("tuple_factory")));
    if factory != EMPTY {
        return call(it, factory, &[EMPTY], site, None);
    }
    EMPTY
}

/// Model `dataclasses.make_dataclass(...)`: dynamic class creation is not tracked.
fn make_dataclass(_it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    EMPTY
}
