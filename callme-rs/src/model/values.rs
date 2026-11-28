//! Abstract values: the tokens, `Vals`, and the contexts and nodes that closures capture.
//!
//! An abstract value `Vals` is a set of tokens. Every token, value, context and node is
//! interned in a `Values` table, so each is a `u32` handle: equal handles mean equal values,
//! and comparing or copying one costs nothing. A `Vals` is a sorted, duplicate-free list of
//! token handles; `EMPTY` is handle 0, and a union of two values is interned like any other.
//!
//! A token's depth says how deeply other tokens nest inside it; the analysis drops or widens
//! tokens that nest too deeply, which keeps every value finite. As in the Python version the
//! depth is not part of a token's identity. `Closure` points to the `Node` it was created in,
//! and a node's context holds values, so all of them share one table.

use std::sync::Arc;

use indexmap::IndexSet;
use rustc_hash::{FxBuildHasher, FxHashMap};

use super::ids::{ClassId, FuncId, Site, Style, SymId};
use crate::syntax::Name;

/// A set of interned items, numbered in insertion order.
type Interner<T> = IndexSet<T, FxBuildHasher>;

/// Handle of an interned token.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct TokId(pub u32);

/// Handle of an interned abstract value: a set of tokens.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Vals(pub u32);

/// The empty value: nothing callable is known.
pub const EMPTY: Vals = Vals(0);

/// Handle of an interned context.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct CtxId(pub u32);

/// Handle of an interned node: one function under one context.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct NodeId(pub u32);

impl NodeId {
    /// Return the handle as a table index.
    pub fn index(self) -> usize {
        self.0 as usize
    }
}

impl TokId {
    /// Return the handle as a table index.
    pub fn index(self) -> usize {
        self.0 as usize
    }
}

/// Allocation site: where in function `func` the expression that creates an object is.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Alloc {
    /// the function containing the expression
    pub func: FuncId,
    /// its line
    pub line: u32,
    /// its column
    pub col: u32,
}

impl Alloc {
    /// Return the allocation site of an object created by the call at `site`.
    pub fn of(site: &Site) -> Alloc {
        Alloc { func: site.caller, line: site.line, col: site.col }
    }
}

/// An abstract object: a callable, a class, an instance, a container or a module.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Token {
    /// function `func` created while node `parent` ran (None: unknown or too deep)
    Closure {
        /// the function
        func: FuncId,
        /// the node its free variables are read from
        parent: Option<NodeId>,
    },
    /// a builtin or stdlib callable modelled in `stubs`, or a stub module
    Builtin {
        /// stub name, e.g. `map`, `functools.partial`, or `<partial>` for an internal token
        name: Name,
        /// values prepended to every call: a receiver, or a partial's function and arguments
        bound: Arc<[Vals]>,
        /// keyword arguments bound the same way
        bound_kw: Arc<[(Name, Vals)]>,
    },
    /// the class object of `cls`
    ClassObj(ClassId),
    /// the instances of `cls` created at `alloc` (None: unknown site)
    Instance {
        /// the class
        cls: ClassId,
        /// where the instances are created
        alloc: Option<Alloc>,
    },
    /// callable `func` with `receiver` (a class or instance) bound as first argument
    BoundMethod {
        /// the callable
        func: TokId,
        /// the bound receiver
        receiver: TokId,
    },
    /// `classmethod(func)`
    ClassMethod(TokId),
    /// `staticmethod(func)`
    StaticMethod(TokId),
    /// `super(start, receiver)`: attribute lookup in the receiver's MRO after `start`
    Super {
        /// the class after which lookup starts
        start: ClassId,
        /// a class or instance
        receiver: TokId,
    },
    /// `property(fget, fset, fdel)`; each accessor is the set of functions it may be
    Property {
        /// the getters
        fget: Vals,
        /// the setters
        fset: Vals,
        /// the deleters
        fdel: Vals,
    },
    /// a container created at `alloc`; its contents live in `State.heap`
    Box {
        /// the container kind: "list", "dict", "set", or a stub's own kind such as "queue"
        kind: Name,
        /// where it is created
        alloc: Alloc,
    },
    /// the module object of the program's module `name`
    Module(Name),
}

/// A context: the abstract entry state a function is analysed under.
///
/// ```text
/// params  (parameter, value) pairs for the parameters that hold tokens, sorted by name
/// parent  the called closure's parent node, or None when unknown
/// merged  True for the one widened context of a function that ran out of contexts; its
///         entry state is kept in `State.entry` instead
/// ```
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct CtxData {
    /// sorted (parameter, value) pairs
    pub params: Arc<[(Name, Vals)]>,
    /// the closure's parent node
    pub parent: Option<NodeId>,
    /// True for the widened context
    pub merged: bool,
}

/// One analysed instance of a function: the function under a context.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct NodeData {
    /// the function
    pub func: FuncId,
    /// the context
    pub ctx: CtxId,
}

/// Lookup of the identity data of functions and classes, to print tokens.
pub trait IdTable {
    /// Return the identity data of function `f`.
    fn func_sym(&self, f: FuncId) -> &SymId;
    /// Return the identity data of class `c`.
    fn class_sym(&self, c: ClassId) -> &SymId;
}

/// The table of every token, value, context and node of one analysis.
///
/// Handles index into it; a handle from one table means nothing in another one. Entries are
/// never removed, so handles stay valid as the table grows.
#[derive(Clone)]
pub struct Values {
    /// interned tokens
    tokens: Interner<Token>,
    /// depth of each token
    depth: Vec<u32>,
    /// the tokens of each value, sorted
    sets: Vec<Arc<[TokId]>>,
    /// value handle of each token list
    set_index: FxHashMap<Arc<[TokId]>, Vals>,
    /// the singleton value of each token, once made
    singles: Vec<Option<Vals>>,
    /// interned contexts
    ctxs: Interner<CtxData>,
    /// interned nodes
    nodes: Interner<NodeData>,
}

impl Default for Values {
    fn default() -> Values {
        Values::new()
    }
}

impl Values {
    /// Create a table holding only `EMPTY` and the empty context.
    pub fn new() -> Values {
        let empty: Arc<[TokId]> = Arc::from(Vec::new());
        let mut set_index = FxHashMap::default();
        set_index.insert(empty.clone(), EMPTY);
        let mut v = Values {
            tokens: Interner::default(),
            depth: Vec::new(),
            sets: vec![empty],
            set_index,
            singles: Vec::new(),
            ctxs: Interner::default(),
            nodes: Interner::default(),
        };
        v.ctx(&[], None, false);
        v
    }

    // ------------------------------------------------------------ tokens

    /// Intern `tok` and return its handle; a new token's depth is derived from what it holds.
    pub fn tok(&mut self, tok: Token) -> TokId {
        if let Some(i) = self.tokens.get_index_of(&tok) {
            return TokId(as_u32(i));
        }
        let depth = self.derived_depth(&tok);
        self.insert_token(tok, depth)
    }

    /// Intern a closure token with an explicit depth (used only when it is new).
    pub fn closure(&mut self, func: FuncId, parent: Option<NodeId>, depth: u32) -> TokId {
        let tok = Token::Closure { func, parent };
        if let Some(i) = self.tokens.get_index_of(&tok) {
            return TokId(as_u32(i));
        }
        self.insert_token(tok, depth)
    }

    /// Add a token known to be new.
    fn insert_token(&mut self, tok: Token, depth: u32) -> TokId {
        let (i, _) = self.tokens.insert_full(tok);
        self.depth.push(depth);
        self.singles.push(None);
        TokId(as_u32(i))
    }

    /// Return the depth a new token gets: one more than the deepest token it holds.
    fn derived_depth(&self, tok: &Token) -> u32 {
        let max_in = |vs: &mut dyn Iterator<Item = Vals>| -> Option<u32> {
            vs.flat_map(|v| self.sets[v.0 as usize].iter().map(|t| self.depth[t.index()])).max()
        };
        match tok {
            Token::Closure { .. }
            | Token::ClassObj(_)
            | Token::Instance { .. }
            | Token::Box { .. }
            | Token::Module(_) => 0,
            Token::Builtin { bound, bound_kw, .. } => {
                let mut it = bound.iter().copied().chain(bound_kw.iter().map(|(_, v)| *v));
                max_in(&mut it).map_or(0, |d| d + 1)
            }
            Token::Property { fget, fset, fdel } => max_in(&mut [*fget, *fset, *fdel].into_iter()).map_or(0, |d| d + 1),
            Token::BoundMethod { func, receiver } => 1 + self.depth[func.index()].max(self.depth[receiver.index()]),
            Token::ClassMethod(f) | Token::StaticMethod(f) => 1 + self.depth[f.index()],
            Token::Super { receiver, .. } => 1 + self.depth[receiver.index()],
        }
    }

    /// Return the token behind handle `t`.
    pub fn get(&self, t: TokId) -> &Token {
        &self.tokens[t.index()]
    }

    /// Return the depth of token `t`.
    pub fn depth(&self, t: TokId) -> u32 {
        self.depth[t.index()]
    }

    /// Return the number of tokens interned so far.
    pub fn token_count(&self) -> usize {
        self.tokens.len()
    }

    // ------------------------------------------------------------ values

    /// Return the value holding only token `t`.
    pub fn single(&mut self, t: TokId) -> Vals {
        if let Some(v) = self.singles[t.index()] {
            return v;
        }
        let v = self.intern_sorted(vec![t]);
        self.singles[t.index()] = Some(v);
        v
    }

    /// Intern the value holding `toks`, which may be unsorted and contain duplicates.
    pub fn of(&mut self, mut toks: Vec<TokId>) -> Vals {
        match toks.len() {
            0 => EMPTY,
            1 => self.single(toks[0]),
            _ => {
                toks.sort_unstable();
                toks.dedup();
                self.intern_sorted(toks)
            }
        }
    }

    /// Intern a sorted, duplicate-free token list.
    fn intern_sorted(&mut self, toks: Vec<TokId>) -> Vals {
        if let Some(&v) = self.set_index.get(&toks[..]) {
            return v;
        }
        let arc: Arc<[TokId]> = Arc::from(toks);
        let v = Vals(as_u32(self.sets.len()));
        self.sets.push(arc.clone());
        self.set_index.insert(arc, v);
        v
    }

    /// Return the tokens of `v`, sorted; the list is shared, so holding it is cheap.
    pub fn toks(&self, v: Vals) -> Arc<[TokId]> {
        self.sets[v.0 as usize].clone()
    }

    /// Return the tokens of `v` as a borrowed slice.
    pub fn slice(&self, v: Vals) -> &[TokId] {
        &self.sets[v.0 as usize]
    }

    /// Return the number of tokens in `v`.
    pub fn len(&self, v: Vals) -> usize {
        self.sets[v.0 as usize].len()
    }

    /// Return True if `t` is in `v`.
    pub fn contains(&self, v: Vals, t: TokId) -> bool {
        self.slice(v).binary_search(&t).is_ok()
    }

    /// Return True if every token of `a` is in `b`.
    pub fn subset(&self, a: Vals, b: Vals) -> bool {
        if a == b || a == EMPTY {
            return true;
        }
        let (xs, ys) = (self.slice(a), self.slice(b));
        if xs.len() > ys.len() {
            return false;
        }
        let mut j = 0;
        for x in xs {
            while j < ys.len() && ys[j] < *x {
                j += 1;
            }
            if j == ys.len() || ys[j] != *x {
                return false;
            }
            j += 1;
        }
        true
    }

    /// Return the union of `a` and `b`.
    pub fn union(&mut self, a: Vals, b: Vals) -> Vals {
        if a == b || b == EMPTY || self.subset(b, a) {
            return a;
        }
        if a == EMPTY || self.subset(a, b) {
            return b;
        }
        let (xs, ys) = (self.slice(a), self.slice(b));
        let mut out = Vec::with_capacity(xs.len() + ys.len());
        let (mut i, mut j) = (0, 0);
        while i < xs.len() && j < ys.len() {
            match xs[i].cmp(&ys[j]) {
                std::cmp::Ordering::Less => {
                    out.push(xs[i]);
                    i += 1;
                }
                std::cmp::Ordering::Greater => {
                    out.push(ys[j]);
                    j += 1;
                }
                std::cmp::Ordering::Equal => {
                    out.push(xs[i]);
                    i += 1;
                    j += 1;
                }
            }
        }
        out.extend_from_slice(&xs[i..]);
        out.extend_from_slice(&ys[j..]);
        self.intern_sorted(out)
    }

    /// Return the union of all `vs`.
    pub fn union_all(&mut self, vs: impl IntoIterator<Item = Vals>) -> Vals {
        let mut out = EMPTY;
        for v in vs {
            out = self.union(out, v);
        }
        out
    }

    /// Return `v` plus token `t`.
    pub fn add(&mut self, v: Vals, t: TokId) -> Vals {
        let s = self.single(t);
        self.union(v, s)
    }

    /// Return the tokens of `v` for which `keep` holds.
    pub fn filter(&mut self, v: Vals, mut keep: impl FnMut(&Values, TokId) -> bool) -> Vals {
        let toks = self.toks(v);
        let kept: Vec<TokId> = toks.iter().copied().filter(|&t| keep(self, t)).collect();
        if kept.len() == toks.len() { v } else { self.of(kept) }
    }

    /// Return the value made of `f(t)` for every token `t` of `v`.
    pub fn map(&mut self, v: Vals, mut f: impl FnMut(&mut Values, TokId) -> TokId) -> Vals {
        let toks = self.toks(v);
        let out: Vec<TokId> = toks.iter().map(|&t| f(self, t)).collect();
        self.of(out)
    }

    /// Return the number of values interned so far.
    pub fn vals_count(&self) -> usize {
        self.sets.len()
    }

    // ------------------------------------------------------------ contexts and nodes

    /// Intern the context with these sorted `params`, `parent` and `merged` flag.
    pub fn ctx(&mut self, params: &[(Name, Vals)], parent: Option<NodeId>, merged: bool) -> CtxId {
        let data = CtxData { params: Arc::from(params), parent, merged };
        let (i, _) = self.ctxs.insert_full(data);
        CtxId(as_u32(i))
    }

    /// Return the empty context `Ctx()`.
    pub fn empty_ctx(&self) -> CtxId {
        CtxId(0)
    }

    /// Return the data of context `c`.
    pub fn ctx_data(&self, c: CtxId) -> &CtxData {
        &self.ctxs[c.0 as usize]
    }

    /// Intern node `(func, ctx)`.
    pub fn node(&mut self, func: FuncId, ctx: CtxId) -> NodeId {
        let (i, _) = self.nodes.insert_full(NodeData { func, ctx });
        NodeId(as_u32(i))
    }

    /// Return the function and context of node `n`.
    pub fn node_data(&self, n: NodeId) -> NodeData {
        self.nodes[n.index()]
    }

    /// Return the function of node `n`.
    pub fn node_func(&self, n: NodeId) -> FuncId {
        self.nodes[n.index()].func
    }

    /// Return the context of node `n`.
    pub fn node_ctx(&self, n: NodeId) -> &CtxData {
        self.ctx_data(self.nodes[n.index()].ctx)
    }

    // ------------------------------------------------------------ printing

    /// Return the printable name of a token, with ids rendered in `style` (`token_label`).
    ///
    /// Closures print as their function id, classes as `<class C>`, instances as `C()@line:col`,
    /// boxes as `<list@line:col>`, modules as `<module pkg.util>` and partials as `<partial:f>`;
    /// the other tokens print as calls, e.g. `classmethod(f)` or `super(C, C()@3:0)`.
    pub fn label(&self, ids: &dyn IdTable, t: TokId, style: Style) -> String {
        match self.get(t) {
            Token::Builtin { name, bound, .. } => {
                if !bound.is_empty() && name.starts_with("<partial") {
                    let inner = self.sorted_labels_all(ids, bound[0], style).join(",");
                    format!("{}:{inner}>", &name[..name.len() - 1])
                } else {
                    name.to_string()
                }
            }
            Token::ClassObj(c) => format!("<class {}>", ids.class_sym(*c).render(style)),
            Token::Instance { cls, alloc } => {
                let at = alloc.map(|a| format!("@{}:{}", a.line, a.col)).unwrap_or_default();
                format!("{}(){at}", ids.class_sym(*cls).render(style))
            }
            Token::Box { kind, alloc } => format!("<{kind}@{}:{}>", alloc.line, alloc.col),
            Token::Module(n) => format!("<module {n}>"),
            Token::BoundMethod { func, receiver } => {
                format!("{}@{}", self.label(ids, *func, style), self.label(ids, *receiver, style))
            }
            Token::ClassMethod(f) => format!("classmethod({})", self.label(ids, *f, style)),
            Token::StaticMethod(f) => format!("staticmethod({})", self.label(ids, *f, style)),
            Token::Super { start, receiver } => {
                format!("super({}, {})", ids.class_sym(*start).render(style), self.label(ids, *receiver, style))
            }
            Token::Property { fget, .. } => {
                format!("property({})", self.sorted_labels_all(ids, *fget, style).join(","))
            }
            Token::Closure { func, .. } => ids.func_sym(*func).render(style).to_owned(),
        }
    }

    /// Return the labels of the tokens of `v`, sorted and without duplicates.
    pub fn sorted_labels(&self, ids: &dyn IdTable, v: Vals, style: Style) -> Vec<String> {
        let mut out: Vec<String> = self.slice(v).iter().map(|&t| self.label(ids, t, style)).collect();
        out.sort();
        out.dedup();
        out
    }

    /// Return the labels of the tokens of `v`, sorted, duplicates kept (Python's `sorted` over
    /// a generator rather than a set).
    pub fn sorted_labels_all(&self, ids: &dyn IdTable, v: Vals, style: Style) -> Vec<String> {
        let mut out: Vec<String> = self.slice(v).iter().map(|&t| self.label(ids, t, style)).collect();
        out.sort();
        out
    }
}

/// Convert a table length to a handle number.
fn as_u32(n: usize) -> u32 {
    u32::try_from(n).expect("fewer than 2^32 interned values")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::ids::FuncId;

    #[test]
    fn values_are_interned_sets() {
        let mut v = Values::new();
        let a = v.tok(Token::Module(Name::new("a")));
        let b = v.tok(Token::Module(Name::new("b")));
        assert_eq!(a, v.tok(Token::Module(Name::new("a"))));
        let sa = v.single(a);
        let sb = v.single(b);
        let ab = v.union(sa, sb);
        assert_eq!(ab, v.union(sb, sa));
        assert_eq!(ab, v.of(vec![b, a, b]));
        assert!(v.subset(sa, ab) && !v.subset(ab, sa));
        assert_eq!(v.union(ab, sa), ab);
        assert_eq!(v.union(EMPTY, sb), sb);
    }

    #[test]
    fn depth_counts_nesting() {
        let mut v = Values::new();
        let f = v.closure(FuncId(0), None, 0);
        let sf = v.single(f);
        let p = v.tok(Token::Builtin {
            name: Name::new("<partial>"),
            bound: Arc::from(vec![sf]),
            bound_kw: Arc::from(vec![]),
        });
        let sp = v.single(p);
        let pp = v.tok(Token::Builtin {
            name: Name::new("<partial>"),
            bound: Arc::from(vec![sp]),
            bound_kw: Arc::from(vec![]),
        });
        assert_eq!((v.depth(f), v.depth(p), v.depth(pp)), (0, 1, 2));
    }
}
