//! Comparing two states by their contents (`State::same_result`).
//!
//! Two states may number their values differently, since each has its own `Values` table, so
//! every handle is first turned into a value that no table owns.

use crate::model::{ClassId, FuncId, NodeId, TokId, Token, Vals, Values};

use super::state::{Env, State, Table};

/// A token in a form independent of any `Values` table, for comparing states.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum CTok {
    /// `Closure(func, parent)`
    Closure(FuncId, Option<Box<CNode>>),
    /// `Builtin(name, bound, bound_kw)`
    Builtin(String, Vec<CVals>, Vec<(String, CVals)>),
    /// `ClassObj(cls)`
    ClassObj(ClassId),
    /// `Instance(cls, alloc)`
    Instance(ClassId, Option<crate::model::Alloc>),
    /// `BoundMethod(func, receiver)`
    Bound(Box<CTok>, Box<CTok>),
    /// `ClassMethod(func)`
    ClassMethod(Box<CTok>),
    /// `StaticMethod(func)`
    StaticMethod(Box<CTok>),
    /// `Super(start, receiver)`
    Super(ClassId, Box<CTok>),
    /// `Property(fget, fset, fdel)`
    Property(CVals, CVals, CVals),
    /// `Box(kind, alloc)`
    Box(String, crate::model::Alloc),
    /// `Module(name)`
    Module(String),
}

/// A value in table-independent form: its tokens, sorted.
type CVals = Vec<CTok>;

/// A node in table-independent form: function, context parameters, parent, merged flag.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct CNode(FuncId, Vec<(String, CVals)>, Option<Box<CNode>>, bool);

/// An environment in table-independent form.
type CEnv = Vec<(String, CVals)>;

/// Converts the handles of one state into table-independent values.
struct Canon<'v> {
    /// the table the handles belong to
    v: &'v Values,
}

impl Canon<'_> {
    /// Return token `t` in table-independent form.
    fn tok(&self, t: TokId) -> CTok {
        match self.v.get(t) {
            Token::Closure { func, parent } => CTok::Closure(*func, parent.map(|p| Box::new(self.node(p)))),
            Token::Builtin { name, bound, bound_kw } => CTok::Builtin(
                name.to_string(),
                bound.iter().map(|&b| self.vals(b)).collect(),
                bound_kw.iter().map(|&(k, b)| (k.to_string(), self.vals(b))).collect(),
            ),
            Token::ClassObj(c) => CTok::ClassObj(*c),
            Token::Instance { cls, alloc } => CTok::Instance(*cls, *alloc),
            Token::BoundMethod { func, receiver } => {
                CTok::Bound(Box::new(self.tok(*func)), Box::new(self.tok(*receiver)))
            }
            Token::ClassMethod(f) => CTok::ClassMethod(Box::new(self.tok(*f))),
            Token::StaticMethod(f) => CTok::StaticMethod(Box::new(self.tok(*f))),
            Token::Super { start, receiver } => CTok::Super(*start, Box::new(self.tok(*receiver))),
            Token::Property { fget, fset, fdel } => {
                CTok::Property(self.vals(*fget), self.vals(*fset), self.vals(*fdel))
            }
            Token::Box { kind, alloc } => CTok::Box(kind.to_string(), *alloc),
            Token::Module(n) => CTok::Module(n.to_string()),
        }
    }

    /// Return value `x` in table-independent form.
    fn vals(&self, x: Vals) -> CVals {
        let mut out: CVals = self.v.slice(x).iter().map(|&t| self.tok(t)).collect();
        out.sort();
        out
    }

    /// Return node `n` in table-independent form.
    fn node(&self, n: NodeId) -> CNode {
        let d = self.v.node_data(n);
        let c = self.v.ctx_data(d.ctx);
        CNode(
            d.func,
            c.params.iter().map(|&(k, x)| (k.to_string(), self.vals(x))).collect(),
            c.parent.map(|p| Box::new(self.node(p))),
            c.merged,
        )
    }

    /// Return environment `e` in table-independent form, sorted by name.
    fn env(&self, e: &Env) -> CEnv {
        let mut out: CEnv = e.iter().map(|(k, &x)| (k.to_string(), self.vals(x))).collect();
        out.sort();
        out
    }
}

impl State {
    /// Return True if both states hold the same results, ignoring node order and `changed`.
    ///
    /// The two states may number their values differently (each has its own table), so the
    /// tables are compared by their contents.
    pub fn same_result(&self, other: &State) -> bool {
        let (a, b) = (Canon { v: &self.values }, Canon { v: &other.values });
        /// Return a sorted list of items.
        fn sorted<T: Ord>(mut v: Vec<T>) -> Vec<T> {
            v.sort();
            v
        }
        let nodes = |c: &Canon<'_>, s: &State| sorted(s.order.iter().map(|&n| c.node(n)).collect());
        let node_envs =
            |c: &Canon<'_>, t: &Table<NodeId, Env>| sorted(t.iter().map(|(n, e)| (c.node(n), c.env(e))).collect());
        let rets = |c: &Canon<'_>, s: &State| sorted(s.ret.iter().map(|(n, &x)| (c.node(n), c.vals(x))).collect());
        let edges = |c: &Canon<'_>, s: &State| {
            sorted(
                s.edges
                    .iter()
                    .map(|e| {
                        (
                            c.node(e.caller),
                            (e.site.caller, e.site.line, e.site.col, e.site.text.to_string(), e.site.kind.as_str()),
                            c.node(e.callee),
                        )
                    })
                    .collect(),
            )
        };
        let classes = |c: &Canon<'_>, s: &State| sorted(s.class_attrs.iter().map(|(k, e)| (k, c.env(e))).collect());
        let insts = |c: &Canon<'_>, s: &State| sorted(s.inst_attrs.iter().map(|(t, e)| (c.tok(t), c.env(e))).collect());
        let heap = |c: &Canon<'_>, s: &State| sorted(s.heap.iter().map(|(t, &x)| (c.tok(t), c.vals(x))).collect());
        let externs = |c: &Canon<'_>, s: &State| sorted(s.extern_.iter().map(|(f, e)| (f, c.env(e))).collect());
        let writes = |c: &Canon<'_>, s: &State| {
            sorted(
                s.writes
                    .iter()
                    .map(|(n, w)| (c.node(n), sorted(w.iter().map(|(&f, e)| (f, c.env(e))).collect())))
                    .collect(),
            )
        };
        let metas =
            |s: &State| sorted(s.metaclasses.iter().map(|(k, m)| (k, sorted(m.iter().copied().collect()))).collect());
        let decos = |c: &Canon<'_>, s: &State| {
            sorted(
                s.unknown_decos
                    .iter()
                    .map(|(n, site)| {
                        (c.node(*n), (site.caller, site.line, site.col, site.text.to_string(), site.kind.as_str()))
                    })
                    .collect(),
            )
        };
        let bases = |s: &State| s.bases.iter().map(|(k, b)| (k, b.clone())).collect::<Vec<_>>();
        nodes(&a, self) == nodes(&b, other)
            && node_envs(&a, &self.envs) == node_envs(&b, &other.envs)
            && rets(&a, self) == rets(&b, other)
            && edges(&a, self) == edges(&b, other)
            && node_envs(&a, &self.entry) == node_envs(&b, &other.entry)
            && classes(&a, self) == classes(&b, other)
            && insts(&a, self) == insts(&b, other)
            && heap(&a, self) == heap(&b, other)
            && externs(&a, self) == externs(&b, other)
            && writes(&a, self) == writes(&b, other)
            && bases(self) == bases(other)
            && metas(self) == metas(other)
            && decos(&a, self) == decos(&b, other)
    }
}
