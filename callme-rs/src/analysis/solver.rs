//! Call graph pass: global fixpoint over (function, context) nodes.
//!
//! `solve` maps a State to a State, so passes can be chained:
//!
//! ```text
//!   let s = solve(&prog, None, &SolveOptions::default())?;                  // reachable code
//!   let s = solve(&prog, Some(s), &SolveOptions { include_unreachable: true, ..})?;  // + dead code
//!   solve(&prog, Some(s.copy()), &opts)?.same_result(&s)                     // already a fixpoint
//! ```
//!
//! Every pass only joins (never overwrites), so results grow monotonically and re-running a
//! pass on its own output changes nothing. To combine this analysis with others, use
//! `CallGraphPass` with `pipeline::run_passes`. `worklist::solve_worklist` reaches the same kind
//! of fixpoint visiting only the nodes whose inputs changed.

use std::any::Any;

use crate::error::{Error, Result};
use crate::frontend::{FuncKind, Program};
use crate::model::Edge;

use super::interp::analyze;
use super::pipeline::{Facts, Pass, PassState, run_passes};
use super::state::{DepKey, State};

/// Contexts per function before calls are widened, unless a state says otherwise.
pub const DEFAULT_MAX_CONTEXTS: usize = 16;

/// Which fixpoint algorithm `solve` uses.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Solver {
    /// analyse every node once per round, as the Python version does
    #[default]
    Rounds,
    /// re-analyse only nodes whose inputs changed, then confirm with one full round
    Worklist,
}

/// Options of `solve`, `init_state` and `CallGraphPass`.
///
/// ```text
/// max_contexts    context budget of a new state (default 16); for an existing state None or
///                 its own value
/// flow_sensitive  flow mode of a new state (default true); for an existing state None or its
///                 own value
/// max_rounds      maximum number of rounds before `Error::Runtime`
/// ```
#[derive(Clone, Debug)]
#[allow(missing_docs)]
pub struct SolveOptions {
    pub include_unreachable: bool,
    pub max_contexts: Option<usize>,
    pub flow_sensitive: Option<bool>,
    pub max_rounds: usize,
    pub solver: Solver,
}

impl Default for SolveOptions {
    fn default() -> SolveOptions {
        SolveOptions {
            include_unreachable: false,
            max_contexts: None,
            flow_sensitive: None,
            max_rounds: 10_000,
            solver: Solver::Rounds,
        }
    }
}

/// Return Python's spelling of a bool, for error messages.
fn py_bool(b: bool) -> &'static str {
    if b { "True" } else { "False" }
}

/// Return a state for `prog` with its root nodes registered, creating it if `state` is None.
///
/// The entry module bodies (`Program.entries`) are always roots; other modules become reachable
/// when they are imported. With `include_unreachable`, every module body and every function is
/// a root too, analysed with no known arguments, so code that is never called or imported is
/// also covered. Calling it again on its own result changes nothing.
///
/// For an existing state, `max_contexts` and `flow_sensitive` must be None or match its own
/// value; any conflict, or a state made for another program, is an `Error::Value`.
pub fn init_state(prog: &Program, state: Option<State>, opts: &SolveOptions) -> Result<State> {
    let mut state = match state {
        None => State::new(
            opts.max_contexts.unwrap_or(DEFAULT_MAX_CONTEXTS),
            Some(prog.fingerprint.clone()),
            opts.flow_sensitive.unwrap_or(true),
        ),
        Some(mut s) => {
            if s.program.as_ref().is_some_and(|p| *p != prog.fingerprint) {
                return Err(Error::Value(
                    "state was produced for a different program (or another version of it)".into(),
                ));
            }
            if let Some(m) = opts.max_contexts
                && m != s.max_ctx
            {
                return Err(Error::Value(format!("max_contexts={m} conflicts with state.max_ctx={}", s.max_ctx)));
            }
            if let Some(f) = opts.flow_sensitive
                && f != s.flow
            {
                return Err(Error::Value(format!(
                    "flow_sensitive={} conflicts with state.flow={}",
                    py_bool(f),
                    py_bool(s.flow)
                )));
            }
            s.program = Some(prog.fingerprint.clone());
            s
        }
    };
    let roots = if prog.entries.is_empty() { vec![prog.module] } else { prog.entries.clone() };
    for entry in roots {
        let n = state.node_of(entry, &[], None, false);
        state.register(n);
    }
    if opts.include_unreachable {
        // also analyse never-called functions (static-only edges)
        for info in &prog.funcs {
            if info.kind == FuncKind::Module {
                let n = state.node_of(info.id, &[], None, false);
                state.register(n);
                continue;
            }
            let in_module = info.parent.is_some_and(|p| prog.func(p).kind == FuncKind::Module);
            let parent = match info.parent {
                Some(p) if in_module => Some(state.node_of(p, &[], None, false)),
                _ => None,
            };
            let n = state.node_of(info.id, &[], parent, false);
            state.register(n);
        }
    }
    Ok(state)
}

/// Return True if `CALLME_TRACE` is set: every node visit is then logged to stderr with its
/// duration, to find what makes an analysis slow.
fn trace_enabled() -> bool {
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("CALLME_TRACE").is_some())
}

/// Analyse node `node` once and join its environment, return value and call edges into
/// `state`; return True if any of them grew.
pub fn visit(prog: &Program, state: &mut State, node: crate::model::NodeId) -> bool {
    let started = trace_enabled().then(std::time::Instant::now);
    if started.is_some() {
        let label = crate::output::node_label(&state.values, prog, node, crate::model::Style::Module);
        eprintln!("callme: visiting {label}");
    }
    let (env, ret, calls) = analyze(node, state, prog, None);
    if let Some(t) = started {
        let label = crate::output::node_label(&state.values, prog, node, crate::model::Style::Module);
        eprintln!("callme: visited {label} in {:.3}s", t.elapsed().as_secs_f64());
    }
    let mut changed = false;
    let old_env = state.envs.get(node).cloned().unwrap_or_default();
    let mut new_env = old_env.clone();
    for (k, v) in env {
        State::join_into(&mut state.values, &mut new_env, k, v);
    }
    if new_env != old_env {
        state.envs.insert(node, new_env);
        state.grew(DepKey::Env(node));
        state.grew(DepKey::FuncEnv(state.values.node_func(node)));
        changed = true;
    }
    let old_ret = state.ret.get(node).copied().unwrap_or(crate::model::EMPTY);
    if !state.values.subset(ret, old_ret) {
        let r = state.values.union(old_ret, ret);
        state.ret.insert(node, r);
        state.grew(DepKey::Ret(node));
        changed = true;
    }
    for (site, callee) in calls {
        if state.edges.insert(Edge { caller: node, site, callee }) {
            changed = true;
        }
    }
    changed
}

/// After a round that changed nothing else, confirm the decorator sites that are still
/// unresolved (`State.pending_decos`) as the identity; return True if any is new.
pub fn confirm_decorators(state: &mut State) -> bool {
    let new: Vec<_> = state.pending_decos.iter().filter(|d| !state.unknown_decos.contains(*d)).copied().collect();
    state.pending_decos.clear();
    if new.is_empty() {
        return false;
    }
    state.unknown_decos.extend(new);
    true
}

/// Analyse every known node once and join the results into `state`, in place.
///
/// Each node's environment, return value and call edges are joined into the state.
/// `state.changed` is true afterwards if anything grew, including the contexts and tables that
/// `analyze` writes directly. When a round makes no other changes, any decorator sites that
/// still evaluated to `EMPTY` are confirmed unresolved in `state.unknown_decos` (so the next
/// round treats them as the identity) and `state.changed` is set.
pub fn step(prog: &Program, state: &mut State) {
    state.changed = false;
    state.pending_decos.clear();
    let n = state.order.len();
    for i in 0..n {
        let node = state.order[i];
        if visit(prog, state, node) {
            state.changed = true;
        }
    }
    if !state.changed && confirm_decorators(state) {
        state.changed = true;
    }
    state.pending_decos.clear();
}

/// The call graph analysis as a pipeline pass, whose state is a `State`.
///
/// The options are passed to `init_state`; None for `max_contexts` or `flow_sensitive` means
/// the default for a new state, or the value of a state being resumed.
pub struct CallGraphPass {
    /// the options of `init_state`
    pub opts: SolveOptions,
    /// the key of its state in `Facts`
    pub name: String,
}

impl CallGraphPass {
    /// Create the pass with these options, named "callgraph".
    pub fn new(opts: SolveOptions) -> CallGraphPass {
        CallGraphPass { opts, name: "callgraph".into() }
    }
}

impl PassState for State {
    fn copy_state(&self) -> Box<dyn PassState> {
        Box::new(self.copy())
    }

    fn same_result(&self, other: &dyn PassState) -> bool {
        other.as_any().downcast_ref::<State>().is_some_and(|o| State::same_result(self, o))
    }

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn as_any_mut(&mut self) -> &mut dyn Any {
        self
    }

    fn type_name(&self) -> &'static str {
        "State"
    }
}

impl Pass for CallGraphPass {
    fn name(&self) -> &str {
        &self.name
    }

    fn init(&self, prog: &Program, _facts: &Facts, state: Option<Box<dyn PassState>>) -> Result<Box<dyn PassState>> {
        let state = match state {
            None => None,
            Some(s) => {
                let tn = s.type_name();
                let any: Box<dyn Any> = s;
                Some(
                    *any.downcast::<State>()
                        .map_err(|_| Error::Type(format!("{} state must be a State, not a {tn}", self.name)))?,
                )
            }
        };
        Ok(Box::new(init_state(prog, state, &self.opts)?))
    }

    fn step(&self, prog: &Program, facts: &mut Facts) -> Result<bool> {
        let state = facts.get_mut::<State>(&self.name)?;
        match self.opts.solver {
            Solver::Rounds => step(prog, state),
            Solver::Worklist => super::worklist::solve_worklist(prog, state),
        }
        Ok(state.changed)
    }
}

/// A callback run after each round of `solve`: `on_round(round, state)`.
pub type StateHook<'h> = &'h mut dyn FnMut(usize, &State);

/// Run the call graph pass to a fixpoint, starting from `state` or from an empty state.
///
/// The state is taken by value; pass `state.copy()` to keep the original. `max_contexts`
/// defaults to 16 and `flow_sensitive` to true for a new state; for a given state they default
/// to its own values, and a different value is an `Error::Value`. The fixpoint takes at most
/// `opts.max_rounds` rounds, or `Error::Runtime` is returned.
///
/// on_round  called as `on_round(round, state)` after each round, e.g. to record a trace
pub fn solve(
    prog: &Program,
    state: Option<State>,
    opts: &SolveOptions,
    on_round: Option<StateHook<'_>>,
) -> Result<State> {
    let p = CallGraphPass::new(opts.clone());
    let facts = state.map(|s| {
        let mut f = Facts::new(prog.fingerprint.clone());
        f.states.insert(p.name.clone(), Box::new(s) as Box<dyn PassState>);
        f
    });
    let mut hook = on_round.map(|f| {
        move |rnd: usize, facts: &Facts| {
            if let Ok(s) = facts.get::<State>("callgraph") {
                f(rnd, s);
            }
        }
    });
    let hook_ref: Option<super::pipeline::RoundHook<'_>> = hook.as_mut().map(|h| h as super::pipeline::RoundHook<'_>);
    let mut facts = run_passes(prog, &[&p], facts, opts.max_rounds, hook_ref)?;
    let boxed = facts.states.shift_remove(&p.name).expect("the call graph pass has a state");
    let any: Box<dyn Any> = boxed;
    Ok(*any.downcast::<State>().expect("the call graph state is a State"))
}
