//! Statements, parameter lists and the other non-expression nodes (`visit_<Stmt>`,
//! `visit_arguments`, ...).

use super::expr::binop;
use super::{Unparser, prec};
use crate::syntax::ast::*;

impl Unparser {
    /// `_type_params_helper`.
    pub(super) fn type_params(&mut self, tps: &[TypeParam]) {
        if tps.is_empty() {
            return;
        }
        self.write("[");
        self.interleave(tps, ", ", |u, t| match t {
            TypeParam::TypeVar { name, bound, default_value, .. } => {
                u.write(name);
                if let Some(b) = bound {
                    u.write(": ");
                    u.traverse(b, prec::TEST);
                }
                if let Some(d) = default_value {
                    u.write(" = ");
                    u.traverse(d, prec::TEST);
                }
            }
            TypeParam::TypeVarTuple { name, default_value, .. } => {
                u.write("*");
                u.write(name);
                if let Some(d) = default_value {
                    u.write(" = ");
                    u.traverse(d, prec::TEST);
                }
            }
            TypeParam::ParamSpec { name, default_value, .. } => {
                u.write("**");
                u.write(name);
                if let Some(d) = default_value {
                    u.write(" = ");
                    u.traverse(d, prec::TEST);
                }
            }
        });
        self.write("]");
    }

    /// Write one statement (`visit_<Stmt>`).
    #[allow(clippy::too_many_lines)]
    pub(super) fn stmt(&mut self, s: &Stmt) {
        if self.done {
            return;
        }
        match s {
            Stmt::Expr(x) => {
                self.fill("");
                self.traverse(&x.value, prec::YIELD);
            }
            Stmt::Import(x) => {
                self.fill("import ");
                self.interleave(&x.names, ", ", Unparser::alias);
            }
            Stmt::ImportFrom(x) => {
                self.fill("from ");
                self.write(&".".repeat(x.level as usize));
                if let Some(m) = x.module {
                    self.write(&m);
                }
                self.write(" import ");
                self.interleave(&x.names, ", ", Unparser::alias);
            }
            Stmt::Assign(x) => {
                self.fill("");
                for t in &x.targets {
                    self.traverse(t, prec::TUPLE);
                    self.write(" = ");
                }
                self.traverse(&x.value, prec::TEST);
            }
            Stmt::AugAssign(x) => {
                self.fill("");
                self.traverse(&x.target, prec::TEST);
                self.write(" ");
                self.write(binop(x.op).0);
                self.write("= ");
                self.traverse(&x.value, prec::TEST);
            }
            Stmt::AnnAssign(x) => {
                self.fill("");
                let parens = !x.simple && matches!(x.target, Expr::Name(_));
                self.parens_if(parens, |u| u.traverse(&x.target, prec::TEST));
                self.write(": ");
                self.traverse(&x.annotation, prec::TEST);
                if let Some(v) = &x.value {
                    self.write(" = ");
                    self.traverse(v, prec::TEST);
                }
            }
            Stmt::Return(x) => {
                self.fill("return");
                if let Some(v) = &x.value {
                    self.write(" ");
                    self.traverse(v, prec::TEST);
                }
            }
            Stmt::Pass(_) => self.fill("pass"),
            Stmt::Break(_) => self.fill("break"),
            Stmt::Continue(_) => self.fill("continue"),
            Stmt::Delete(x) => {
                self.fill("del ");
                self.interleave(&x.targets, ", ", |u, e| u.traverse(e, prec::TEST));
            }
            Stmt::Assert(x) => {
                self.fill("assert ");
                self.traverse(&x.test, prec::TEST);
                if let Some(m) = &x.msg {
                    self.write(", ");
                    self.traverse(m, prec::TEST);
                }
            }
            Stmt::Global(x) | Stmt::Nonlocal(x) => {
                self.fill(if matches!(s, Stmt::Global(_)) { "global " } else { "nonlocal " });
                self.interleave(&x.names, ", ", |u, n| u.write(n));
            }
            Stmt::Raise(x) => {
                self.fill("raise");
                if let Some(e) = &x.exc {
                    self.write(" ");
                    self.traverse(e, prec::TEST);
                    if let Some(c) = &x.cause {
                        self.write(" from ");
                        self.traverse(c, prec::TEST);
                    }
                }
            }
            Stmt::Try(x) => {
                let prev = self.in_try_star;
                self.in_try_star = x.is_star;
                self.fill("try");
                self.block(|u| u.body(&x.body));
                for h in &x.handlers {
                    self.fill(if self.in_try_star { "except*" } else { "except" });
                    if let Some(t) = &h.type_ {
                        self.write(" ");
                        self.traverse(t, prec::TEST);
                    }
                    if let Some(n) = h.name {
                        self.write(" as ");
                        self.write(&n);
                    }
                    self.block(|u| u.body(&h.body));
                }
                if !x.orelse.is_empty() {
                    self.fill("else");
                    self.block(|u| u.body(&x.orelse));
                }
                if !x.finalbody.is_empty() {
                    self.fill("finally");
                    self.block(|u| u.body(&x.finalbody));
                }
                self.in_try_star = prev;
            }
            Stmt::ClassDef(c) => {
                self.maybe_newline();
                for d in &c.decorator_list {
                    self.fill("@");
                    self.traverse(d, prec::TEST);
                }
                self.fill("class ");
                self.write(&c.name);
                self.type_params(&c.type_params);
                let parens = !c.bases.is_empty() || !c.keywords.is_empty();
                self.parens_if(parens, |u| {
                    let mut comma = false;
                    for e in &c.bases {
                        if comma {
                            u.write(", ");
                        }
                        comma = true;
                        u.traverse(e, prec::TEST);
                    }
                    for k in &c.keywords {
                        if comma {
                            u.write(", ");
                        }
                        comma = true;
                        u.keyword(k);
                    }
                });
                self.block(|u| u.docstring_and_body(&c.body));
            }
            Stmt::FunctionDef(f) => {
                self.maybe_newline();
                for d in &f.decorator_list {
                    self.fill("@");
                    self.traverse(d, prec::TEST);
                }
                self.fill(if f.is_async { "async def " } else { "def " });
                self.write(&f.name);
                self.type_params(&f.type_params);
                self.write("(");
                self.arguments(&f.args);
                self.write(")");
                if let Some(r) = &f.returns {
                    self.write(" -> ");
                    self.traverse(r, prec::TEST);
                }
                self.block(|u| u.docstring_and_body(&f.body));
            }
            Stmt::TypeAlias(x) => {
                self.fill("type ");
                self.traverse(&x.name, prec::TEST);
                self.type_params(&x.type_params);
                self.write(" = ");
                self.traverse(&x.value, prec::TEST);
            }
            Stmt::For(x) => {
                self.fill(if x.is_async { "async for " } else { "for " });
                self.traverse(&x.target, prec::TUPLE);
                self.write(" in ");
                self.traverse(&x.iter, prec::TEST);
                self.block(|u| u.body(&x.body));
                if !x.orelse.is_empty() {
                    self.fill("else");
                    self.block(|u| u.body(&x.orelse));
                }
            }
            Stmt::If(x) => {
                self.fill("if ");
                self.traverse(&x.test, prec::TEST);
                self.block(|u| u.body(&x.body));
                let mut node = x;
                while let [Stmt::If(inner)] = &node.orelse[..] {
                    node = inner;
                    self.fill("elif ");
                    self.traverse(&node.test, prec::TEST);
                    self.block(|u| u.body(&node.body));
                }
                if !node.orelse.is_empty() {
                    self.fill("else");
                    self.block(|u| u.body(&node.orelse));
                }
            }
            Stmt::While(x) => {
                self.fill("while ");
                self.traverse(&x.test, prec::TEST);
                self.block(|u| u.body(&x.body));
                if !x.orelse.is_empty() {
                    self.fill("else");
                    self.block(|u| u.body(&x.orelse));
                }
            }
            Stmt::With(x) => {
                self.fill(if x.is_async { "async with " } else { "with " });
                self.interleave(&x.items, ", ", |u, i| {
                    u.traverse(&i.context_expr, prec::TEST);
                    if let Some(v) = &i.optional_vars {
                        u.write(" as ");
                        u.traverse(v, prec::TEST);
                    }
                });
                self.block(|u| u.body(&x.body));
            }
            Stmt::Match(x) => {
                self.fill("match ");
                self.traverse(&x.subject, prec::TEST);
                self.block(|u| {
                    for c in &x.cases {
                        if u.done {
                            return;
                        }
                        u.fill("case ");
                        u.pattern(&c.pattern, prec::TEST);
                        if let Some(g) = &c.guard {
                            u.write(" if ");
                            u.traverse(g, prec::TEST);
                        }
                        u.block(|u| u.body(&c.body));
                    }
                });
            }
        }
    }

    /// `visit_alias`.
    pub(super) fn alias(&mut self, a: &Alias) {
        self.write(&a.name);
        if let Some(n) = a.asname {
            self.write(" as ");
            self.write(&n);
        }
    }

    /// `visit_keyword`.
    pub(super) fn keyword(&mut self, k: &Keyword) {
        match k.arg {
            None => self.write("**"),
            Some(n) => {
                self.write(&n);
                self.write("=");
            }
        }
        self.traverse(&k.value, prec::TEST);
    }

    /// `visit_arg`.
    pub(super) fn arg(&mut self, a: &Arg) {
        self.write(&a.arg);
        if let Some(ann) = &a.annotation {
            self.write(": ");
            self.traverse(ann, prec::TEST);
        }
    }

    /// `visit_arguments`.
    pub(super) fn arguments(&mut self, a: &Arguments) {
        let mut first = true;
        let all: Vec<&Arg> = a.posonlyargs.iter().chain(&a.args).collect();
        let pad = all.len() - a.defaults.len();
        for (i, arg) in all.iter().enumerate() {
            if first {
                first = false;
            } else {
                self.write(", ");
            }
            self.arg(arg);
            if i >= pad {
                self.write("=");
                self.traverse(&a.defaults[i - pad], prec::TEST);
            }
            if i + 1 == a.posonlyargs.len() {
                self.write(", /");
            }
        }
        if a.vararg.is_some() || !a.kwonlyargs.is_empty() {
            if first {
                first = false;
            } else {
                self.write(", ");
            }
            self.write("*");
            if let Some(v) = &a.vararg {
                self.write(&v.arg);
                if let Some(ann) = &v.annotation {
                    self.write(": ");
                    self.traverse(ann, prec::TEST);
                }
            }
        }
        for (arg, d) in a.kwonlyargs.iter().zip(&a.kw_defaults) {
            self.write(", ");
            self.arg(arg);
            if let Some(d) = d {
                self.write("=");
                self.traverse(d, prec::TEST);
            }
        }
        if let Some(k) = &a.kwarg {
            if !first {
                self.write(", ");
            }
            self.write("**");
            self.write(&k.arg);
            if let Some(ann) = &k.annotation {
                self.write(": ");
                self.traverse(ann, prec::TEST);
            }
        }
    }
}
