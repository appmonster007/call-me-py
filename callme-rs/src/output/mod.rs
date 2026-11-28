//! Step 3: results and files.
//!
//! `callgraph` builds the `CallGraph` result from a solved state, `icfg` the interprocedural
//! CFG, `export` writes JSON and DOT, `persist` saves and loads solver states, and `json` is
//! the JSON reader and writer they use (byte-compatible with Python's `json` module).

pub mod callgraph;
pub mod export;
pub mod icfg;
pub mod json;
pub mod persist;

pub use callgraph::{CallGraph, build_callgraph, node_label};
pub use export::{icfg_dict, to_dict, to_dot, to_icfg_dot, to_json};
pub use icfg::{ICFG, SYNTACTIC_KINDS, build_icfg};
pub use persist::{load_state, save_state, state_from_json, state_to_json};
