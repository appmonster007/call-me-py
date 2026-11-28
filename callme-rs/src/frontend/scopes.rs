//! Scope analysis: the locals, `global` / `nonlocal` declarations and must-writes of a scope.
//!
//! Everything here reads syntax only. `walk_scope` visits a scope's own nodes without entering
//! nested functions or classes, and the other functions are built on it.

use crate::syntax::Name;
use crate::syntax::ast::{Arguments, Ctx, Expr, Pattern, Stmt};
use crate::syntax::walk::NodeRef;

use super::program::{DefaultRef, NameSet, Sig};

/// Return the parts of a def, lambda or class that run in the enclosing scope.
///
/// These are the decorators, default values, base classes and class keywords: they are
/// evaluated where the definition is, not inside it. Other nodes have none.
pub fn outer_parts(n: NodeRef<'_>) -> Vec<NodeRef<'_>> {
    let mut out = Vec::new();
    match n {
        NodeRef::Stmt(Stmt::ClassDef(c)) => {
            out.extend(c.decorator_list.iter().map(NodeRef::Expr));
            out.extend(c.bases.iter().map(NodeRef::Expr));
            out.extend(c.keywords.iter().map(|k| NodeRef::Expr(&k.value)));
        }
        NodeRef::Stmt(Stmt::FunctionDef(d)) => {
            out.extend(d.decorator_list.iter().map(NodeRef::Expr));
            defaults(&d.args, &mut out);
        }
        NodeRef::Expr(Expr::Lambda(l)) => defaults(&l.args, &mut out),
        _ => {}
    }
    out
}

/// Add the default values of `a`, positional ones first, to `out`.
fn defaults<'a>(a: &'a Arguments, out: &mut Vec<NodeRef<'a>>) {
    out.extend(a.defaults.iter().map(NodeRef::Expr));
    out.extend(a.kw_defaults.iter().flatten().map(NodeRef::Expr));
}

/// Return True for a def, lambda or class (a node that opens a scope).
pub fn is_scope_node(n: NodeRef<'_>) -> bool {
    matches!(n, NodeRef::Stmt(Stmt::FunctionDef(_) | Stmt::ClassDef(_)) | NodeRef::Expr(Expr::Lambda(_)))
}

/// Iterator over the nodes of one scope; see `walk_scope`.
pub struct WalkScope<'a> {
    /// nodes still to visit, the next one last
    stack: Vec<NodeRef<'a>>,
}

impl<'a> Iterator for WalkScope<'a> {
    type Item = NodeRef<'a>;

    fn next(&mut self) -> Option<NodeRef<'a>> {
        let n = self.stack.pop()?;
        let at = self.stack.len();
        if is_scope_node(n) {
            self.stack.extend(outer_parts(n));
        } else {
            n.for_each_child(&mut |c| self.stack.push(c));
        }
        self.stack[at..].reverse();
        Some(n)
    }
}

/// Return every node that belongs to one scope, in source order.
///
/// Nested defs, lambdas and classes are yielded, together with their parts that run in this
/// scope (`outer_parts`), but their bodies are not entered.
pub fn walk_scope<'a>(nodes: impl IntoIterator<Item = NodeRef<'a>>) -> WalkScope<'a> {
    let mut stack: Vec<NodeRef<'a>> = nodes.into_iter().collect();
    stack.reverse();
    WalkScope { stack }
}

/// Return the `Sig` of a function's parameter list.
pub fn make_sig(args: &Arguments) -> Sig {
    let pos: Vec<Name> = args.posonlyargs.iter().chain(&args.args).map(|a| a.arg).collect();
    let mut defaults: Vec<(Name, DefaultRef)> = Vec::new();
    let mut set = |name: Name, d: DefaultRef| match defaults.iter_mut().find(|(n, _)| *n == name) {
        Some(slot) => slot.1 = d,
        None => defaults.push((name, d)),
    };
    let first = pos.len() - args.defaults.len().min(pos.len());
    for (i, name) in pos[first..].iter().enumerate() {
        set(*name, DefaultRef::Pos(i));
    }
    let kwonly: Vec<Name> = args.kwonlyargs.iter().map(|a| a.arg).collect();
    for (i, (name, d)) in kwonly.iter().zip(&args.kw_defaults).enumerate() {
        if d.is_some() {
            set(*name, DefaultRef::Kw(i));
        }
    }
    let required = pos.iter().chain(&kwonly).filter(|n| !defaults.iter().any(|(d, _)| d == *n)).copied().collect();
    Sig {
        pos,
        kwonly,
        vararg: args.vararg.as_ref().map(|a| a.arg),
        kwarg: args.kwarg.as_ref().map(|a| a.arg),
        defaults,
        required,
    }
}

/// Return the names that one `match` pattern node binds itself (not its sub-patterns).
pub fn pattern_names(n: NodeRef<'_>) -> Vec<Name> {
    match n {
        NodeRef::Pattern(Pattern::MatchAs { name, .. } | Pattern::MatchStar { name, .. }) => {
            name.iter().copied().collect()
        }
        NodeRef::Pattern(Pattern::MatchMapping { rest, .. }) => rest.iter().copied().collect(),
        _ => Vec::new(),
    }
}

/// Return the name an import alias binds: its `asname`, or the first part of its name.
pub fn alias_binding(name: Name, asname: Option<Name>) -> Name {
    asname.unwrap_or_else(|| match name.split_once('.') {
        Some((first, _)) => Name::new(first),
        None => name,
    })
}

/// Return the names local to one scope: its parameters and every name the scope binds.
///
/// Assignments, deletions, defs, classes, imports, `except ... as` and `match` captures
/// count; names declared `global` or `nonlocal` are removed.
pub fn scope_locals<'a>(body: impl IntoIterator<Item = NodeRef<'a>>, params: &[Name]) -> NameSet {
    let mut names: NameSet = params.iter().copied().collect();
    let mut declared = NameSet::default();
    for n in walk_scope(body) {
        match n {
            NodeRef::Expr(Expr::Name(x)) if matches!(x.ctx, Ctx::Store | Ctx::Del) => {
                names.insert(x.id);
            }
            NodeRef::Stmt(Stmt::FunctionDef(d)) => {
                names.insert(d.name);
            }
            NodeRef::Stmt(Stmt::ClassDef(c)) => {
                names.insert(c.name);
            }
            NodeRef::Stmt(Stmt::Import(i)) => {
                names.extend(i.names.iter().filter(|a| &*a.name != "*").map(|a| alias_binding(a.name, a.asname)));
            }
            NodeRef::Stmt(Stmt::ImportFrom(i)) => {
                names.extend(i.names.iter().filter(|a| &*a.name != "*").map(|a| alias_binding(a.name, a.asname)));
            }
            NodeRef::ExceptHandler(h) => {
                if let Some(name) = h.name {
                    names.insert(name);
                }
            }
            NodeRef::Stmt(Stmt::Global(g) | Stmt::Nonlocal(g)) => declared.extend(g.names.iter().copied()),
            _ => {}
        }
        names.extend(pattern_names(n));
    }
    names.retain(|n| !declared.contains(n));
    names
}

/// Return the names declared `global` and those declared `nonlocal` in one scope.
pub fn scope_decls<'a>(body: impl IntoIterator<Item = NodeRef<'a>>) -> (NameSet, NameSet) {
    let mut glob = NameSet::default();
    let mut nonloc = NameSet::default();
    for n in walk_scope(body) {
        match n {
            NodeRef::Stmt(Stmt::Global(g)) => glob.extend(g.names.iter().copied()),
            NodeRef::Stmt(Stmt::Nonlocal(g)) => nonloc.extend(g.names.iter().copied()),
            _ => {}
        }
    }
    (glob, nonloc)
}

/// Return the names in `tracked` directly bound by assignment target `t`.
fn target_names(t: &Expr, tracked: &NameSet, out: &mut NameSet) {
    match t {
        Expr::Name(x) => {
            if tracked.contains(&x.id) {
                out.insert(x.id);
            }
        }
        Expr::Tuple(x) | Expr::List(x) => {
            for e in &x.elts {
                target_names(e, tracked, out);
            }
        }
        Expr::Starred(x) => target_names(&x.value, tracked, out),
        _ => {}
    }
}

/// Return the names in `tracked` unconditionally assigned by simple statement `s`.
fn stmt_writes(s: &Stmt, tracked: &NameSet) -> NameSet {
    let mut out = NameSet::default();
    match s {
        Stmt::Assign(x) => {
            for t in &x.targets {
                target_names(t, tracked, &mut out);
            }
        }
        Stmt::AnnAssign(x) => {
            if x.value.is_some() {
                target_names(&x.target, tracked, &mut out);
            }
        }
        Stmt::FunctionDef(d) => {
            if tracked.contains(&d.name) {
                out.insert(d.name);
            }
        }
        Stmt::ClassDef(c) => {
            if tracked.contains(&c.name) {
                out.insert(c.name);
            }
        }
        Stmt::Import(i) => {
            out.extend(
                i.names
                    .iter()
                    .filter(|a| &*a.name != "*")
                    .map(|a| alias_binding(a.name, a.asname))
                    .filter(|n| tracked.contains(n)),
            );
        }
        Stmt::ImportFrom(i) => {
            out.extend(
                i.names
                    .iter()
                    .filter(|a| &*a.name != "*")
                    .map(|a| alias_binding(a.name, a.asname))
                    .filter(|n| tracked.contains(n)),
            );
        }
        _ => {}
    }
    out
}

/// Return every name in `tracked` deleted anywhere in `stmts` (within the same scope).
fn all_deletes(stmts: &[Stmt], tracked: &NameSet) -> NameSet {
    let mut out = NameSet::default();
    for n in walk_scope(stmts.iter().map(NodeRef::Stmt)) {
        if let NodeRef::Stmt(Stmt::Delete(d)) = n {
            for t in &d.targets {
                if let Expr::Name(x) = t
                    && tracked.contains(&x.id)
                {
                    out.insert(x.id);
                }
            }
        }
    }
    out
}

/// Return True if match pattern `pat` always matches any subject.
fn is_irrefutable(pat: &Pattern) -> bool {
    match pat {
        Pattern::MatchAs { pattern, .. } => pattern.is_none(),
        Pattern::MatchOr { patterns, .. } => patterns.iter().any(is_irrefutable),
        _ => false,
    }
}

/// Return the intersection of the non-empty list `sets`.
fn intersect(sets: &[NameSet]) -> NameSet {
    let mut out = sets[0].clone();
    for s in &sets[1..] {
        out.retain(|n| s.contains(n));
    }
    out
}

/// Return `a` without the names in `b`.
fn minus(a: &NameSet, b: &NameSet) -> NameSet {
    a.iter().filter(|n| !b.contains(*n)).copied().collect()
}

/// The state of `scope_must_writes`: the must-written sets at each `return`, and at each
/// `break` of the enclosing loops.
struct MustWrites<'t> {
    /// the names whose writes are tracked
    tracked: &'t NameSet,
    /// one set per `return` reached
    returns: Vec<NameSet>,
    /// per enclosing loop, one set per `break`
    loop_breaks: Vec<Vec<NameSet>>,
}

impl MustWrites<'_> {
    /// Return (must-written set at fallthrough, whether fallthrough is reachable).
    fn walk(&mut self, seq: &[Stmt], cur: NameSet) -> (NameSet, bool) {
        let mut cur = cur;
        for s in seq {
            match s {
                Stmt::Return(_) => {
                    self.returns.push(cur.clone());
                    return (cur, false);
                }
                Stmt::Raise(_) | Stmt::Continue(_) => return (cur, false),
                Stmt::Break(_) => {
                    if let Some(b) = self.loop_breaks.last_mut() {
                        b.push(cur.clone());
                    }
                    return (cur, false);
                }
                Stmt::If(x) => {
                    let (m1, l1) = self.walk(&x.body, cur.clone());
                    let (m2, l2) = self.walk(&x.orelse, cur.clone());
                    let live: Vec<NameSet> =
                        [(m1, l1), (m2, l2)].into_iter().filter(|(_, ok)| *ok).map(|(m, _)| m).collect();
                    if live.is_empty() {
                        return (cur, false);
                    }
                    cur = intersect(&live);
                }
                Stmt::For(_) | Stmt::While(_) => {
                    let (body, orelse) = match s {
                        Stmt::For(f) => (&f.body, &f.orelse),
                        Stmt::While(w) => (&w.body, &w.orelse),
                        _ => unreachable!(),
                    };
                    let head = minus(&cur, &all_deletes(body, self.tracked));
                    self.loop_breaks.push(Vec::new());
                    self.walk(body, head.clone());
                    let breaks = self.loop_breaks.pop().unwrap_or_default();
                    let (m_else, l_else) = self.walk(orelse, head);
                    let mut exits: Vec<NameSet> = if l_else { vec![m_else] } else { Vec::new() };
                    exits.extend(breaks);
                    if exits.is_empty() {
                        return (cur, false);
                    }
                    cur = intersect(&exits);
                }
                Stmt::Try(t) => {
                    let ret_mark = self.returns.len();
                    let body_dels = all_deletes(&t.body, self.tracked);
                    let h_start = minus(&cur, &body_dels);
                    let (m_body, l_body) = self.walk(&t.body, cur.clone());
                    let (m_else, l_else) = if l_body { self.walk(&t.orelse, m_body) } else { (m_body, false) };
                    let mut ends: Vec<(NameSet, bool)> = vec![(m_else, l_else)];
                    for h in &t.handlers {
                        ends.push(self.walk(&h.body, h_start.clone()));
                    }
                    let live: Vec<NameSet> = ends.into_iter().filter(|(_, ok)| *ok).map(|(m, _)| m).collect();
                    if t.finalbody.is_empty() {
                        if live.is_empty() {
                            return (cur, false);
                        }
                        cur = intersect(&live);
                    } else {
                        let fin_dels = all_deletes(&t.finalbody, self.tracked);
                        if !fin_dels.is_empty() {
                            for r in &mut self.returns[ret_mark..] {
                                *r = minus(r, &fin_dels);
                            }
                        }
                        if live.is_empty() {
                            self.walk(&t.finalbody, h_start);
                            return (cur, false);
                        }
                        let (c, ok) = self.walk(&t.finalbody, intersect(&live));
                        cur = c;
                        if !ok {
                            return (cur, false);
                        }
                    }
                }
                Stmt::With(w) => {
                    let base = minus(&cur, &all_deletes(&w.body, self.tracked));
                    let (m_body, l_body) = self.walk(&w.body, cur.clone());
                    cur = if l_body { base.iter().filter(|n| m_body.contains(*n)).copied().collect() } else { base };
                }
                Stmt::Match(m) => {
                    let mut ends: Vec<(NameSet, bool)> =
                        m.cases.iter().map(|c| self.walk(&c.body, cur.clone())).collect();
                    let exhaustive = m.cases.iter().any(|c| c.guard.is_none() && is_irrefutable(&c.pattern));
                    if !exhaustive {
                        ends.push((cur.clone(), true));
                    }
                    let live: Vec<NameSet> = ends.into_iter().filter(|(_, ok)| *ok).map(|(m, _)| m).collect();
                    if live.is_empty() {
                        return (cur, false);
                    }
                    cur = intersect(&live);
                }
                Stmt::Delete(d) => {
                    let dels: NameSet = d
                        .targets
                        .iter()
                        .filter_map(|t| match t {
                            Expr::Name(x) if self.tracked.contains(&x.id) => Some(x.id),
                            _ => None,
                        })
                        .collect();
                    if !dels.is_empty() {
                        cur = minus(&cur, &dels);
                    }
                }
                _ => {
                    let writes = stmt_writes(s, self.tracked);
                    if !writes.is_empty() {
                        cur.extend(writes);
                    }
                }
            }
        }
        (cur, true)
    }
}

/// Return the names in `tracked` that a function definitely assigns on every normal-return
/// path.
///
/// Only a plain (not async) def counts, and not a generator, because calling those does not
/// run their body right away. Loops, `with` blocks and `try` handlers are treated
/// conservatively, so the result is always a sound under-approximation of the guaranteed
/// writes.
///
/// is_plain_def  True for a def that is not async
pub fn scope_must_writes(is_plain_def: bool, body: &[Stmt], tracked: &NameSet) -> NameSet {
    if tracked.is_empty() || !is_plain_def {
        return NameSet::default();
    }
    let yields = walk_scope(body.iter().map(NodeRef::Stmt))
        .any(|n| matches!(n, NodeRef::Expr(Expr::Yield(_) | Expr::YieldFrom(_))));
    if yields {
        return NameSet::default();
    }
    let mut w = MustWrites { tracked, returns: Vec::new(), loop_breaks: Vec::new() };
    let (end_must, falls_through) = w.walk(body, NameSet::default());
    let mut exits = w.returns;
    if falls_through {
        exits.push(end_must);
    }
    if exits.is_empty() { NameSet::default() } else { intersect(&exits) }
}

/// Return the names annotated in a class body or assigned as `self.<name>` in its methods.
pub fn class_known_attrs(body: &[Stmt]) -> NameSet {
    let mut names = NameSet::default();
    for n in walk_scope(body.iter().map(NodeRef::Stmt)) {
        match n {
            NodeRef::Stmt(Stmt::AnnAssign(a)) => {
                if let Expr::Name(x) = &a.target {
                    names.insert(x.id);
                }
            }
            NodeRef::Stmt(Stmt::FunctionDef(d)) => {
                let Some(self_name) = d.args.posonlyargs.iter().chain(&d.args.args).next().map(|a| a.arg) else {
                    continue;
                };
                for inner in walk_scope(d.body.iter().map(NodeRef::Stmt)) {
                    if let NodeRef::Expr(Expr::Attribute(at)) = inner
                        && at.ctx == Ctx::Store
                        && let Expr::Name(v) = &*at.value
                        && v.id == self_name
                    {
                        names.insert(at.attr);
                    }
                }
            }
            _ => {}
        }
    }
    names
}
