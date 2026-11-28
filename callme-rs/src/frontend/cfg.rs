//! Step 1b: one control-flow graph per function (module body, def, lambda).
//!
//! Program points (see `model::graphs::PointKind`):
//!
//! ```text
//!   entry, exit       one each per function
//!   stmt              a statement's own effect, after the calls inside it
//!   branch            a test; successors are labelled true / false
//!   loop              loop head (while join point, for / comprehension "next item")
//!   handler           start of an except clause
//!   call, after_call  every call site is split in two, joined by a call_to_return edge; the
//!                     ICFG adds call edges out of `call` and return edges into `after_call`
//! ```
//!
//! Calls are emitted in Python evaluation order. Nested functions are not entered (their
//! decorators and defaults are evaluated here, their bodies get their own CFG); class bodies
//! are inlined because they run in the enclosing function.
//!
//! Exceptions: `raise`, failed `assert`, and every point inside a `try` body go to that try's
//! handlers (exc edges); `return` / `break` / `continue` / `raise` leaving a `try ... finally`
//! run the finally block first. Implicit exceptions outside any `try` are not modelled.
//!
//! Every statement gets a node even when unreachable (dead code), because the call graph
//! analysis still reports call sites in dead code (see `Interp::block`).

use indexmap::{IndexMap, IndexSet};
use rustc_hash::FxBuildHasher;

use crate::model::{CFG, CFGEdge, CFGNode, EdgeKind, FuncId, PointKind, Site, SiteKind};
use crate::syntax::ast::{BoolOp, Comprehension, Expr, Pattern, Pos, Stmt};
use crate::syntax::unparse;
use crate::syntax::walk::NodeRef;

use super::program::{Body, FuncInfo};
use super::sites::{cut, make_site};

/// Dangling (source point, edge kind) pairs.
type Frontier = Vec<(u32, EdgeKind)>;

/// An abrupt exit from the normal flow.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Jump {
    /// `return`
    Return,
    /// `raise`, or a failed `assert`
    Raise,
    /// `break`
    Break,
    /// `continue`
    Continue,
}

/// An enclosing construct that decides where a jump goes.
enum Frame {
    /// a loop being built: its head point and the `break` exits waiting for its end
    Loop {
        /// the loop head
        head: u32,
        /// `break` exits
        breaks: Frontier,
    },
    /// a `try` body being built: the exception sources that go to its handlers
    Try {
        /// exception sources
        exc: Frontier,
    },
    /// a `try ... finally`: abrupt exits that must run the finally block first
    Finally {
        /// (frontier, jump kind) of each pending exit
        pending: Vec<(Frontier, Jump)>,
    },
}

/// Return the first line of `text`, cut to `limit` characters.
fn first_line(text: &str, limit: usize) -> String {
    cut(text.split('\n').next().unwrap_or(""), limit)
}

/// Return the first line of an expression's source, cut to `limit` characters (`short`).
fn short_expr(e: &Expr, limit: usize) -> String {
    first_line(&unparse::expr_prefix(e, limit + 1), limit)
}

/// Return the first line of a statement's source, cut to 50 characters.
fn short_stmt(s: &Stmt) -> String {
    first_line(&unparse::stmt_first_line(s, Some(51)), 50)
}

/// Return the first line of a pattern's source, cut to 50 characters.
fn short_pattern(p: &Pattern) -> String {
    first_line(&unparse::pattern_prefix(p, 51), 50)
}

/// Builder of one function's CFG, fed statements and expressions in evaluation order.
///
/// The frontier is the list of dangling (point, edge kind) pairs that the next emitted point is
/// linked from; it is empty after a jump, which makes the code that follows unreachable.
/// `frames` is the stack of enclosing loops, `try` bodies and finally blocks that decide where
/// a jump goes.
pub struct Builder {
    /// the function whose body is being built
    fid: FuncId,
    /// points so far
    nodes: Vec<CFGNode>,
    /// edges so far, in insertion order, without duplicates
    edges: IndexSet<CFGEdge, FxBuildHasher>,
    /// call site -> (CALL point, AFTER_CALL point)
    calls: IndexMap<Site, (u32, u32), FxBuildHasher>,
    /// enclosing loops, try bodies and finally blocks, innermost last
    frames: Vec<Frame>,
    /// dangling (point, kind) pairs the next point is linked from
    frontier: Frontier,
    /// the exit point
    exit: u32,
}

impl Builder {
    /// Create the entry and exit points and set the frontier to the entry.
    pub fn new(fid: FuncId) -> Builder {
        let mut b = Builder {
            fid,
            nodes: Vec::new(),
            edges: IndexSet::default(),
            calls: IndexMap::default(),
            frames: Vec::new(),
            frontier: Vec::new(),
            exit: 1,
        };
        let entry = b.new_point(PointKind::Entry, None, "entry".into(), None);
        b.exit = b.new_point(PointKind::Exit, None, "exit".into(), None);
        b.frontier = vec![(entry, EdgeKind::Flow)];
        b
    }

    // ------------------------------------------------------------ plumbing

    /// Add a point without linking it, and return its index.
    fn new_point(&mut self, kind: PointKind, pos: Option<Pos>, text: String, site: Option<Site>) -> u32 {
        let idx = u32::try_from(self.nodes.len()).expect("fewer than 2^32 points");
        let p = pos.unwrap_or_default();
        self.nodes.push(CFGNode { idx, kind, line: p.line, col: p.col, text, site });
        idx
    }

    /// Add an edge from every (point, kind) pair in `src` to point `dst`.
    fn link(&mut self, src: &[(u32, EdgeKind)], dst: u32) {
        for &(s, k) in src {
            self.edges.insert(CFGEdge { src: s, dst, kind: k });
        }
    }

    /// Add a point linked from the frontier, make it the new frontier, and return its index.
    ///
    /// Inside a `try` body the point also gets an exception edge to the handlers, because any
    /// point there may raise; inside a finally block it is recorded as a pending raise instead.
    fn emit(&mut self, kind: PointKind, pos: Option<Pos>, text: String, site: Option<Site>) -> u32 {
        let idx = self.new_point(kind, pos, text, site);
        let frontier = std::mem::take(&mut self.frontier);
        self.link(&frontier, idx);
        self.frontier = vec![(idx, EdgeKind::Flow)];
        for f in self.frames.iter_mut().rev() {
            match f {
                Frame::Try { exc } => {
                    exc.push((idx, EdgeKind::Exc));
                    break;
                }
                Frame::Finally { pending } => {
                    pending.push((vec![(idx, EdgeKind::Exc)], Jump::Raise));
                    break;
                }
                Frame::Loop { .. } => {}
            }
        }
        idx
    }

    /// Leave the frontier abruptly with `kind`.
    ///
    /// The nearest frame that handles the jump receives it: a finally block delays it until the
    /// block has run, a `try` body sends a raise to its handlers, and a loop takes `break` to its
    /// end and `continue` to its head. With no such frame the jump goes to the exit, as an
    /// exception edge for a raise. The frontier is empty afterwards.
    fn jump(&mut self, kind: Jump) {
        let src = std::mem::take(&mut self.frontier);
        if src.is_empty() {
            return;
        }
        let mut link_to: Option<u32> = None;
        for f in self.frames.iter_mut().rev() {
            match f {
                Frame::Finally { pending } => {
                    pending.push((src, kind));
                    return;
                }
                Frame::Try { exc } if kind == Jump::Raise => {
                    exc.extend(src.iter().map(|&(s, _)| (s, EdgeKind::Exc)));
                    return;
                }
                Frame::Loop { head, breaks } if matches!(kind, Jump::Break | Jump::Continue) => {
                    if kind == Jump::Break {
                        breaks.extend(src.iter().copied());
                        return;
                    }
                    link_to = Some(*head);
                    break;
                }
                _ => {}
            }
        }
        if let Some(head) = link_to {
            self.link(&src, head);
            return;
        }
        let out: Frontier =
            src.iter().map(|&(s, k)| (s, if kind == Jump::Raise { EdgeKind::Exc } else { k })).collect();
        let exit = self.exit;
        self.link(&out, exit);
    }

    /// Emit a BRANCH point for a test and return it; callers link its TRUE and FALSE sides.
    fn branch(&mut self, pos: Pos, text: String) -> u32 {
        self.emit(PointKind::Branch, Some(pos), text, None)
    }

    // ------------------------------------------------------------ expressions

    /// Emit the points for the calls inside any syntax node, by its kind (`expr` on a node
    /// that is not an expression visits its children).
    fn any(&mut self, n: NodeRef<'_>) {
        match n {
            NodeRef::Expr(e) => self.expr(e),
            other => other.for_each_child(&mut |c| self.any(c)),
        }
    }

    /// Emit the points for the calls inside expression `e`, in Python's evaluation order.
    ///
    /// `a if c else b`, `and` / `or` and comprehensions add BRANCH or LOOP points, because the
    /// calls in them may not run. A lambda's body is not entered (it has its own CFG); only its
    /// default values are. Expressions without calls emit nothing.
    fn expr(&mut self, e: &Expr) {
        match e {
            Expr::Call(c) => {
                self.expr(&c.func);
                for a in &c.args {
                    self.expr(a);
                }
                for k in &c.keywords {
                    self.expr(&k.value);
                }
                let site = make_site(self.fid, e, SiteKind::Call);
                self.call_point(c.pos, site);
            }
            Expr::Lambda(l) => {
                for d in l.args.defaults.iter().chain(l.args.kw_defaults.iter().flatten()) {
                    self.expr(d);
                }
            }
            Expr::IfExp(x) => {
                self.expr(&x.test);
                let b = self.branch(x.pos, short_expr(&x.test, 50));
                self.frontier = vec![(b, EdgeKind::True)];
                self.expr(&x.body);
                let then = std::mem::replace(&mut self.frontier, vec![(b, EdgeKind::False)]);
                self.expr(&x.orelse);
                let rest = std::mem::take(&mut self.frontier);
                self.frontier = [then, rest].concat();
            }
            Expr::BoolOp(x) => {
                let mut done: Frontier = Vec::new();
                let (stop, go) = if x.op == BoolOp::Or {
                    (EdgeKind::True, EdgeKind::False)
                } else {
                    (EdgeKind::False, EdgeKind::True)
                };
                self.expr(&x.values[0]);
                for pair in x.values.windows(2) {
                    let b = self.branch(pair[0].pos(), short_expr(&pair[0], 50));
                    done.push((b, stop));
                    self.frontier = vec![(b, go)];
                    self.expr(&pair[1]);
                }
                let rest = std::mem::take(&mut self.frontier);
                self.frontier = [done, rest].concat();
            }
            Expr::ListComp(c) | Expr::SetComp(c) | Expr::GeneratorExp(c) => {
                self.comprehension(c.pos, &c.generators, &[&c.elt]);
            }
            Expr::DictComp(c) => self.comprehension(c.pos, &c.generators, &[&c.key, &c.value]),
            Expr::Dict(d) => {
                for (k, v) in d.keys.iter().zip(&d.values) {
                    if let Some(k) = k {
                        self.expr(k);
                    }
                    self.expr(v);
                }
            }
            other => NodeRef::Expr(other).for_each_child(&mut |c| self.any(c)),
        }
    }

    /// Emit the CALL and AFTER_CALL points of `site`, joined by a call-to-return edge.
    ///
    /// The ICFG later adds call edges out of the CALL point and return edges into the AFTER_CALL
    /// point. `site` must be the `Site` that `sites::make_site` gives the same node.
    fn call_point(&mut self, pos: Pos, site: Site) {
        let c = self.emit(PointKind::Call, Some(pos), site.text.to_string(), Some(site));
        self.frontier = vec![(c, EdgeKind::CallToReturn)];
        let r = self.emit(PointKind::AfterCall, Some(pos), site.text.to_string(), Some(site));
        self.calls.insert(site, (c, r));
    }

    /// Emit a comprehension as one loop whose head serves all its generators.
    ///
    /// elts  the element expression, or the key and value of a dict comprehension
    fn comprehension(&mut self, pos: Pos, gens: &[Comprehension], elts: &[&Expr]) {
        self.expr(&gens[0].iter);
        let text = format!("for {} in {}", short_expr(&gens[0].target, 50), short_expr(&gens[0].iter, 50));
        let head = self.emit(PointKind::Loop, Some(pos), text, None);
        self.frontier = vec![(head, EdgeKind::True)];
        let mut back: Frontier = Vec::new();
        for (i, g) in gens.iter().enumerate() {
            self.expr(&g.target);
            if i > 0 {
                self.expr(&g.iter);
            }
            for cond in &g.ifs {
                self.expr(cond);
                let b = self.branch(cond.pos(), short_expr(cond, 50));
                back.push((b, EdgeKind::False));
                self.frontier = vec![(b, EdgeKind::True)];
            }
        }
        for e in elts {
            self.expr(e);
        }
        let mut src = std::mem::take(&mut self.frontier);
        src.extend(back);
        self.link(&src, head);
        self.frontier = vec![(head, EdgeKind::False)];
    }

    // ------------------------------------------------------------ statements

    /// Emit the statements in order.
    fn block(&mut self, stmts: &[Stmt]) {
        for s in stmts {
            self.stmt(s);
        }
    }

    /// Emit one statement, by its kind.
    ///
    /// A statement without a rule of its own is simple: its expressions are emitted, then one
    /// STMT point for its own effect.
    #[allow(clippy::too_many_lines)]
    fn stmt(&mut self, s: &Stmt) {
        let pos = Some(s.pos());
        match s {
            Stmt::Assign(x) => {
                self.expr(&x.value);
                for t in &x.targets {
                    self.expr(t);
                }
                self.emit(PointKind::Stmt, pos, short_stmt(s), None);
            }
            Stmt::Return(x) => {
                if let Some(v) = &x.value {
                    self.expr(v);
                }
                self.emit(PointKind::Stmt, pos, short_stmt(s), None);
                self.jump(Jump::Return);
            }
            Stmt::Raise(x) => {
                for e in x.exc.iter().chain(&x.cause) {
                    self.expr(e);
                }
                self.emit(PointKind::Stmt, pos, short_stmt(s), None);
                self.jump(Jump::Raise);
            }
            Stmt::Break(_) => {
                self.emit(PointKind::Stmt, pos, "break".into(), None);
                self.jump(Jump::Break);
            }
            Stmt::Continue(_) => {
                self.emit(PointKind::Stmt, pos, "continue".into(), None);
                self.jump(Jump::Continue);
            }
            Stmt::Assert(x) => {
                self.expr(&x.test);
                let b = self.branch(x.pos, short_expr(&x.test, 50));
                self.frontier = vec![(b, EdgeKind::False)];
                if let Some(m) = &x.msg {
                    self.expr(m);
                }
                self.jump(Jump::Raise);
                self.frontier = vec![(b, EdgeKind::True)];
            }
            Stmt::If(x) => {
                self.expr(&x.test);
                let b = self.branch(x.pos, short_expr(&x.test, 50));
                self.frontier = vec![(b, EdgeKind::True)];
                self.block(&x.body);
                let then = std::mem::replace(&mut self.frontier, vec![(b, EdgeKind::False)]);
                self.block(&x.orelse);
                let rest = std::mem::take(&mut self.frontier);
                self.frontier = [then, rest].concat();
            }
            Stmt::While(x) => {
                let head = self.emit(PointKind::Loop, pos, "while".into(), None);
                self.expr(&x.test);
                let b = self.branch(x.test.pos(), short_expr(&x.test, 50));
                self.loop_body(head, vec![(b, EdgeKind::True)], vec![(b, EdgeKind::False)], &x.body, &x.orelse);
            }
            Stmt::For(x) => {
                self.expr(&x.iter);
                let text = format!("for {} in {}", short_expr(&x.target, 50), short_expr(&x.iter, 50));
                let head = self.emit(PointKind::Loop, pos, text, None);
                self.frontier = vec![(head, EdgeKind::True)];
                self.expr(&x.target); // subscript / attribute targets may contain calls
                let enter = std::mem::take(&mut self.frontier);
                self.loop_body(head, enter, vec![(head, EdgeKind::False)], &x.body, &x.orelse);
            }
            Stmt::With(x) => {
                for it in &x.items {
                    self.expr(&it.context_expr);
                    if let Some(v) = &it.optional_vars {
                        self.expr(v);
                    }
                }
                let items: Vec<String> = x.items.iter().map(|i| short_expr(&i.context_expr, 30)).collect();
                self.emit(PointKind::Stmt, pos, format!("with {}", items.join(", ")), None);
                self.block(&x.body);
            }
            Stmt::Try(x) => self.try_stmt(x),
            Stmt::FunctionDef(d) => {
                for e in &d.decorator_list {
                    self.expr(e);
                }
                for e in d.args.defaults.iter().chain(d.args.kw_defaults.iter().flatten()) {
                    self.expr(e);
                }
                self.decorate(&d.decorator_list);
                self.emit(PointKind::Stmt, pos, format!("def {}", d.name), None);
            }
            Stmt::ClassDef(c) => {
                for e in c.decorator_list.iter().chain(&c.bases).chain(c.keywords.iter().map(|k| &k.value)) {
                    self.expr(e);
                }
                self.block(&c.body); // the class body runs here
                self.decorate(&c.decorator_list);
                self.emit(PointKind::Stmt, pos, format!("class {}", c.name), None);
            }
            Stmt::Match(m) => {
                self.expr(&m.subject);
                let mut out: Frontier = Vec::new();
                for case in &m.cases {
                    self.any(NodeRef::Pattern(&case.pattern));
                    let b = self.branch(case.pattern.pos(), format!("case {}", short_pattern(&case.pattern)));
                    let mut miss = vec![(b, EdgeKind::False)];
                    self.frontier = vec![(b, EdgeKind::True)];
                    if let Some(g) = &case.guard {
                        self.expr(g);
                        let gb = self.branch(g.pos(), short_expr(g, 50));
                        miss.push((gb, EdgeKind::False));
                        self.frontier = vec![(gb, EdgeKind::True)];
                    }
                    self.block(&case.body);
                    out.extend(std::mem::replace(&mut self.frontier, miss));
                }
                let rest = std::mem::take(&mut self.frontier);
                out.extend(rest);
                self.frontier = out;
            }
            other => {
                // simple statement: its expressions, then its effect
                NodeRef::Stmt(other).for_each_child(&mut |c| self.any(c));
                self.emit(PointKind::Stmt, pos, short_stmt(other), None);
            }
        }
    }

    /// Emit a loop body from `enter` and link its end back to `head`, then the `else` block.
    ///
    /// The `else` block starts from `leave`, the normal exit of the loop; `break` exits skip it
    /// and join the frontier after it.
    fn loop_body(&mut self, head: u32, enter: Frontier, leave: Frontier, body: &[Stmt], orelse: &[Stmt]) {
        self.frames.push(Frame::Loop { head, breaks: Vec::new() });
        self.frontier = enter;
        self.block(body);
        let end = std::mem::take(&mut self.frontier);
        self.link(&end, head);
        let Some(Frame::Loop { breaks, .. }) = self.frames.pop() else {
            unreachable!("the loop frame is on top");
        };
        self.frontier = leave;
        self.block(orelse);
        self.frontier.extend(breaks);
    }

    /// Emit `try` or `try*` with its handlers, `else` block and finally block.
    ///
    /// Every point of the body may raise to each handler. Handler types are unknown, so an
    /// exception can also pass all handlers and is raised on. With a finally block, every exit
    /// from the body and the handlers (normal, return, raise, break or continue) runs the finally
    /// block first; afterwards each kind of abrupt exit continues to its own target, and the
    /// normal path continues only if one of the paths reached the end normally.
    fn try_stmt(&mut self, s: &crate::syntax::ast::Try) {
        let has_fin = !s.finalbody.is_empty();
        if has_fin {
            self.frames.push(Frame::Finally { pending: Vec::new() });
        }
        self.frames.push(Frame::Try { exc: Vec::new() });
        self.block(&s.body);
        let Some(Frame::Try { exc }) = self.frames.pop() else {
            unreachable!("the try frame is on top");
        };
        self.block(&s.orelse);
        let mut normal = std::mem::take(&mut self.frontier);
        for h in &s.handlers {
            self.frontier = exc.clone();
            if let Some(t) = &h.type_ {
                self.expr(t);
            }
            let mut text = String::from("except");
            if let Some(t) = &h.type_ {
                text.push(' ');
                text.push_str(&short_expr(t, 50));
            }
            if let Some(n) = h.name {
                text.push_str(" as ");
                text.push_str(&n);
            }
            self.emit(PointKind::Handler, Some(h.pos), text, None);
            self.block(&h.body);
            normal.extend(std::mem::take(&mut self.frontier));
        }
        self.frontier = exc; // not caught here (handler types are unknown)
        self.jump(Jump::Raise);
        if !has_fin {
            self.frontier = normal;
            return;
        }
        let Some(Frame::Finally { pending }) = self.frames.pop() else {
            unreachable!("the finally frame is on top");
        };
        let mut kinds: Vec<Jump> = Vec::new();
        for (_, k) in &pending {
            if !kinds.contains(k) {
                kinds.push(*k);
            }
        }
        let mut start = normal.clone();
        for (src, _) in &pending {
            start.extend(src.iter().copied());
        }
        self.frontier = start;
        self.block(&s.finalbody);
        let end = std::mem::take(&mut self.frontier);
        for k in kinds {
            // then continue the abrupt exits
            self.frontier = end.clone();
            self.jump(k);
        }
        self.frontier = if normal.is_empty() { Vec::new() } else { end };
    }

    /// Emit one decorator call point per decorator, applied from the bottom one up.
    fn decorate(&mut self, decorators: &[Expr]) {
        for d in decorators.iter().rev() {
            let site = make_site(self.fid, d, SiteKind::Decorator);
            self.call_point(d.pos(), site);
        }
    }

    // ------------------------------------------------------------ result

    /// Link the frontier to the exit and return the finished CFG.
    pub fn finish(mut self) -> CFG {
        let frontier = std::mem::take(&mut self.frontier);
        let exit = self.exit;
        self.link(&frontier, exit);
        CFG::new(self.fid, self.nodes, self.edges.into_iter().collect(), self.calls)
    }
}

/// Build the CFG of one function.
///
/// A lambda's body expression is emitted, followed by an implicit `return`.
pub fn build_cfg(info: &FuncInfo) -> CFG {
    let mut b = Builder::new(info.id);
    match info.body() {
        Body::Expr(e) => {
            b.expr(e);
            b.emit(PointKind::Stmt, Some(e.pos()), format!("return {}", short_expr(e, 50)), None);
        }
        Body::Stmts(stmts) => b.block(stmts),
    }
    b.finish()
}
