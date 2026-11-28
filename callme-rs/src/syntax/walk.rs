//! Generic traversal: CPython's `ast.iter_child_nodes` and `ast.walk`.
//!
//! `NodeRef` is a reference to any node that `ast.iter_child_nodes` can yield, except the
//! context and operator singletons (`Load`, `Add`, ...), which no traversal here needs.
//! Children come in CPython's `_fields` order, which fixes the order of CFG points and of
//! calls in generic traversals, and `walk` is breadth-first like `ast.walk`, which fixes the
//! order in which imports are found.

use std::collections::VecDeque;

use super::ast::*;

/// A reference to one syntax node of any kind.
#[derive(Clone, Copy, Debug)]
#[allow(missing_docs)]
pub enum NodeRef<'a> {
    Module(&'a Module),
    Stmt(&'a Stmt),
    Expr(&'a Expr),
    Comprehension(&'a Comprehension),
    ExceptHandler(&'a ExceptHandler),
    Arguments(&'a Arguments),
    Arg(&'a Arg),
    Keyword(&'a Keyword),
    Alias(&'a Alias),
    WithItem(&'a WithItem),
    MatchCase(&'a MatchCase),
    Pattern(&'a Pattern),
    TypeParam(&'a TypeParam),
}

/// Call `f` on every statement of `body`.
fn stmts<'a>(body: &'a [Stmt], f: &mut impl FnMut(NodeRef<'a>)) {
    for s in body {
        f(NodeRef::Stmt(s));
    }
}

/// Call `f` on every expression of `es`.
fn exprs<'a>(es: &'a [Expr], f: &mut impl FnMut(NodeRef<'a>)) {
    for e in es {
        f(NodeRef::Expr(e));
    }
}

/// Call `f` on `e` if it is present.
fn opt<'a>(e: Option<&'a Expr>, f: &mut impl FnMut(NodeRef<'a>)) {
    if let Some(e) = e {
        f(NodeRef::Expr(e));
    }
}

/// Call `f` on every type parameter.
fn type_params<'a>(tp: &'a [TypeParam], f: &mut impl FnMut(NodeRef<'a>)) {
    for t in tp {
        f(NodeRef::TypeParam(t));
    }
}

impl<'a> NodeRef<'a> {
    /// Call `f` on each child node, in CPython's field order (`ast.iter_child_nodes`).
    #[allow(clippy::too_many_lines)]
    pub fn for_each_child(self, f: &mut impl FnMut(NodeRef<'a>)) {
        match self {
            NodeRef::Module(m) => stmts(&m.body, f),
            NodeRef::Stmt(s) => match s {
                Stmt::FunctionDef(d) => {
                    f(NodeRef::Arguments(&d.args));
                    stmts(&d.body, f);
                    exprs(&d.decorator_list, f);
                    opt(d.returns.as_deref(), f);
                    type_params(&d.type_params, f);
                }
                Stmt::ClassDef(c) => {
                    exprs(&c.bases, f);
                    for k in &c.keywords {
                        f(NodeRef::Keyword(k));
                    }
                    stmts(&c.body, f);
                    exprs(&c.decorator_list, f);
                    type_params(&c.type_params, f);
                }
                Stmt::Return(x) => opt(x.value.as_ref(), f),
                Stmt::Delete(x) => exprs(&x.targets, f),
                Stmt::Assign(x) => {
                    exprs(&x.targets, f);
                    f(NodeRef::Expr(&x.value));
                }
                Stmt::TypeAlias(x) => {
                    f(NodeRef::Expr(&x.name));
                    type_params(&x.type_params, f);
                    f(NodeRef::Expr(&x.value));
                }
                Stmt::AugAssign(x) => {
                    f(NodeRef::Expr(&x.target));
                    f(NodeRef::Expr(&x.value));
                }
                Stmt::AnnAssign(x) => {
                    f(NodeRef::Expr(&x.target));
                    f(NodeRef::Expr(&x.annotation));
                    opt(x.value.as_ref(), f);
                }
                Stmt::For(x) => {
                    f(NodeRef::Expr(&x.target));
                    f(NodeRef::Expr(&x.iter));
                    stmts(&x.body, f);
                    stmts(&x.orelse, f);
                }
                Stmt::While(x) => {
                    f(NodeRef::Expr(&x.test));
                    stmts(&x.body, f);
                    stmts(&x.orelse, f);
                }
                Stmt::If(x) => {
                    f(NodeRef::Expr(&x.test));
                    stmts(&x.body, f);
                    stmts(&x.orelse, f);
                }
                Stmt::With(x) => {
                    for i in &x.items {
                        f(NodeRef::WithItem(i));
                    }
                    stmts(&x.body, f);
                }
                Stmt::Match(x) => {
                    f(NodeRef::Expr(&x.subject));
                    for c in &x.cases {
                        f(NodeRef::MatchCase(c));
                    }
                }
                Stmt::Raise(x) => {
                    opt(x.exc.as_ref(), f);
                    opt(x.cause.as_ref(), f);
                }
                Stmt::Try(x) => {
                    stmts(&x.body, f);
                    for h in &x.handlers {
                        f(NodeRef::ExceptHandler(h));
                    }
                    stmts(&x.orelse, f);
                    stmts(&x.finalbody, f);
                }
                Stmt::Assert(x) => {
                    f(NodeRef::Expr(&x.test));
                    opt(x.msg.as_ref(), f);
                }
                Stmt::Import(x) => {
                    for a in &x.names {
                        f(NodeRef::Alias(a));
                    }
                }
                Stmt::ImportFrom(x) => {
                    for a in &x.names {
                        f(NodeRef::Alias(a));
                    }
                }
                Stmt::Expr(x) => f(NodeRef::Expr(&x.value)),
                Stmt::Global(_) | Stmt::Nonlocal(_) | Stmt::Pass(_) | Stmt::Break(_) | Stmt::Continue(_) => {}
            },
            NodeRef::Expr(e) => match e {
                Expr::BoolOp(x) => exprs(&x.values, f),
                Expr::NamedExpr(x) => {
                    f(NodeRef::Expr(&x.target));
                    f(NodeRef::Expr(&x.value));
                }
                Expr::BinOp(x) => {
                    f(NodeRef::Expr(&x.left));
                    f(NodeRef::Expr(&x.right));
                }
                Expr::UnaryOp(x) => f(NodeRef::Expr(&x.operand)),
                Expr::Lambda(x) => {
                    f(NodeRef::Arguments(&x.args));
                    f(NodeRef::Expr(&x.body));
                }
                Expr::IfExp(x) => {
                    f(NodeRef::Expr(&x.test));
                    f(NodeRef::Expr(&x.body));
                    f(NodeRef::Expr(&x.orelse));
                }
                Expr::Dict(x) => {
                    for k in x.keys.iter().flatten() {
                        f(NodeRef::Expr(k));
                    }
                    exprs(&x.values, f);
                }
                Expr::Set(x) | Expr::List(x) | Expr::Tuple(x) => exprs(&x.elts, f),
                Expr::ListComp(x) | Expr::SetComp(x) | Expr::GeneratorExp(x) => {
                    f(NodeRef::Expr(&x.elt));
                    for g in &x.generators {
                        f(NodeRef::Comprehension(g));
                    }
                }
                Expr::DictComp(x) => {
                    f(NodeRef::Expr(&x.key));
                    f(NodeRef::Expr(&x.value));
                    for g in &x.generators {
                        f(NodeRef::Comprehension(g));
                    }
                }
                Expr::Await(x) | Expr::YieldFrom(x) => f(NodeRef::Expr(&x.value)),
                Expr::Yield(x) => opt(x.value.as_deref(), f),
                Expr::Compare(x) => {
                    f(NodeRef::Expr(&x.left));
                    exprs(&x.comparators, f);
                }
                Expr::Call(x) => {
                    f(NodeRef::Expr(&x.func));
                    exprs(&x.args, f);
                    for k in &x.keywords {
                        f(NodeRef::Keyword(k));
                    }
                }
                Expr::FormattedValue(x) => {
                    f(NodeRef::Expr(&x.value));
                    opt(x.format_spec.as_deref(), f);
                }
                Expr::JoinedStr(x) => exprs(&x.values, f),
                Expr::Attribute(x) => f(NodeRef::Expr(&x.value)),
                Expr::Subscript(x) => {
                    f(NodeRef::Expr(&x.value));
                    f(NodeRef::Expr(&x.slice));
                }
                Expr::Starred(x) => f(NodeRef::Expr(&x.value)),
                Expr::Slice(x) => {
                    opt(x.lower.as_deref(), f);
                    opt(x.upper.as_deref(), f);
                    opt(x.step.as_deref(), f);
                }
                Expr::Constant(_) | Expr::Name(_) => {}
            },
            NodeRef::Comprehension(c) => {
                f(NodeRef::Expr(&c.target));
                f(NodeRef::Expr(&c.iter));
                exprs(&c.ifs, f);
            }
            NodeRef::ExceptHandler(h) => {
                opt(h.type_.as_ref(), f);
                stmts(&h.body, f);
            }
            NodeRef::Arguments(a) => {
                let args = a.posonlyargs.iter().chain(&a.args);
                for x in args {
                    f(NodeRef::Arg(x));
                }
                if let Some(v) = &a.vararg {
                    f(NodeRef::Arg(v));
                }
                for x in &a.kwonlyargs {
                    f(NodeRef::Arg(x));
                }
                for d in a.kw_defaults.iter().flatten() {
                    f(NodeRef::Expr(d));
                }
                if let Some(k) = &a.kwarg {
                    f(NodeRef::Arg(k));
                }
                exprs(&a.defaults, f);
            }
            NodeRef::Arg(a) => opt(a.annotation.as_deref(), f),
            NodeRef::Keyword(k) => f(NodeRef::Expr(&k.value)),
            NodeRef::Alias(_) => {}
            NodeRef::WithItem(w) => {
                f(NodeRef::Expr(&w.context_expr));
                opt(w.optional_vars.as_ref(), f);
            }
            NodeRef::MatchCase(c) => {
                f(NodeRef::Pattern(&c.pattern));
                opt(c.guard.as_ref(), f);
                stmts(&c.body, f);
            }
            NodeRef::Pattern(p) => match p {
                Pattern::MatchValue { value, .. } => f(NodeRef::Expr(value)),
                Pattern::MatchSingleton { .. } | Pattern::MatchStar { .. } => {}
                Pattern::MatchSequence { patterns, .. } | Pattern::MatchOr { patterns, .. } => {
                    for p in patterns {
                        f(NodeRef::Pattern(p));
                    }
                }
                Pattern::MatchMapping { keys, patterns, .. } => {
                    exprs(keys, f);
                    for p in patterns {
                        f(NodeRef::Pattern(p));
                    }
                }
                Pattern::MatchClass { cls, patterns, kwd_patterns, .. } => {
                    f(NodeRef::Expr(cls));
                    for p in patterns.iter().chain(kwd_patterns) {
                        f(NodeRef::Pattern(p));
                    }
                }
                Pattern::MatchAs { pattern, .. } => {
                    if let Some(p) = pattern {
                        f(NodeRef::Pattern(p));
                    }
                }
            },
            NodeRef::TypeParam(t) => match t {
                TypeParam::TypeVar { bound, default_value, .. } => {
                    opt(bound.as_deref(), f);
                    opt(default_value.as_deref(), f);
                }
                TypeParam::ParamSpec { default_value, .. } | TypeParam::TypeVarTuple { default_value, .. } => {
                    opt(default_value.as_deref(), f);
                }
            },
        }
    }

    /// Return the child nodes in CPython's field order.
    pub fn children(self) -> Vec<NodeRef<'a>> {
        let mut out = Vec::new();
        self.for_each_child(&mut |c| out.push(c));
        out
    }

    /// Return CPython's class name of the node, e.g. "comprehension" or "MatchAs".
    pub fn type_name(self) -> &'static str {
        match self {
            NodeRef::Module(_) => "Module",
            NodeRef::Stmt(s) => s.type_name(),
            NodeRef::Expr(e) => e.type_name(),
            NodeRef::Comprehension(_) => "comprehension",
            NodeRef::ExceptHandler(_) => "ExceptHandler",
            NodeRef::Arguments(_) => "arguments",
            NodeRef::Arg(_) => "arg",
            NodeRef::Keyword(_) => "keyword",
            NodeRef::Alias(_) => "alias",
            NodeRef::WithItem(_) => "withitem",
            NodeRef::MatchCase(_) => "match_case",
            NodeRef::Pattern(p) => p.type_name(),
            NodeRef::TypeParam(t) => t.type_name(),
        }
    }

    /// Return the node's CPython position, or None for the kinds CPython gives none.
    pub fn pos(self) -> Option<Pos> {
        match self {
            NodeRef::Stmt(s) => Some(s.pos()),
            NodeRef::Expr(e) => Some(e.pos()),
            NodeRef::ExceptHandler(h) => Some(h.pos),
            NodeRef::Arg(a) => Some(a.pos),
            NodeRef::Keyword(k) => Some(k.pos),
            NodeRef::Alias(a) => Some(a.pos),
            NodeRef::Pattern(p) => Some(p.pos()),
            NodeRef::TypeParam(t) => Some(t.pos()),
            NodeRef::Module(_)
            | NodeRef::Comprehension(_)
            | NodeRef::Arguments(_)
            | NodeRef::WithItem(_)
            | NodeRef::MatchCase(_) => None,
        }
    }
}

/// Return every node under `root`, `root` included, breadth-first like `ast.walk`.
pub fn walk(root: NodeRef<'_>) -> Vec<NodeRef<'_>> {
    let mut out = Vec::new();
    let mut todo = VecDeque::from([root]);
    while let Some(n) = todo.pop_front() {
        out.push(n);
        n.for_each_child(&mut |c| todo.push_back(c));
    }
    out
}
