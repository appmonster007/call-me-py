//! Step 2: abstract interpretation and the fixpoint.
//!
//! `state` holds the global `State`; `interp` interprets one function in one context;
//! `solver` runs the call graph pass to a fixpoint (round by round, as the Python version
//! does), `worklist` does the same visiting only the nodes whose inputs changed, and
//! `pipeline` runs several passes to a joint fixpoint. `deps` holds the worklist's dependency
//! keys and `compare` the structural comparison of two states.

pub mod compare;
pub mod deps;
pub mod interp;
pub mod pipeline;
pub mod solver;
pub mod state;
pub mod worklist;

pub use interp::{Interp, analyze};
pub use pipeline::{Facts, Pass, PassState, run_passes};
pub use solver::{CallGraphPass, DEFAULT_MAX_CONTEXTS, SolveOptions, Solver, init_state, solve, step};
pub use state::{Env, MAX_DEPTH, State, Writes, join_envs, mk_closure};
