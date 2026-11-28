//! Layer 0 of the interpreter: the `Interp` struct and its constructor, the value helpers every
//! layer uses, and `bind_attr` and `LoopFrame`.

use std::sync::Arc;

use indexmap::IndexSet;
use rustc_hash::FxBuildHasher;

use crate::frontend::{FuncInfo, Program};
use crate::model::{ClassId, EMPTY, FuncId, NodeId, Site, TokId, Token, Vals, Values};
use crate::name;

use crate::analysis::state::{Env, State};

/// The call edges found by one run: (site, callee node) pairs.
pub type Calls = Vec<(Site, NodeId)>;

/// Flow mode: the environment where a path ends, and whether a live path gets there.
pub type End = (Env, bool);

/// Environments that leave one loop early: one per `break` and one per `continue`.
#[derive(Default)]
pub struct LoopFrame {
    /// the environment at each `break`
    pub breaks: Vec<Env>,
    /// the environment at each `continue`
    pub continues: Vec<Env>,
}

/// Abstract interpreter for one node: a function body run under one context.
///
/// It evaluates expressions to `Vals`, collects the call edges it finds in `calls`, and writes
/// shared facts (contexts, attributes, the heap) into `state`. In flow mode, `env` is the
/// environment at the current point, `summary` collects every value each local takes, and
/// `live` is False once the current path has ended.
///
/// The Python version is a chain of layer classes; here every layer is an `impl Interp` block
/// in the module of the same name (`names`, `modules`, `calls`, `attributes`, `expressions`,
/// `operators`, `statements`, `definitions`, `flow`), and `e_<expr>` / `s_<stmt>` dispatch is
/// a `match` in `eval` and `stmt`.
pub struct Interp<'a> {
    /// the program
    pub prog: &'a Program,
    /// the global state, written unless `readonly`
    pub state: &'a mut State,
    /// the node being run, or None when running a function with no context
    pub node: Option<NodeId>,
    /// evaluate without writing to `state` (default values)
    pub readonly: bool,
    /// the function being run
    pub info: &'a FuncInfo,
    /// flow mode: the locals at the current program point
    pub env: Env,
    /// the return value so far
    pub ret: Vals,
    /// the call edges found so far
    pub calls: Calls,
    /// the classes whose bodies are running, innermost last, with their namespaces
    pub class_stack: Vec<(ClassId, Env)>,
    /// flow-sensitive locals
    pub flow: bool,
    /// flow mode: every value each local takes anywhere (what `State.envs` stores)
    pub summary: Env,
    /// flow mode: False after return / raise / break / continue, until a live path joins
    pub live: bool,
    /// enclosing loops, innermost last
    pub loops: Vec<LoopFrame>,
    /// enclosing try / with bodies: the union of every state seen inside (may raise)
    pub tries: Vec<Env>,
    /// decorator sites that evaluated to `EMPTY` in this run
    pub empty_decos: IndexSet<Site, FxBuildHasher>,
    /// above 0 inside conditional expressions, stubs or implicit calls: no strong kills
    pub weak_depth: u32,
}

impl<'a> Interp<'a> {
    /// Prepare to interpret `node`, or function `func` with no context.
    ///
    /// ```text
    /// readonly  evaluate without writing to `state`; used for default values, which run in
    ///           the defining scope and are read by every context
    /// func      the function to use when `node` is None; locals then read the union over all
    ///           of its contexts
    /// ```
    pub fn new(
        prog: &'a Program,
        state: &'a mut State,
        node: Option<NodeId>,
        readonly: bool,
        func: Option<FuncId>,
    ) -> Interp<'a> {
        let fid = match node {
            Some(n) => state.values.node_func(n),
            None => func.expect("Interp needs a node or a function"),
        };
        let flow = state.flow;
        Interp {
            prog,
            state,
            node,
            readonly,
            info: prog.func(fid),
            env: Env::default(),
            ret: EMPTY,
            calls: Vec::new(),
            class_stack: Vec::new(),
            flow,
            summary: Env::default(),
            live: true,
            loops: Vec::new(),
            tries: Vec::new(),
            empty_decos: IndexSet::default(),
            weak_depth: 0,
        }
    }

    // ------------------------------------------------------------ value helpers

    /// Return the value table.
    pub fn v(&mut self) -> &mut Values {
        &mut self.state.values
    }

    /// Return the union of `a` and `b`.
    pub fn u(&mut self, a: Vals, b: Vals) -> Vals {
        self.state.values.union(a, b)
    }

    /// Return the value holding only `t`.
    pub fn single(&mut self, t: TokId) -> Vals {
        self.state.values.single(t)
    }

    /// Intern `tok` and return the value holding only it.
    pub fn one(&mut self, tok: Token) -> Vals {
        let t = self.state.values.tok(tok);
        self.state.values.single(t)
    }

    /// Return the tokens of `v`.
    pub fn toks(&self, v: Vals) -> Arc<[TokId]> {
        self.state.values.toks(v)
    }

    /// Return the token behind `t` (cloned, so the table may grow while it is used).
    pub fn tok(&self, t: TokId) -> Token {
        self.state.values.get(t).clone()
    }

    /// Return the value of `f(t)` for every token of `v`.
    pub fn map_vals(&mut self, v: Vals, mut f: impl FnMut(&mut Interp<'a>, TokId) -> TokId) -> Vals {
        let toks = self.toks(v);
        let out: Vec<TokId> = toks.iter().map(|&t| f(self, t)).collect();
        self.state.values.of(out)
    }

    /// Return `v` with each token bound as an attribute read through `receiver` (`bind_attr`).
    pub fn bind_all(&mut self, v: Vals, owner_cls: ClassId, receiver: TokId) -> Vals {
        self.map_vals(v, |it, t| bind_attr(&mut it.state.values, t, owner_cls, receiver))
    }

    /// Return the class of an `Instance` token, or None for other tokens.
    pub fn instance_cls(&self, t: TokId) -> Option<ClassId> {
        match self.state.values.get(t) {
            Token::Instance { cls, .. } => Some(*cls),
            _ => None,
        }
    }

    /// Run `f` with `weak_depth` raised by one, as the Python version's `try` / `finally` does.
    pub fn weakly<T>(&mut self, f: impl FnOnce(&mut Interp<'a>) -> T) -> T {
        self.weak_depth += 1;
        let out = f(self);
        self.weak_depth -= 1;
        out
    }

    /// Return the `Site` of `e` as made by `make_site`, for the current function.
    pub fn site(&self, e: &crate::syntax::ast::Expr, kind: crate::model::SiteKind) -> Site {
        self.prog.site(self.info.id, e, kind)
    }
}

/// Return `tok` bound as Python binds an attribute read through `receiver`.
///
/// A classmethod binds the owner class `owner_cls`, a staticmethod gives its function, and a
/// function read through an instance becomes a bound method. `partialmethod` and
/// `singledispatchmethod` tokens bind `receiver` as their first argument. Other tokens are
/// unchanged.
pub fn bind_attr(v: &mut Values, tok: TokId, owner_cls: ClassId, receiver: TokId) -> TokId {
    match v.get(tok).clone() {
        Token::ClassMethod(f) => {
            let cls = v.tok(Token::ClassObj(owner_cls));
            return v.tok(Token::BoundMethod { func: f, receiver: cls });
        }
        Token::StaticMethod(f) => return f,
        _ => {}
    }
    if !matches!(v.get(receiver), Token::Instance { .. }) {
        return tok;
    }
    match v.get(tok).clone() {
        Token::Closure { .. } => v.tok(Token::BoundMethod { func: tok, receiver }),
        Token::Builtin { name, bound, bound_kw } => {
            let recv = v.single(receiver);
            if name == name!("<dataclass_init>") {
                v.tok(Token::BoundMethod { func: tok, receiver })
            } else if name == name!("<partialmethod>") && !bound.is_empty() {
                let mut b = vec![bound[0], recv];
                b.extend(&bound[1..]);
                v.tok(Token::Builtin { name: name!("<partial>"), bound: Arc::from(b), bound_kw })
            } else if name == name!("<partialmethod*>") && bound.len() >= 2 {
                let rest = v.union(recv, bound[1]);
                v.tok(Token::Builtin { name: name!("<partial*>"), bound: Arc::from(vec![bound[0], rest]), bound_kw })
            } else if name == name!("<singledispatchmethod>") {
                let inner =
                    v.tok(Token::Builtin { name: name!("<singledispatch>"), bound, bound_kw: Arc::from(Vec::new()) });
                v.tok(Token::BoundMethod { func: inner, receiver })
            } else {
                tok
            }
        }
        _ => tok,
    }
}
