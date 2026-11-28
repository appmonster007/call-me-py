//! Abstract interpretation of one function body in one context.
//!
//! An abstract value is a set of tokens ("which functions may this expression be?"):
//! `Closure`s for user code, `Builtin`s for callables modelled in `stubs`, classes and
//! instances. Containers are collapsed to the union of their elements plus a `Box` token (their
//! allocation site); what is stored into a container after it is created lives in
//! `State.heap[box]` and is read back with `deref`. Calls are context sensitive, with the
//! callee's abstract entry state as context:
//!
//! ```text
//!   entering   {param_i -> eval(arg_i)} becomes the callee's context
//!   returning  the call evaluates to the callee's return value for that context
//! ```
//!
//! Flow sensitivity (`State.flow`, the default): a body is interpreted once, in program order.
//! Assigning a local replaces its value (strong update); branches run on copies of the env and
//! are joined where they meet again; a loop repeats its body until the env at its head stops
//! growing; `break` / `continue` / `return` / `raise` end their path. Everything other scopes
//! read from a node (closures, default arguments) is its *summary*: the union of every value
//! each local takes anywhere in the body, stored in `State.envs`. Writes to enclosing scopes
//! (`global` / `nonlocal`) are summarised per node in `State.writes` and applied back at call
//! sites, with a strong update when every callee statically must-writes the variable.
//!
//! With `flow` off, a local is the union of all its bindings (weak updates) and the body is
//! re-run until that union stops changing.
//!
//! `Interp` is one struct; the Python version's layers are its `impl` blocks, one per module:
//! `base`, `names`, `modules`, `calls`, `attributes`, `expressions`, `operators`,
//! `statements`, `definitions` and `flow`.

pub mod attributes;
pub mod base;
pub mod calls;
pub mod definitions;
pub mod expressions;
pub mod flow;
pub mod modules;
pub mod names;
pub mod operators;
pub mod statements;

use std::sync::Arc;

use crate::frontend::{FuncInfo, Program};
use crate::model::{ClassId, EMPTY, NodeId, Site, TokId, Vals, Values};
use crate::stubs::{Host, HostState, Kws};
use crate::syntax::Name;

pub use base::{Calls, End, Interp, LoopFrame, bind_attr};

use super::state::{DepKey, Env, State, join_envs};

/// Analyse one node to its local fixpoint, and return (environment, return value, call edges).
///
/// The call edges are (site, callee node) pairs. In flow mode (`state.flow`) the body runs once
/// in program order from the entry state. The returned environment is the node's summary:
/// every value each local takes, joined with `env`. Otherwise the body re-runs from the entry
/// state joined with `env` until the environment stops growing. Either way, the returned
/// environment can be passed back in: with the same `state`, analysing again from the result
/// returns the same result.
///
/// Side effects: new callee contexts are registered in `state` (`State::resolve`), and widened
/// contexts may grow their entry state. The class and instance attribute tables, the heap and
/// `extern_` (writes through `global` and `nonlocal`) may grow too. The environment, return
/// value and edges are not written back; `solver::step` joins them.
///
/// env  environment to start from and join; defaults to the one stored for `node` in `state`
pub fn analyze(node: NodeId, state: &mut State, prog: &Program, env: Option<Env>) -> (Env, Vals, Calls) {
    let ctx = state.ctx_of(node).clone();
    let func = state.values.node_func(node);
    state.read(DepKey::Entry(node));
    state.read(DepKey::Env(node));
    state.read(DepKey::Extern(func));
    let start: Env = if ctx.merged {
        state.entry.get(node).cloned().unwrap_or_default()
    } else {
        ctx.params.iter().copied().collect()
    };
    let known = match env {
        Some(e) => e,
        None => state.envs.get(node).cloned().unwrap_or_default(),
    };
    let ext = state.extern_.get(func).cloned().unwrap_or_default();
    if state.flow {
        // callee writes are applied at call sites; `known` only joins the summary
        let summary = join_envs(&mut state.values, [&start, &ext, &known]);
        let mut it = Interp::new(prog, state, Some(node), false, None);
        it.env = start;
        it.summary = summary;
        it.run();
        let pending: Vec<Site> = it.empty_decos.iter().copied().collect();
        let (summary, ret, calls) = (std::mem::take(&mut it.summary), it.ret, std::mem::take(&mut it.calls));
        for site in pending {
            if !state.unknown_decos.contains(&(node, site)) {
                state.pending_decos.insert((node, site));
            }
        }
        return (summary, ret, calls);
    }
    let mut it = Interp::new(prog, state, Some(node), false, None);
    it.env = start;
    for (&name, &vals) in ext.iter().chain(known.iter()) {
        // global / nonlocal writes, then what is already known
        State::join_into(&mut it.state.values, &mut it.env, name, vals);
    }
    loop {
        let snapshot = it.env.clone();
        it.ret = EMPTY;
        it.calls.clear();
        it.empty_decos.clear();
        it.run();
        if it.env == snapshot {
            let pending: Vec<Site> = it.empty_decos.iter().copied().collect();
            let (env, ret, calls) = (std::mem::take(&mut it.env), it.ret, std::mem::take(&mut it.calls));
            for site in pending {
                if !state.unknown_decos.contains(&(node, site)) {
                    state.pending_decos.insert((node, site));
                }
            }
            return (env, ret, calls);
        }
    }
}

impl Host for Interp<'_> {
    fn info(&self) -> &FuncInfo {
        self.info
    }

    fn readonly(&self) -> bool {
        self.readonly
    }

    fn state(&mut self) -> &mut dyn HostState {
        self.state
    }

    fn values(&mut self) -> &mut Values {
        &mut self.state.values
    }

    fn lookup(&mut self, name: Name) -> Vals {
        Interp::lookup(self, name)
    }

    fn apply(
        &mut self,
        callee: Vals,
        pos: &[Vals],
        star: Option<Vals>,
        kws: &Kws,
        kwstar: Option<Vals>,
        site: Site,
    ) -> Vals {
        Interp::apply(self, callee, pos, star, kws, kwstar, site)
    }

    fn instantiate(
        &mut self,
        cls: TokId,
        pos: &[Vals],
        star: Option<Vals>,
        kws: &Kws,
        kwstar: Option<Vals>,
        site: Site,
    ) -> Vals {
        Interp::instantiate(self, cls, pos, star, kws, kwstar, site)
    }

    fn invoke_dunder(&mut self, recv: Vals, name: Name, pos: &[Vals], site: Site) -> (bool, Vals) {
        Interp::invoke_dunder(self, recv, name, pos, site)
    }

    fn invoke_data_descriptor_set(&mut self, obj: TokId, cls_vals: Vals, vals: Vals, site: Site) -> bool {
        Interp::invoke_data_descriptor_set(self, obj, cls_vals, vals, site)
    }

    fn iterate(&mut self, iterable: Vals, site: Site, is_async: bool) -> Vals {
        Interp::iterate(self, iterable, site, is_async)
    }

    fn import_any(&mut self) -> Vals {
        Interp::import_any(self)
    }
}

impl HostState for State {
    fn values(&mut self) -> &mut Values {
        &mut self.values
    }

    fn class_attr_items(&mut self, cls: ClassId) -> Vec<(Name, Vals)> {
        self.read(DepKey::Class(cls));
        self.class_attrs.get(cls).map(|e| e.iter().map(|(&n, &v)| (n, v)).collect()).unwrap_or_default()
    }

    fn has_own_class_attr(&mut self, cls: ClassId, name: Name) -> bool {
        self.read(DepKey::Class(cls));
        self.class_attrs.get(cls).is_some_and(|e| e.contains_key(&name))
    }

    fn mro(&mut self, cls: ClassId) -> Arc<[ClassId]> {
        State::mro(self, cls)
    }

    fn add_class_attr(&mut self, cls: ClassId, name: Name, vals: Vals) {
        State::add_class_attr(self, cls, name, vals);
    }

    fn add_inst_attr(&mut self, inst: TokId, name: Name, vals: Vals) {
        State::add_inst_attr(self, inst, name, vals);
    }

    fn add_heap(&mut self, b: TokId, vals: Vals) {
        State::add_heap(self, b, vals);
    }

    fn lookup_class_attr(&mut self, cls: ClassId, name: Name, after: Option<ClassId>) -> Vals {
        State::lookup_class_attr(self, cls, name, after)
    }
}
