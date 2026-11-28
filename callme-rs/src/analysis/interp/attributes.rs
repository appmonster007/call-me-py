//! Layer 4 of the interpreter: attribute reads. Methods and binding, descriptors and
//! properties, `__getattr__`, `super()`, module attributes and stub methods.

use crate::model::{ClassId, EMPTY, SiteKind, TokId, Token, Vals};
use crate::name;
use crate::stubs::{BUILTIN_NAMES, Kws, METHOD_STUBS, STUB_MODULES, STUB_PACKAGES, STUBS, shallow};
use crate::syntax::Name;
use crate::syntax::ast::{Attribute, Expr};

use super::base::{Interp, bind_attr};
use super::names::is_load;
use crate::analysis::state::DepKey;

impl Interp<'_> {
    /// Return True if `attr` may exist on instance token `inst` (of class `cls`) without
    /// calling `__getattr__`.
    ///
    /// That is the case when it was stored on the instance, is defined on a class in the MRO,
    /// or is assigned or annotated in one of those classes' source.
    fn has_instance_attr(&mut self, inst: TokId, cls: ClassId, attr: Name) -> bool {
        self.state.read(DepKey::Inst(inst));
        if self.state.inst_attrs.get(inst).is_some_and(|a| a.contains_key(&attr))
            || self.state.has_class_attr(cls, attr)
        {
            return true;
        }
        let mro = self.state.mro(cls);
        mro.iter().any(|&c| self.prog.class(c).known_attrs.contains(&attr))
    }

    /// Evaluate `obj.attr` when it is read; a store or delete target gives `EMPTY`.
    pub fn e_attribute(&mut self, outer: &Expr, e: &Attribute) -> Vals {
        let v = self.eval(&e.value);
        if !is_load(outer) {
            return EMPTY;
        }
        self.attribute(v, outer, e)
    }

    /// Return the value of `<receiver>.attr` for the already evaluated receiver values `v`.
    ///
    /// Follows Python's attribute rules on each token. On a class: descriptor `__get__`,
    /// classmethod and staticmethod binding, then metaclass attributes. On an instance: its own
    /// attribute, then class attributes bound as methods, property getters and descriptor
    /// `__get__`, then `__getattr__` when nothing else defines the name. On `super()`: the MRO
    /// after the start class, for classes and for metaclasses. On a module: its global or
    /// submodule of that name (`module_attr`). Builtin method names in `METHOD_STUBS` become
    /// their stub, bound to the receiver, when nothing else matched.
    ///
    /// outer  the attribute expression itself, which names the implicit call site
    #[allow(clippy::too_many_lines)]
    pub fn attribute(&mut self, v: Vals, outer: &Expr, e: &Attribute) -> Vals {
        let site = self.site(outer, SiteKind::Attr);
        let attr = e.attr;
        let mut out: Vec<TokId> = Vec::new();
        self.weak_depth += 1;
        for &t in self.toks(v).iter() {
            match self.tok(t) {
                Token::Builtin { name, bound, bound_kw } => {
                    let full = Name::new(&format!("{name}.{attr}"));
                    if STUBS.contains(full) || STUB_MODULES.contains(&full) || STUB_PACKAGES.contains(&full) {
                        out.push(self.state.values.tok(Token::Builtin { name: full, bound, bound_kw }));
                    } else if &*name == "builtins" && BUILTIN_NAMES.contains(&attr) {
                        out.push(self.state.values.tok(Token::Builtin {
                            name: attr,
                            bound: Vec::new().into(),
                            bound_kw: Vec::new().into(),
                        }));
                    }
                }
                Token::Module(m) => {
                    let r = self.module_attr(m, attr);
                    out.extend(self.toks(r).iter());
                }
                Token::Property { fget, fset, fdel } => match &*attr {
                    "getter" | "setter" | "deleter" => {
                        let tv = self.single(t);
                        let n = Name::new(&format!("<property_{attr}>"));
                        out.push(self.state.values.tok(Token::Builtin {
                            name: n,
                            bound: vec![tv].into(),
                            bound_kw: Vec::new().into(),
                        }));
                    }
                    "fget" => out.extend(self.toks(fget).iter()),
                    "fset" => out.extend(self.toks(fset).iter()),
                    "fdel" => out.extend(self.toks(fdel).iter()),
                    _ => {}
                },
                Token::ClassObj(cls) => {
                    let cls_vals = self.state.lookup_class_attr(cls, attr, None);
                    let tv = self.single(t);
                    for &attr_tok in self.toks(cls_vals).iter() {
                        if let Some(acls) = self.instance_cls(attr_tok) {
                            let get_fn = self.state.lookup_class_attr(acls, name!("__get__"), None);
                            if get_fn != EMPTY {
                                let bound = self.bind_all(get_fn, acls, attr_tok);
                                let r = self.apply(bound, &[EMPTY, tv], None, &Kws::default(), None, site);
                                out.extend(self.toks(r).iter());
                                continue;
                            }
                        }
                        out.push(bind_attr(&mut self.state.values, attr_tok, cls, t));
                    }
                    if !self.state.has_class_attr(cls, attr) {
                        for m in self.state.get_metaclasses(cls) {
                            let found = self.state.lookup_class_attr(m, attr, None);
                            for &attr_tok in self.toks(found).iter() {
                                if matches!(self.state.values.get(attr_tok), Token::Closure { .. }) {
                                    out.push(self.state.values.tok(Token::BoundMethod { func: attr_tok, receiver: t }));
                                } else {
                                    out.push(bind_attr(&mut self.state.values, attr_tok, m, t));
                                }
                            }
                        }
                    }
                }
                Token::Instance { cls, .. } => {
                    self.state.read(DepKey::Inst(t));
                    let own = self.state.inst_attrs.get(t).and_then(|a| a.get(&attr)).copied().unwrap_or(EMPTY);
                    out.extend(self.toks(own).iter());
                    let cls_vals = self.state.lookup_class_attr(cls, attr, None);
                    let mut fgets = EMPTY;
                    let tv = self.single(t);
                    for &attr_tok in self.toks(cls_vals).iter() {
                        match self.tok(attr_tok) {
                            Token::Property { fget, .. } => fgets = self.u(fgets, fget),
                            Token::Instance { cls: acls, .. } => {
                                let get_fn = self.state.lookup_class_attr(acls, name!("__get__"), None);
                                if get_fn == EMPTY {
                                    out.push(attr_tok);
                                } else {
                                    let bound = self.bind_all(get_fn, acls, attr_tok);
                                    let owner = self.one(Token::ClassObj(cls));
                                    let r = self.apply(bound, &[tv, owner], None, &Kws::default(), None, site);
                                    out.extend(self.toks(r).iter());
                                }
                            }
                            _ => out.push(bind_attr(&mut self.state.values, attr_tok, cls, t)),
                        }
                    }
                    if fgets != EMPTY {
                        let r = self.apply(fgets, &[tv], None, &Kws::default(), None, site);
                        out.extend(self.toks(r).iter());
                    }
                    if !self.has_instance_attr(t, cls, attr) {
                        let getattr_fn = self.state.lookup_class_attr(cls, name!("__getattr__"), None);
                        if getattr_fn != EMPTY {
                            let bound = self.bind_all(getattr_fn, cls, t);
                            let r = self.apply(bound, &[EMPTY], None, &Kws::default(), None, site);
                            out.extend(self.toks(r).iter());
                        }
                    }
                }
                Token::Super { start, receiver } => {
                    let (recv_cls, recv_is_class) = match self.tok(receiver) {
                        Token::ClassObj(c) => (c, true),
                        Token::Instance { cls, .. } => (cls, false),
                        _ => continue,
                    };
                    if recv_is_class && !self.state.mro(recv_cls).contains(&start) {
                        // metaclass super(Meta, Cls): walk Cls's metaclass MRO after Meta
                        let mut found_any = false;
                        for m in self.state.get_metaclasses(recv_cls) {
                            let found = self.state.lookup_class_attr(m, attr, Some(start));
                            if found != EMPTY {
                                found_any = true;
                                for &attr_tok in self.toks(found).iter() {
                                    if matches!(self.state.values.get(attr_tok), Token::Closure { .. }) {
                                        out.push(
                                            self.state.values.tok(Token::BoundMethod { func: attr_tok, receiver }),
                                        );
                                    } else {
                                        out.push(bind_attr(&mut self.state.values, attr_tok, m, receiver));
                                    }
                                }
                            }
                        }
                        if !found_any {
                            let rv = self.single(receiver);
                            let fallback = match &*attr {
                                "__call__" => Some((name!("type.__call__"), vec![rv])),
                                "__new__" => Some((name!("object.__new__"), Vec::new())),
                                "__setattr__" => Some((name!("object.__setattr__"), vec![rv])),
                                _ => None,
                            };
                            if let Some((n, bound)) = fallback {
                                out.push(self.state.values.tok(Token::Builtin {
                                    name: n,
                                    bound: bound.into(),
                                    bound_kw: Vec::new().into(),
                                }));
                            }
                        }
                    } else {
                        let found = self.state.lookup_class_attr(recv_cls, attr, Some(start));
                        if found != EMPTY {
                            for &attr_tok in self.toks(found).iter() {
                                out.push(bind_attr(&mut self.state.values, attr_tok, recv_cls, receiver));
                            }
                        } else if &*attr == "__new__" {
                            out.push(self.state.values.tok(Token::Builtin {
                                name: name!("object.__new__"),
                                bound: Vec::new().into(),
                                bound_kw: Vec::new().into(),
                            }));
                        } else if &*attr == "__setattr__" {
                            let rv = self.single(receiver);
                            out.push(self.state.values.tok(Token::Builtin {
                                name: name!("object.__setattr__"),
                                bound: vec![rv].into(),
                                bound_kw: Vec::new().into(),
                            }));
                        }
                    }
                }
                _ => {}
            }
        }
        self.weak_depth -= 1;
        if out.is_empty()
            && let Some(&stub) = METHOD_STUBS.get(&attr)
        {
            // xs.sort(key=...), d.get(k, default)
            let recv = shallow(&mut self.state.values, v);
            out.push(self.state.values.tok(Token::Builtin {
                name: stub,
                bound: vec![recv].into(),
                bound_kw: Vec::new().into(),
            }));
        }
        self.state.values.of(out)
    }
}
