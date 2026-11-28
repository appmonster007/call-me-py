//! Layer 7 of the interpreter: statements. `block` dispatches on the statement's kind;
//! assignments, `assert`, `del`, `return`, `raise`, and `run`, which runs the whole body.

use crate::frontend::Body;
use crate::model::{Alloc, EMPTY, SiteKind, Token};
use crate::name;
use crate::stubs::Kws;
use crate::syntax::ast::{Delete, ExceptHandler, Expr, Stmt};
use crate::syntax::walk::NodeRef;

use super::base::Interp;

impl Interp<'_> {
    /// Run statements in order, dispatching each one by its kind.
    ///
    /// In flow mode, code after a `return`, `raise`, `break` or `continue` is still analysed,
    /// so its call sites stay in the graph. It starts from the environment at the jump, but the
    /// block stays dead afterwards, so its state never reaches live code.
    pub fn block(&mut self, stmts: &[Stmt]) {
        let mut reached = true;
        for s in stmts {
            if !self.live {
                // flow mode: dead code after a jump is still analysed, from the env at the jump
                reached = false;
                self.live = true;
            }
            self.stmt(s);
        }
        if !reached {
            // ... but its state never reaches live code
            self.live = false;
        }
    }

    /// Run one statement by its kind (`s_<Stmt>` in the Python version), or `generic_stmt`.
    pub fn stmt(&mut self, s: &Stmt) {
        match s {
            Stmt::Import(x) => self.s_import(x),
            Stmt::ImportFrom(x) => self.s_import_from(x),
            Stmt::AugAssign(x) => self.s_aug_assign(x),
            Stmt::Raise(x) => {
                // handlers start from their try's accumulator, which already holds this state
                self.eval_opt(x.exc.as_ref());
                self.eval_opt(x.cause.as_ref());
                if self.flow {
                    self.live = false;
                }
            }
            Stmt::Assign(x) => {
                let v = self.eval(&x.value);
                for t in &x.targets {
                    self.assign(t, v);
                }
            }
            Stmt::AnnAssign(x) => {
                // an annotation without a value binds nothing
                if let Some(value) = &x.value {
                    let v = self.eval(value);
                    self.assign(&x.target, v);
                }
            }
            Stmt::Assert(x) => {
                // `msg` runs only on failure, so its effects stay weak
                self.eval(&x.test);
                if let Some(m) = &x.msg {
                    self.weakly(|it| it.eval(m));
                }
            }
            Stmt::Delete(x) => self.s_delete(x),
            Stmt::Return(x) => {
                let v = self.eval_opt(x.value.as_ref());
                self.ret = self.u(self.ret, v);
                if self.flow {
                    self.live = false;
                }
            }
            Stmt::FunctionDef(d) => self.s_function_def(d),
            Stmt::ClassDef(c) => self.s_class_def(c),
            Stmt::If(x) => self.s_if(s, x),
            Stmt::While(x) => self.s_while(s, x),
            Stmt::Break(_) => self.s_break(),
            Stmt::Continue(_) => self.s_continue(),
            Stmt::Try(x) => self.s_try(s, x),
            Stmt::Match(x) => self.s_match(x),
            Stmt::For(x) => self.s_for(x),
            Stmt::With(x) => self.s_with(x),
            Stmt::Expr(_) | Stmt::Pass(_) | Stmt::Global(_) | Stmt::Nonlocal(_) | Stmt::TypeAlias(_) => {
                self.generic_stmt(s)
            }
        }
    }

    /// Run a statement without its own handler: its expressions, nested statements and handlers.
    ///
    /// This is also how compound statements run in flow-insensitive mode, where branches and
    /// loops need no special treatment.
    pub fn generic_stmt(&mut self, s: &Stmt) {
        for c in NodeRef::Stmt(s).children() {
            match c {
                NodeRef::Expr(e) => {
                    self.eval(e);
                }
                NodeRef::Stmt(x) => self.block(std::slice::from_ref(x)),
                NodeRef::ExceptHandler(h) => self.handler(h),
                _ => {}
            }
        }
    }

    /// Run an `except` clause, binding `as name` to an instance of each caught class.
    pub fn handler(&mut self, h: &ExceptHandler) {
        let exc = self.eval_opt(h.type_.as_ref());
        if let Some(name) = h.name {
            let at = Alloc { func: self.info.id, line: h.pos.line, col: h.pos.col };
            let mut insts = Vec::new();
            for &t in self.toks(exc).iter() {
                if let Token::ClassObj(cls) = self.tok(t) {
                    insts.push(self.state.values.tok(Token::Instance { cls, alloc: Some(at) }));
                }
            }
            let v = self.state.values.of(insts);
            self.bind(name, v, false);
        }
        self.block(&h.body);
    }

    /// Run `del` on each target, calling the special methods involved.
    ///
    /// `del obj.attr` calls a property's deleter or a descriptor's `__delete__`, and `del x[i]`
    /// calls `__delitem__`. In flow mode `del name` leaves a local empty until it is rebound.
    fn s_delete(&mut self, s: &Delete) {
        for t in &s.targets {
            match t {
                Expr::Attribute(a) => {
                    let site = self.site(t, SiteKind::Attr);
                    self.weakly(|it| {
                        let objs = it.eval(&a.value);
                        for &obj in it.toks(objs).iter() {
                            let Some(cls) = it.instance_cls(obj) else {
                                continue;
                            };
                            let cls_vals = it.state.lookup_class_attr(cls, a.attr, None);
                            let mut fdels = EMPTY;
                            let objv = it.single(obj);
                            for &tok in it.toks(cls_vals).iter() {
                                match it.tok(tok) {
                                    Token::Property { fdel, .. } if fdel != EMPTY => fdels = it.u(fdels, fdel),
                                    Token::Instance { cls: dcls, .. } => {
                                        let del_fn = it.state.lookup_class_attr(dcls, name!("__delete__"), None);
                                        if del_fn != EMPTY {
                                            let bound = it.bind_all(del_fn, dcls, tok);
                                            it.apply(bound, &[objv], None, &Kws::default(), None, site);
                                        }
                                    }
                                    _ => {}
                                }
                            }
                            if fdels != EMPTY {
                                it.apply(fdels, &[objv], None, &Kws::default(), None, site);
                            }
                        }
                    });
                }
                Expr::Subscript(x) => {
                    let slc = self.eval(&x.slice);
                    let obj = self.eval(&x.value);
                    let site = self.site(t, SiteKind::Op);
                    self.invoke_dunder(obj, name!("__delitem__"), &[slc], site);
                }
                Expr::Name(n) if self.flow && self.class_stack.is_empty() && self.info.locals.contains(&n.id) => {
                    self.env.shift_remove(&n.id); // `del x`: x holds nothing until rebound
                }
                other => {
                    self.eval(other);
                }
            }
        }
    }

    /// Run the function's body: a lambda's expression becomes its return value.
    pub fn run(&mut self) {
        let info = self.info;
        match info.body() {
            Body::Expr(e) => self.ret = self.eval(e),
            Body::Stmts(stmts) => self.block(stmts),
        }
    }
}
