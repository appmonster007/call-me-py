//! Stubs for the object model: classmethod, staticmethod, super, property, object and type.

use crate::model::{Alloc, EMPTY, Site, TokId, Token, Vals, Values};
use crate::name;

use super::registry::{Args, Host, MAX_PARTIAL_DEPTH, Registry};

/// Register this module's stubs, in the Python version's order.
pub fn register(r: &mut Registry) {
    r.add(&["classmethod"], classmethod);
    r.add(&["staticmethod"], staticmethod);
    r.add(&["super"], super_);
    r.add(&["property"], property);
    r.add(&["<property_getter>"], property_getter);
    r.add(&["<property_setter>"], property_setter);
    r.add(&["<property_deleter>"], property_deleter);
    r.add(&["object"], nothing);
    r.add(&["type"], nothing);
    r.add(&["type.__call__"], type_call);
    r.add(&["object.__new__"], object_new);
    r.add(&["object.__setattr__"], object_setattr);
}

/// Wrap each function of argument 0 with `wrap`, keeping tokens already wrapped (`is_wrapped`)
/// and dropping too deep tokens.
fn wrap_each(it: &mut dyn Host, a: &Args, is_wrapped: fn(&Token) -> bool, wrap: fn(TokId) -> Token) -> Vals {
    let v = it.values();
    let arg = a.get(v, 0, None);
    let mut out = Vec::new();
    for &t in v.toks(arg).iter() {
        if is_wrapped(v.get(t)) {
            out.push(t);
        } else if v.depth(t) <= MAX_PARTIAL_DEPTH {
            out.push(v.tok(wrap(t)));
        }
    }
    v.of(out)
}

/// Model `classmethod(f)`: wrap each function in a `ClassMethod`, dropping too deep tokens.
fn classmethod(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    wrap_each(it, a, |t| matches!(t, Token::ClassMethod(_)), Token::ClassMethod)
}

/// Model `staticmethod(f)`: wrap each function in a `StaticMethod`, dropping too deep tokens.
fn staticmethod(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    wrap_each(it, a, |t| matches!(t, Token::StaticMethod(_)), Token::StaticMethod)
}

/// Return True for a class object or an instance token.
fn is_receiver(v: &Values, t: TokId) -> bool {
    matches!(v.get(t), Token::ClassObj(_) | Token::Instance { .. })
}

/// Model `super()` and `super(C, obj)`: return a `Super` token per start class and receiver.
///
/// The zero-argument form uses the class whose body defines the current function, and the
/// value of its first positional parameter (`self` or `cls`). It returns `EMPTY` outside a
/// method.
fn super_(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    if a.pos.is_empty() {
        // 0-arg super(): enclosing class + first positional param (self / cls)
        let (owner, first) = (it.info().owner, it.info().sig.pos.first().copied());
        let (Some(owner), Some(first)) = (owner, first) else {
            return EMPTY;
        };
        let recv = it.lookup(first);
        let v = it.values();
        let toks: Vec<TokId> = v.toks(recv).iter().copied().filter(|&r| is_receiver(v, r)).collect();
        let out = toks.into_iter().map(|r| v.tok(Token::Super { start: owner, receiver: r })).collect();
        return v.of(out);
    }
    let v = it.values();
    let a0 = a.get(v, 0, None);
    let a1 = a.get(v, 1, None);
    let starts: Vec<_> = v
        .slice(a0)
        .iter()
        .filter_map(|&t| match v.get(t) {
            Token::ClassObj(c) => Some(*c),
            _ => None,
        })
        .collect();
    let recvs: Vec<TokId> = v.slice(a1).iter().copied().filter(|&r| is_receiver(v, r)).collect();
    let mut out = Vec::new();
    for &s in &starts {
        for &r in &recvs {
            out.push(v.tok(Token::Super { start: s, receiver: r }));
        }
    }
    v.of(out)
}

/// Return the accessor functions in `vals`: properties and too deep tokens are dropped.
pub fn property_fn(v: &mut Values, vals: Vals) -> Vals {
    v.filter(vals, |v, t| !matches!(v.get(t), Token::Property { .. }) && v.depth(t) <= MAX_PARTIAL_DEPTH)
}

/// Model `property(fget, fset, fdel)`: return one `Property` holding the three accessors.
fn property(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    let v = it.values();
    let g = a.get(v, 0, Some(name!("fget")));
    let fget = property_fn(v, g);
    let s = a.get(v, 1, Some(name!("fset")));
    let fset = property_fn(v, s);
    let d = a.get(v, 2, Some(name!("fdel")));
    let fdel = property_fn(v, d);
    let t = v.tok(Token::Property { fget, fset, fdel });
    v.single(t)
}

/// Return a copy of each property in argument 0 with one accessor replaced by argument 1.
///
/// which  0 for the getter, 1 for the setter, 2 for the deleter
fn property_with(it: &mut dyn Host, a: &Args, which: usize) -> Vals {
    let v = it.values();
    let f = a.get(v, 1, None);
    let fun = property_fn(v, f);
    let props = a.get(v, 0, None);
    let mut out = Vec::new();
    for &p in v.toks(props).iter() {
        if let Token::Property { fget, fset, fdel } = *v.get(p) {
            let tok = match which {
                0 => Token::Property { fget: fun, fset, fdel },
                1 => Token::Property { fget, fset: fun, fdel },
                _ => Token::Property { fget, fset, fdel: fun },
            };
            out.push(v.tok(tok));
        }
    }
    v.of(out)
}

/// Model `prop.getter(f)`: return a copy of each property with `f` as its getter.
fn property_getter(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    property_with(it, a, 0)
}

/// Model `prop.setter(f)`: return a copy of each property with `f` as its setter.
fn property_setter(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    property_with(it, a, 1)
}

/// Model `prop.deleter(f)`: return a copy of each property with `f` as its deleter.
fn property_deleter(it: &mut dyn Host, a: &Args, _site: Site) -> Vals {
    property_with(it, a, 2)
}

/// Model `object()` and `type(...)`: nothing callable; new classes from `type` are not tracked.
fn nothing(_it: &mut dyn Host, _a: &Args, _site: Site) -> Vals {
    EMPTY
}

/// Model the default metaclass call `type.__call__(cls, ...)`: instantiate each class.
fn type_call(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let classes = a.get(it.values(), 0, None);
    let mut out = EMPTY;
    for &tok in it.values().toks(classes).iter() {
        if matches!(it.values().get(tok), Token::ClassObj(_)) {
            let r = it.instantiate(tok, a.from(1), a.star, &a.kws, a.kwstar, site);
            out = it.values().union(out, r);
        }
    }
    out
}

/// Model `object.__new__(cls)`: return a new instance of each class, allocated at this call.
fn object_new(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let v = it.values();
    let classes = a.get(v, 0, None);
    let mut out = Vec::new();
    for &t in v.toks(classes).iter() {
        if let Token::ClassObj(c) = *v.get(t) {
            out.push(v.tok(Token::Instance { cls: c, alloc: Some(Alloc::of(&site)) }));
        }
    }
    v.of(out)
}

/// Model `object.__setattr__(obj, name, value)`, which skips a custom `__setattr__`.
///
/// The attribute name is not tracked, so `value` goes to the setter of every data descriptor
/// (a property or an object with `__set__`) found in the classes of `obj`'s MRO.
fn object_setattr(it: &mut dyn Host, a: &Args, site: Site) -> Vals {
    let val = a.get(it.values(), 2, None);
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
                it.invoke_data_descriptor_set(obj, toks, val, site);
            }
        }
    }
    EMPTY
}
