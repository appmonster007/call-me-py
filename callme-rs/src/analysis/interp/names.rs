//! Layer 1 of the interpreter: names and stores. Binding and looking up variables, applying
//! callee writes to enclosing scopes, attribute writes and data descriptors, assignment
//! targets and container stores, and the implicit dunder calls (`invoke_dunder`, `iterate`).

use crate::frontend::{FuncKind, NameSet};
use crate::model::{Alloc, EMPTY, FuncId, Site, SiteKind, TokId, Token, Vals};
use crate::name;
use crate::stubs::{BUILTIN_NAMES, Kws, STUBS};
use crate::syntax::Name;
use crate::syntax::ast::{Ctx, Expr};

use super::base::Interp;
use crate::analysis::state::{DepKey, Env, State, Writes};

/// Join `vals` into `acc[name]` (the summary or a `try` accumulator).
fn acc_join(values: &mut crate::model::Values, acc: &mut Env, name: Name, vals: Vals) {
    State::join_into(values, acc, name, vals);
}

impl Interp<'_> {
    /// Join `vals` into `name` in the summary and in every enclosing `try` accumulator.
    fn join_summary(&mut self, name: Name, vals: Vals) {
        acc_join(&mut self.state.values, &mut self.summary, name, vals);
        for acc in &mut self.tries {
            acc_join(&mut self.state.values, acc, name, vals);
        }
    }

    /// Bind `name` to `vals` in the current scope.
    ///
    /// In a class body the name becomes a class attribute; `__new__` is wrapped as a
    /// staticmethod and `__init_subclass__` as a classmethod, as Python does. A local is
    /// replaced in flow mode (a strong update) unless `weak` is set, and joined otherwise; flow
    /// mode also joins it into the summary and the enclosing `try` bodies. Names declared
    /// `global` or `nonlocal` are joined into `State.extern_` of the function that owns them.
    pub fn bind(&mut self, name: Name, vals: Vals, weak: bool) {
        if !self.class_stack.is_empty() {
            let mut vals = vals;
            if name == name!("__new__") {
                vals = self.map_vals(vals, |it, t| match it.state.values.get(t) {
                    Token::Closure { .. } => it.state.values.tok(Token::StaticMethod(t)),
                    _ => t,
                });
            } else if name == name!("__init_subclass__") {
                vals = self.map_vals(vals, |it, t| match it.state.values.get(t) {
                    Token::Closure { .. } => it.state.values.tok(Token::ClassMethod(t)),
                    _ => t,
                });
            }
            let (cid, ns) = self.class_stack.last_mut().expect("a class body is running");
            let cid = *cid;
            State::join_into(&mut self.state.values, ns, name, vals);
            if !self.readonly {
                self.state.add_class_attr(cid, name, vals);
            }
        } else if self.info.locals.contains(&name) {
            if self.flow && !weak {
                // strong update: this binding replaces the old one
                if vals == EMPTY {
                    self.env.shift_remove(&name);
                } else {
                    self.env.insert(name, vals);
                }
            } else if vals != EMPTY {
                State::join_into(&mut self.state.values, &mut self.env, name, vals);
            }
            if self.flow && vals != EMPTY {
                self.join_summary(name, vals);
            }
        } else if vals != EMPTY && !self.readonly && self.info.globals.contains(&name) {
            let owner = self.prog.module_body(&self.info.sym);
            self.state.add_extern(owner, name, vals);
            if self.flow
                && let Some(n) = self.node
            {
                self.state.add_write(n, owner, name, vals);
            }
        } else if vals != EMPTY
            && !self.readonly
            && self.info.nonlocals.contains(&name)
            && let Some(owner) = self.nonlocal_owner(name)
        {
            self.state.add_extern(owner, name, vals);
            if self.flow
                && let Some(n) = self.node
            {
                self.state.add_write(n, owner, name, vals);
            }
        }
    }

    /// Return the nearest enclosing function with local `name`, which `nonlocal name` writes.
    pub fn nonlocal_owner(&self, name: Name) -> Option<FuncId> {
        let mut f = self.info.parent;
        while let Some(id) = f {
            let info = self.prog.func(id);
            if info.kind == FuncKind::Module {
                break;
            }
            if info.locals.contains(&name) {
                return Some(id);
            }
            f = info.parent;
        }
        None
    }

    /// Return the value of variable `name` in the current scope.
    ///
    /// The search follows Python's scoping. The class body being run comes first, then the
    /// function's locals, including writes from inner scopes in flow mode. Then come the
    /// enclosing functions: the summary of the context the closure was created in, or every
    /// context when that is unknown. Then the module, and finally a builtin with that name. An
    /// unresolved name gives `EMPTY`, so calling it adds no edge.
    pub fn lookup(&mut self, name: Name) -> Vals {
        if let Some((_, ns)) = self.class_stack.last()
            && let Some(&v) = ns.get(&name)
        {
            return v;
        }
        if self.info.locals.contains(&name) {
            return match self.node {
                None => self.state.union_env(self.info.id, name),
                Some(_) => self.env.get(&name).copied().unwrap_or(EMPTY),
            };
        }
        let mut parent_node = self.node.and_then(|n| self.state.ctx_of(n).parent);
        let mut f = self.info.parent;
        while let Some(id) = f {
            // lexical chain: closures, then module
            let pinfo = self.prog.func(id);
            if pinfo.locals.contains(&name) {
                return match parent_node {
                    Some(p) => {
                        self.state.read(DepKey::Env(p));
                        self.state.envs.get(p).and_then(|e| e.get(&name)).copied().unwrap_or(EMPTY)
                    }
                    None => self.state.union_env(id, name),
                };
            }
            parent_node = parent_node.and_then(|p| self.state.ctx_of(p).parent);
            f = pinfo.parent;
        }
        if BUILTIN_NAMES.contains(&name) {
            // not shadowed anywhere: the builtin
            return self.one(Token::Builtin { name, bound: Vec::new().into(), bound_kw: Vec::new().into() });
        }
        EMPTY // unresolved
    }

    /// Call the setters of the data descriptors in `cls_vals` with `obj` and `vals`.
    ///
    /// A property's `fset` and the `__set__` of a descriptor instance are called. Return True
    /// if any setter was found, which means the value is not stored on the instance.
    pub fn invoke_data_descriptor_set(&mut self, obj: TokId, cls_vals: Vals, vals: Vals, site: Site) -> bool {
        self.weakly(|it| {
            let mut fsets = EMPTY;
            let mut handled = false;
            let objv = it.single(obj);
            for &tok in it.toks(cls_vals).iter() {
                match it.tok(tok) {
                    Token::Property { fset, .. } if fset != EMPTY => fsets = it.u(fsets, fset),
                    Token::Instance { cls, .. } => {
                        let set_fn = it.state.lookup_class_attr(cls, name!("__set__"), None);
                        if set_fn != EMPTY {
                            let bound = it.bind_all(set_fn, cls, tok);
                            it.apply(bound, &[objv, vals], None, &Kws::default(), None, site);
                            handled = true;
                        }
                    }
                    _ => {}
                }
            }
            if fsets != EMPTY {
                it.apply(fsets, &[objv, vals], None, &Kws::default(), None, site);
                handled = true;
            }
            handled
        })
    }

    /// Store `vals` into attribute `attr` of every object in `objs`, as `obj.attr = vals` does.
    ///
    /// On an instance, a custom `__setattr__` is called instead if the class has one, then a
    /// data descriptor's setter; only when neither exists is the value joined into the instance
    /// attribute. On a class object it is joined into the class attribute, and on a module into
    /// that module's global (`State.extern_`). Read-only mode writes nothing.
    pub fn set_attr(&mut self, objs: Vals, attr: Name, vals: Vals, site: Site) {
        for &t in self.toks(objs).iter() {
            match self.tok(t) {
                Token::Instance { cls, .. } => {
                    let setattr_fn = self.state.lookup_class_attr(cls, name!("__setattr__"), None);
                    if setattr_fn != EMPTY {
                        let bound = self.bind_all(setattr_fn, cls, t);
                        self.weakly(|it| it.apply(bound, &[EMPTY, vals], None, &Kws::default(), None, site));
                        continue;
                    }
                    let cls_vals = self.state.lookup_class_attr(cls, attr, None);
                    if !self.invoke_data_descriptor_set(t, cls_vals, vals, site) && !self.readonly {
                        self.state.add_inst_attr(t, attr, vals);
                    }
                }
                Token::ClassObj(cls) if !self.readonly => self.state.add_class_attr(cls, attr, vals),
                Token::Module(m) if !self.readonly => {
                    // a namespace package has no globals of its own
                    if let Some(body) = self.prog.module(m).and_then(|i| i.func) {
                        self.state.add_extern(body, attr, vals);
                        if self.flow
                            && let Some(n) = self.node
                        {
                            self.state.add_write(n, body, attr, vals);
                        }
                    }
                }
                _ => {}
            }
        }
    }

    /// Assign `vals` to an assignment target, following Python's forms of target.
    ///
    /// A name is bound; an attribute goes through `set_attr`; a subscript calls `__setitem__`
    /// and otherwise stores into the container. Tuple and list targets each receive the
    /// elements of `vals`, since unpacking reads them; `*rest` targets receive them too.
    pub fn assign(&mut self, t: &Expr, vals: Vals) {
        match t {
            Expr::Name(n) => self.bind(n.id, vals, false),
            Expr::Attribute(a) => {
                let obj = self.eval(&a.value);
                let site = self.site(t, SiteKind::Attr);
                self.set_attr(obj, a.attr, vals, site);
            }
            Expr::Subscript(s) => {
                let slc = self.eval(&s.slice);
                let obj = self.eval(&s.value);
                let site = self.site(t, SiteKind::Op);
                let (found, _) = self.invoke_dunder(obj, name!("__setitem__"), &[slc, vals], site);
                self.store(&s.value, obj, vals, !found);
            }
            Expr::Tuple(x) | Expr::List(x) => {
                let elems = self.state.deref(vals); // unpacking reads the elements
                for e in &x.elts {
                    self.assign(e, elems);
                }
            }
            Expr::Starred(s) => self.assign(&s.value, vals),
            other => {
                self.eval(other);
            }
        }
    }

    /// Return a new `Box` of `kind` for the container created by expression `e`.
    pub fn new_box(&mut self, kind: Name, e: &Expr) -> Vals {
        let p = e.pos();
        self.one(Token::Box { kind, alloc: Alloc { func: self.info.id, line: p.line, col: p.col } })
    }

    /// Join `vals` into the containers in `recv`, the value of `target`, as a weak update.
    ///
    /// Each `Box` in `recv` receives the values in `State.heap`. If `recv` holds no box (the
    /// container's origin is unknown) and `fallback` is set, the target expression itself is
    /// widened instead, as if the container were the union of its elements.
    pub fn store(&mut self, target: &Expr, recv: Vals, vals: Vals, fallback: bool) {
        if vals == EMPTY {
            return;
        }
        let boxes: Vec<TokId> = self
            .toks(recv)
            .iter()
            .copied()
            .filter(|&t| matches!(self.state.values.get(t), Token::Box { .. }))
            .collect();
        if !boxes.is_empty() {
            if !self.readonly {
                for b in boxes {
                    self.state.add_heap(b, vals);
                }
            }
        } else if fallback && matches!(target, Expr::Name(_) | Expr::Attribute(_) | Expr::Subscript(_)) {
            self.assign(target, vals);
        }
    }

    /// Call special method `name` on every instance or stub object in `recv` that defines it.
    ///
    /// Return whether any token defined it, and the union of the results. A `Builtin` token
    /// matches when `<tok.name>.<name>` is registered in `STUBS`.
    pub fn invoke_dunder(&mut self, recv: Vals, name: Name, pos: &[Vals], site: Site) -> (bool, Vals) {
        self.weakly(|it| {
            let mut found = false;
            let mut out = EMPTY;
            for &t in it.toks(recv).iter() {
                match it.tok(t) {
                    Token::Instance { cls, .. } => {
                        let f = it.state.lookup_class_attr(cls, name, None);
                        if f != EMPTY {
                            found = true;
                            let bound = it.bind_all(f, cls, t);
                            let r = it.apply(bound, pos, None, &Kws::default(), None, site);
                            out = it.u(out, r);
                        }
                    }
                    Token::Builtin { name: bname, bound, bound_kw } => {
                        let full = Name::new(&format!("{bname}.{name}"));
                        if STUBS.contains(full) {
                            found = true;
                            let callee = it.one(Token::Builtin { name: full, bound, bound_kw });
                            let r = it.apply(callee, pos, None, &Kws::default(), None, site);
                            out = it.u(out, r);
                        }
                    }
                    _ => {}
                }
            }
            (found, out)
        })
    }

    /// Return the elements produced by iterating over `iterable`, as a `for` loop does.
    ///
    /// An instance whose class defines `__iter__` has it called, then `__next__` on the result;
    /// with `is_async`, `__aiter__` and `__anext__` are used instead. Any other value stands for
    /// its own elements and is returned with its containers' contents.
    pub fn iterate(&mut self, iterable: Vals, site: Site, is_async: bool) -> Vals {
        let (iter_name, next_name) =
            if is_async { (name!("__aiter__"), name!("__anext__")) } else { (name!("__iter__"), name!("__next__")) };
        self.weakly(|it| {
            let mut out = EMPTY;
            // the other tokens stand for their own elements; `deref` of their union is the
            // union of their `deref`s, so they are read in one go
            let mut rest: Vec<TokId> = Vec::new();
            for &t in it.toks(iterable).iter() {
                if let Some(cls) = it.instance_cls(t) {
                    let it_fn = it.state.lookup_class_attr(cls, iter_name, None);
                    if it_fn != EMPTY {
                        let bound = it.bind_all(it_fn, cls, t);
                        let iters = it.apply(bound, &[], None, &Kws::default(), None, site);
                        let (_, elem) = it.invoke_dunder(iters, next_name, &[], site);
                        out = it.u(out, elem);
                        continue;
                    }
                }
                rest.push(t);
            }
            let rest = it.state.values.of(rest);
            let d = it.state.deref(rest);
            it.u(out, d)
        })
    }

    /// Evaluate `expr` and return its elements; literal containers are read directly.
    pub fn iter_expr(&mut self, expr: &Expr, is_async: bool) -> Vals {
        let v = self.eval(expr);
        if matches!(
            expr,
            Expr::List(_)
                | Expr::Tuple(_)
                | Expr::Set(_)
                | Expr::Dict(_)
                | Expr::ListComp(_)
                | Expr::SetComp(_)
                | Expr::DictComp(_)
                | Expr::GeneratorExp(_)
        ) {
            return self.state.deref(v);
        }
        let site = self.site(expr, SiteKind::Iter);
        self.iterate(v, site, is_async)
    }

    /// Return True if `nonlocal name` in closure token `tok` refers to this node's own local.
    fn closure_owns_local(&self, func: FuncId, parent: Option<crate::model::NodeId>, name: Name) -> bool {
        let mut f = self.prog.func(func).parent;
        let mut pnode = parent;
        while let Some(id) = f {
            let info = self.prog.func(id);
            if info.kind == FuncKind::Module {
                break;
            }
            if info.locals.contains(&name) {
                return id == self.info.id && pnode.is_some() && pnode == self.node;
            }
            f = info.parent;
            pnode = pnode.and_then(|p| self.state.ctx_of(p).parent);
        }
        false
    }

    /// Return the locals of this function that closure `func` (created in `parent`) definitely
    /// overwrites on return.
    pub fn must_kills_for_closure(&self, func: FuncId, parent: Option<crate::model::NodeId>) -> NameSet {
        let finfo = self.prog.func(func);
        let mut kills = NameSet::default();
        for &name in &finfo.must_writes {
            if finfo.globals.contains(&name) {
                if self.prog.module_body(&finfo.sym) == self.info.id {
                    kills.insert(name);
                }
            } else if finfo.nonlocals.contains(&name) && self.closure_owns_local(func, parent, name) {
                kills.insert(name);
            }
        }
        kills
    }

    /// Apply callee side-effect `writes` to `env` (strong for `kills`) and propagate them.
    ///
    /// Writes targeting this function update `env`, `summary` and `tries`. Writes targeting
    /// other scopes, or targeting this function without a guaranteed kill on a non-module
    /// function (which may belong to an outer recursive activation), are propagated to
    /// `State.writes` of this node.
    pub fn apply_writes(&mut self, writes: &Writes, kills: &NameSet) {
        let own = writes.get(&self.info.id);
        if self.flow {
            for &name in kills {
                let vals = own.and_then(|o| o.get(&name)).copied().unwrap_or(EMPTY);
                if vals == EMPTY {
                    self.env.shift_remove(&name);
                } else {
                    self.env.insert(name, vals);
                    self.join_summary(name, vals);
                }
            }
            if let Some(own) = own {
                for (&name, &vals) in own {
                    if !kills.contains(&name) && vals != EMPTY {
                        State::join_into(&mut self.state.values, &mut self.env, name, vals);
                        self.join_summary(name, vals);
                    }
                }
            }
        }
        if self.flow
            && !self.readonly
            && let Some(node) = self.node
        {
            for (&owner, env) in writes {
                if owner == self.info.id {
                    if self.info.kind != FuncKind::Module {
                        for (&name, &vals) in env {
                            if !kills.contains(&name) {
                                self.state.add_write(node, owner, name, vals);
                            }
                        }
                    }
                } else {
                    for (&name, &vals) in env {
                        self.state.add_write(node, owner, name, vals);
                    }
                }
            }
        }
    }
}

/// Return True if expression `e` is read (its context is `Load`).
pub fn is_load(e: &Expr) -> bool {
    e.ctx().is_none_or(|c| c == Ctx::Load)
}
