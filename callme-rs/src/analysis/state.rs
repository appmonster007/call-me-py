//! The global solver state: every table the analysis fills, and the joins that grow them.
//!
//! `State` holds the value table, contexts, environments, return values, call edges, the
//! class and instance attribute tables, the heap and the writes to other scopes. `mk_closure`
//! makes the closure token for a `def` or `lambda`, widening its parent past `MAX_DEPTH`
//! nested contexts.

use std::marker::PhantomData;
use std::sync::Arc;

use indexmap::{IndexMap, IndexSet};
use rustc_hash::{FxBuildHasher, FxHashMap, FxHashSet};

pub use super::deps::{DepKey, Deps};
use crate::model::{ClassId, CtxData, EMPTY, Edge, FuncId, NodeId, Site, TokId, Token, Vals, Values};
use crate::syntax::Name;

/// Variable name -> values, in insertion order.
pub type Env = IndexMap<Name, Vals, FxBuildHasher>;

/// Owner function -> {name: values written by a node or its callees}.
pub type Writes = IndexMap<FuncId, Env, FxBuildHasher>;

/// Nesting of captured contexts before widening to "unknown parent".
pub const MAX_DEPTH: u32 = 3;

/// A handle that indexes a dense table.
pub trait Key: Copy {
    /// Return the handle's index.
    fn index(self) -> usize;
    /// Return the handle with index `i`.
    fn from_index(i: usize) -> Self;
}

/// Implement `Key` for a `u32` handle type.
macro_rules! key {
    ($t:ty) => {
        impl Key for $t {
            fn index(self) -> usize {
                self.0 as usize
            }
            fn from_index(i: usize) -> Self {
                Self(u32::try_from(i).expect("fewer than 2^32 handles"))
            }
        }
    };
}
key!(NodeId);
key!(FuncId);
key!(ClassId);
key!(TokId);

/// A map from dense handles to values, stored as a vector of slots.
#[derive(Clone, Debug)]
pub struct Table<K, V> {
    /// one slot per handle index
    slots: Vec<Option<V>>,
    /// the key type
    key: PhantomData<K>,
}

impl<K, V> Default for Table<K, V> {
    fn default() -> Table<K, V> {
        Table { slots: Vec::new(), key: PhantomData }
    }
}

impl<K: Key, V> Table<K, V> {
    /// Return the value of `k`, if any.
    pub fn get(&self, k: K) -> Option<&V> {
        self.slots.get(k.index()).and_then(Option::as_ref)
    }

    /// Return the value of `k` for writing, if any.
    pub fn get_mut(&mut self, k: K) -> Option<&mut V> {
        self.slots.get_mut(k.index()).and_then(Option::as_mut)
    }

    /// Return True if `k` has a value.
    pub fn contains(&self, k: K) -> bool {
        self.get(k).is_some()
    }

    /// Set the value of `k`.
    pub fn insert(&mut self, k: K, v: V) {
        let i = k.index();
        if i >= self.slots.len() {
            self.slots.resize_with(i + 1, || None);
        }
        self.slots[i] = Some(v);
    }

    /// Return the value of `k`, inserting `V::default()` first if it has none.
    pub fn entry(&mut self, k: K) -> &mut V
    where
        V: Default,
    {
        let i = k.index();
        if i >= self.slots.len() {
            self.slots.resize_with(i + 1, || None);
        }
        self.slots[i].get_or_insert_with(V::default)
    }

    /// Return every (key, value) pair, in handle order.
    pub fn iter(&self) -> impl Iterator<Item = (K, &V)> {
        self.slots.iter().enumerate().filter_map(|(i, v)| v.as_ref().map(|v| (K::from_index(i), v)))
    }

    /// Return True if no key has a value.
    pub fn is_empty(&self) -> bool {
        self.slots.iter().all(Option::is_none)
    }
}

/// Global analysis state: contexts, environments, return values, call edges and object tables.
///
/// One `State` holds the result of the whole fixpoint. Every table only grows, because each
/// write is a join, and `changed` records whether anything grew since it was last reset.
/// `solver::step` resets it once per round. The `Values` table that every handle in the state
/// refers to is part of it.
///
/// ```text
/// max_ctx   contexts per function before calls are widened to one shared context
/// program   fingerprint of the `Program` the state describes, checked when resuming
/// flow      True for flow-sensitive locals; False makes each local the union of its bindings
/// ```
#[derive(Clone)]
#[allow(missing_docs)]
pub struct State {
    pub max_ctx: usize,
    pub program: Option<String>,
    pub flow: bool,
    /// every token, value, context and node the state refers to
    pub values: Values,
    /// every registered node, in registration order
    pub order: Vec<NodeId>,
    /// the registered nodes of each function
    pub by_func: Table<FuncId, Vec<NodeId>>,
    /// per node, every value each local takes (its summary)
    pub envs: Table<NodeId, Env>,
    /// return value per node
    pub ret: Table<NodeId, Vals>,
    /// call edges found so far
    pub edges: FxHashSet<Edge>,
    /// entry states of widened contexts
    pub entry: Table<NodeId, Env>,
    /// class namespace per class: methods, class attributes, attributes set on the class
    pub class_attrs: Table<ClassId, Env>,
    /// attributes of each abstract object (an `Instance` token)
    pub inst_attrs: Table<TokId, Env>,
    /// what was stored into each container (a `Box` token) after its creation
    pub heap: Table<TokId, Vals>,
    /// writes from other scopes: `global`, `nonlocal`, and `module.attr = v` into a module body
    pub extern_: Table<FuncId, Env>,
    /// per-node `global` / `nonlocal` / module-attribute writes, its callees' included
    pub writes: Table<NodeId, Writes>,
    /// direct user base classes per class
    pub bases: Table<ClassId, Vec<ClassId>>,
    /// direct user metaclasses per class
    pub metaclasses: Table<ClassId, IndexSet<ClassId, FxBuildHasher>>,
    /// decorator sites confirmed unresolved after convergence, treated as the identity
    pub unknown_decos: FxHashSet<(NodeId, Site)>,
    /// transient: empty decorator sites seen in the current round
    pub pending_decos: FxHashSet<(NodeId, Site)>,
    /// memoised MROs, cleared when bases change
    mro_cache: FxHashMap<ClassId, Arc<[ClassId]>>,
    /// memoised `deref` results with the boxes they read, cleared when the heap grows
    deref_cache: FxHashMap<Vals, (Vals, Arc<[TokId]>)>,
    /// set by any write that grew something
    pub changed: bool,
    /// dependency tracking, while the worklist solver runs
    pub deps: Option<Box<Deps>>,
}

impl State {
    /// Create an empty state.
    pub fn new(max_ctx: usize, program: Option<String>, flow: bool) -> State {
        State {
            max_ctx,
            program,
            flow,
            values: Values::new(),
            order: Vec::new(),
            by_func: Table::default(),
            envs: Table::default(),
            ret: Table::default(),
            edges: FxHashSet::default(),
            entry: Table::default(),
            class_attrs: Table::default(),
            inst_attrs: Table::default(),
            heap: Table::default(),
            extern_: Table::default(),
            writes: Table::default(),
            bases: Table::default(),
            metaclasses: Table::default(),
            unknown_decos: FxHashSet::default(),
            pending_decos: FxHashSet::default(),
            mro_cache: FxHashMap::default(),
            deref_cache: FxHashMap::default(),
            changed: false,
            deps: None,
        }
    }

    /// Return an independent copy that also starts with no pending decorator sites.
    pub fn copy(&self) -> State {
        let mut s = self.clone();
        s.pending_decos.clear();
        s
    }

    // ------------------------------------------------------------ dependencies

    /// Record that the node being analysed reads `key` (only while the worklist solver runs).
    #[inline]
    pub fn read(&mut self, key: DepKey) {
        if let Some(d) = self.deps.as_deref_mut()
            && let Some(cur) = d.current
        {
            d.readers.entry(key).or_default().insert(cur);
        }
    }

    /// Record that `key` grew: set `changed`, and tell the worklist solver.
    #[inline]
    pub fn grew(&mut self, key: DepKey) {
        self.changed = true;
        if let Some(d) = self.deps.as_deref_mut() {
            d.dirty.push(key);
        }
    }

    // ------------------------------------------------------------ joins

    /// Join `vals` into `env[name]`; return True if it grew.
    pub fn join_into(values: &mut Values, env: &mut Env, name: Name, vals: Vals) -> bool {
        match env.get(&name).copied() {
            None => {
                env.insert(name, vals);
                true
            }
            Some(old) => {
                if values.subset(vals, old) {
                    false
                } else {
                    let new = values.union(old, vals);
                    env.insert(name, new);
                    true
                }
            }
        }
    }

    /// Join `vals` into attribute `name` of class `cls`, setting `changed` if it grew.
    pub fn add_class_attr(&mut self, cls: ClassId, name: Name, vals: Vals) {
        let env = self.class_attrs.entry(cls);
        if State::join_into(&mut self.values, env, name, vals) {
            self.grew(DepKey::Class(cls));
        }
    }

    /// Join `vals` into attribute `name` of instance token `inst`, setting `changed` if it grew.
    pub fn add_inst_attr(&mut self, inst: TokId, name: Name, vals: Vals) {
        let env = self.inst_attrs.entry(inst);
        if State::join_into(&mut self.values, env, name, vals) {
            self.grew(DepKey::Inst(inst));
        }
    }

    /// Join `vals` into what is stored in container token `b`, setting `changed` if it grew.
    pub fn add_heap(&mut self, b: TokId, vals: Vals) {
        let old = self.heap.get(b).copied().unwrap_or(EMPTY);
        if !self.values.subset(vals, old) {
            let new = self.values.union(old, vals);
            self.heap.insert(b, new);
            self.deref_cache.clear();
            self.grew(DepKey::Heap(b));
        }
    }

    /// Join `vals` into the writes to variable `name` of `func` made from other scopes.
    ///
    /// These are `global` and `nonlocal` writes, and attribute writes to a module object,
    /// which `func`, a module body, then holds as a global.
    pub fn add_extern(&mut self, func: FuncId, name: Name, vals: Vals) {
        let env = self.extern_.entry(func); // created even when nothing grows, as in Python
        let old = env.get(&name).copied().unwrap_or(EMPTY);
        if !self.values.subset(vals, old) {
            let new = self.values.union(old, vals);
            env.insert(name, new);
            self.grew(DepKey::Extern(func));
        }
    }

    /// Join `vals` into the side-effect writes of `node` to `(func, name)`.
    ///
    /// Records `global`, `nonlocal` and module-attribute writes performed directly by `node`
    /// or transitively by its callees, so callers in flow mode can apply them at call sites.
    pub fn add_write(&mut self, node: NodeId, func: FuncId, name: Name, vals: Vals) {
        if vals == EMPTY {
            return;
        }
        let old = self.writes.get(node).and_then(|w| w.get(&func)).and_then(|e| e.get(&name)).copied().unwrap_or(EMPTY);
        if !self.values.subset(vals, old) {
            let new = self.values.union(old, vals);
            self.writes.entry(node).entry(func).or_default().insert(name, new);
            self.grew(DepKey::Writes(node));
        }
    }

    /// Return `vals` plus everything stored into the containers it holds, transitively.
    ///
    /// Containers found among the stored values are read too, so a list of lists yields the
    /// elements of both. Results are memoised until the heap next grows.
    pub fn deref(&mut self, vals: Vals) -> Vals {
        if vals == EMPTY || (self.heap.is_empty() && self.deps.is_none()) {
            return vals;
        }
        if let Some((out, read)) = self.deref_cache.get(&vals).cloned() {
            for &b in read.iter() {
                self.read(DepKey::Heap(b));
            }
            return out;
        }
        let mut todo: Vec<TokId> = self
            .values
            .slice(vals)
            .iter()
            .copied()
            .filter(|&t| matches!(self.values.get(t), Token::Box { .. }))
            .collect();
        let mut seen: Vec<TokId> = Vec::new();
        let mut out = vals;
        while let Some(b) = todo.pop() {
            if seen.contains(&b) {
                continue;
            }
            seen.push(b);
            let Some(&stored) = self.heap.get(b) else {
                continue;
            };
            for &t in self.values.slice(stored) {
                if matches!(self.values.get(t), Token::Box { .. }) && !seen.contains(&t) {
                    todo.push(t);
                }
            }
            out = self.values.union(out, stored);
        }
        for &b in &seen {
            self.read(DepKey::Heap(b));
        }
        self.deref_cache.insert(vals, (out, Arc::from(seen)));
        out
    }

    /// Append new direct base classes to those of `cls`, in order, and clear the MRO cache.
    pub fn add_bases(&mut self, cls: ClassId, new_bases: &[ClassId]) {
        let old = self.bases.get(cls).cloned().unwrap_or_default();
        let mut merged = old.clone();
        for &b in new_bases {
            if b != cls && !merged.contains(&b) {
                merged.push(b);
            }
        }
        if merged != old {
            self.bases.insert(cls, merged);
            self.mro_cache.clear();
            self.grew(DepKey::Hierarchy);
        }
    }

    /// Record `meta` as a metaclass of `cls`; a class is never its own metaclass.
    pub fn add_metaclass(&mut self, cls: ClassId, meta: ClassId) {
        if meta == cls {
            return;
        }
        if self.metaclasses.entry(cls).insert(meta) {
            self.grew(DepKey::Hierarchy);
        }
    }

    /// Return the metaclasses of `cls`: those of the first class in its MRO that has any.
    pub fn get_metaclasses(&mut self, cls: ClassId) -> Vec<ClassId> {
        for &c in self.mro(cls).iter() {
            if let Some(ms) = self.metaclasses.get(c)
                && !ms.is_empty()
            {
                return ms.iter().copied().collect();
            }
        }
        Vec::new()
    }

    /// Return the C3 method resolution order of `cls`, starting with `cls` (cached).
    pub fn mro(&mut self, cls: ClassId) -> Arc<[ClassId]> {
        self.read(DepKey::Hierarchy);
        self.cache_mro(cls);
        self.mro_cache[&cls].clone()
    }

    /// Compute and cache the MRO of `cls` if it is not cached yet.
    fn cache_mro(&mut self, cls: ClassId) {
        if !self.mro_cache.contains_key(&cls) {
            let res: Arc<[ClassId]> = Arc::from(self.compute_mro(cls, &mut Vec::new()));
            self.mro_cache.insert(cls, res);
        }
    }

    /// Compute the C3 linearisation of `cls` from the base classes known so far.
    ///
    /// `visiting` holds the classes on the current path, so a cyclic hierarchy ends instead of
    /// recursing forever. When C3 finds no consistent order, it takes the next class depth first
    /// instead of failing.
    fn compute_mro(&self, cls: ClassId, visiting: &mut Vec<ClassId>) -> Vec<ClassId> {
        if visiting.contains(&cls) {
            return Vec::new();
        }
        let bases: Vec<ClassId> = self
            .bases
            .get(cls)
            .map(|b| b.iter().copied().filter(|b| !visiting.contains(b)).collect())
            .unwrap_or_default();
        if bases.is_empty() {
            return vec![cls];
        }
        visiting.push(cls);
        let mut seqs: Vec<Vec<ClassId>> = bases.iter().map(|&b| self.compute_mro(b, visiting)).collect();
        visiting.pop();
        seqs.push(bases);
        let mut out = vec![cls];
        loop {
            seqs.retain(|s| !s.is_empty());
            if seqs.is_empty() {
                return out;
            }
            let cand = seqs
                .iter()
                .map(|s| s[0])
                .find(|h| !seqs.iter().any(|other| other[1..].contains(h)))
                .unwrap_or(seqs[0][0]); // inconsistent hierarchy: safe DFS fallback
            if !out.contains(&cand) {
                out.push(cand);
            }
            for s in &mut seqs {
                if s.first() == Some(&cand) {
                    s.remove(0);
                }
            }
        }
    }

    /// Return attribute `name` from the first class in `cls`'s MRO that defines it, or `EMPTY`.
    ///
    /// ```text
    /// after  start the search after this class, as `super()` does; returns `EMPTY` when it is
    ///        not in the MRO
    /// ```
    pub fn lookup_class_attr(&mut self, cls: ClassId, name: Name, after: Option<ClassId>) -> Vals {
        self.read(DepKey::Hierarchy);
        self.cache_mro(cls);
        let order = &self.mro_cache[&cls];
        let start = match after {
            None => 0,
            Some(a) => match order.iter().position(|&c| c == a) {
                Some(i) => i + 1,
                None => return EMPTY,
            },
        };
        let mut found = EMPTY;
        let mut end = order.len();
        for (i, &c) in order.iter().enumerate().skip(start) {
            if let Some(v) = self.class_attrs.get(c).and_then(|attrs| attrs.get(&name)) {
                found = *v;
                end = i + 1;
                break;
            }
        }
        if self.deps.is_some() {
            // the result depends on every class looked at
            let looked: Vec<ClassId> = order[start..end].to_vec();
            for c in looked {
                self.read(DepKey::Class(c));
            }
        }
        found
    }

    /// Return True if any class in `cls`'s MRO defines attribute `name`.
    pub fn has_class_attr(&mut self, cls: ClassId, name: Name) -> bool {
        self.read(DepKey::Hierarchy);
        self.cache_mro(cls);
        let order = &self.mro_cache[&cls];
        let pos = order.iter().position(|&c| self.class_attrs.get(c).is_some_and(|a| a.contains_key(&name)));
        if self.deps.is_some() {
            let looked: Vec<ClassId> = order[..pos.map_or(order.len(), |i| i + 1)].to_vec();
            for c in looked {
                self.read(DepKey::Class(c));
            }
        }
        pos.is_some()
    }

    // ------------------------------------------------------------ nodes

    /// Return the node `(func, ctx)` for these parts, interned.
    pub fn node_of(&mut self, func: FuncId, params: &[(Name, Vals)], parent: Option<NodeId>, merged: bool) -> NodeId {
        let ctx = self.values.ctx(params, parent, merged);
        self.values.node(func, ctx)
    }

    /// Add `node` to the nodes to analyse, if it is new, and set `changed`.
    pub fn register(&mut self, node: NodeId) {
        let func = self.values.node_func(node);
        let group = self.by_func.entry(func);
        if !group.contains(&node) {
            group.push(node);
            self.order.push(node);
            self.grew(DepKey::Contexts(func));
            if let Some(d) = self.deps.as_deref_mut() {
                d.registered.push(node);
            }
        }
    }

    /// Return the node entered by calling `func` with entry state `params` and closure `parent`.
    ///
    /// A new context is registered while `func` has fewer than `max_ctx` of them. After that,
    /// every new context is widened: the call goes to the single `func[*]` node, whose context is
    /// merged, and `params` are joined into its entry state in `entry`.
    pub fn resolve(&mut self, func: FuncId, params: &[(Name, Vals)], parent: Option<NodeId>) -> NodeId {
        let node = self.node_of(func, params, parent, false);
        let group_len = match self.by_func.get(func) {
            Some(g) if g.contains(&node) => return node,
            Some(g) => g.len(),
            None => 0,
        };
        if group_len < self.max_ctx {
            self.register(node);
            return node;
        }
        let merged = self.node_of(func, &[], None, true); // too many contexts: widen
        self.register(merged);
        let mut new = self.entry.get(merged).cloned().unwrap_or_default();
        let mut grew = false;
        for &(name, toks) in params {
            grew |= State::join_into(&mut self.values, &mut new, name, toks);
        }
        if grew {
            self.entry.insert(merged, new);
            self.grew(DepKey::Entry(merged));
        }
        merged
    }

    /// Return the union of local `name`'s summary over every context of `func`.
    pub fn union_env(&mut self, func: FuncId, name: Name) -> Vals {
        self.read(DepKey::Contexts(func));
        self.read(DepKey::FuncEnv(func));
        let Some(group) = self.by_func.get(func) else {
            return EMPTY;
        };
        let vs: Vec<Vals> =
            group.iter().filter_map(|n| self.envs.get(*n).and_then(|e| e.get(&name)).copied()).collect();
        self.values.union_all(vs)
    }

    /// Return the context data of node `n`.
    pub fn ctx_of(&self, n: NodeId) -> &CtxData {
        self.values.node_ctx(n)
    }
}

/// Return the pointwise union of environments, the join where control-flow paths meet.
pub fn join_envs<'e>(values: &mut Values, envs: impl IntoIterator<Item = &'e Env>) -> Env {
    let mut out = Env::default();
    for env in envs {
        for (&k, &v) in env {
            State::join_into(values, &mut out, k, v);
        }
    }
    out
}

/// Return the closure token for `func` defined while `node` was running.
///
/// The parent is dropped (set to None) when `node` is None, or when the closure would nest
/// contexts deeper than `MAX_DEPTH`; its free variables are then read from every context of
/// the enclosing function.
pub fn mk_closure(values: &mut Values, func: FuncId, node: Option<NodeId>) -> TokId {
    let Some(node) = node else {
        return values.closure(func, None, 0);
    };
    let params = values.node_ctx(node).params.clone();
    let depth =
        1 + params.iter().flat_map(|&(_, ts)| values.slice(ts).iter().map(|&t| values.depth(t))).max().unwrap_or(0);
    if depth > MAX_DEPTH {
        return values.closure(func, None, 0);
    }
    values.closure(func, Some(node), depth)
}
