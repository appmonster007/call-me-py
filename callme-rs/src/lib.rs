//! callme: context-sensitive call graphs for Python programs, by abstract interpretation.
//!
//! This crate is the Rust port of `callme/`, the Python package in the same repository. It
//! reads the entry files and every project module they import, interprets each function in
//! each calling context to find which callables every call may reach, and writes the call
//! graph as JSON or Graphviz DOT. See `README.md` for the module map and the design.
//!
//! The packages follow the pipeline, and each one only uses the packages before it:
//! `syntax` (CPython-shaped syntax trees) ← `model` (ids, values, graphs) ← `frontend`
//! (source → `Program`) ← `stubs` (builtin and stdlib models) ← `analysis` (the interpreter
//! and the fixpoint) ← `output` (call graph, JSON, DOT, saved states).

pub mod analysis;
pub mod error;
pub mod frontend;
pub mod model;
pub mod output;
pub mod stubs;
pub mod syntax;

pub use analysis::{CallGraphPass, Facts, Pass, PassState, SolveOptions, Solver, State, run_passes, solve};
pub use error::{Error, Result};
pub use model::Style;
pub use output::{CallGraph, load_state, save_state, to_dict, to_dot, to_icfg_dot, to_json};

/// Options of `analyze_source`, `analyze_file` and `analyze_files`.
///
/// ```text
/// id_style             rendered ID format: full (file:line:col:qualname), module
///                      (module.qualname) or short (qualname)
/// search_path          more directories to search for imported modules (files only)
/// state                earlier `State` for the same, unchanged sources to resume and extend
/// solve                the solver options: `include_unreachable`, the context budget, the
///                      flow mode, the round limit and the algorithm
/// ```
#[derive(Default)]
#[allow(missing_docs)]
pub struct Options {
    pub id_style: Style,
    pub search_path: Vec<String>,
    pub state: Option<State>,
    pub solve: SolveOptions,
}

/// Parse Python `source`, run the call graph fixpoint solver, and return a `CallGraph`.
///
/// Chains `build_program`, `solve` and `build_callgraph`. The source is a single module named
/// after `filename`; its imports of other files are not followed (use `analyze_files` for
/// that). The returned graph keeps its final solver state in `.state` so later passes can
/// build on it.
///
/// filename  label recorded in function and class ids and in the program fingerprint
pub fn analyze_source(source: &str, filename: &str, opts: Options) -> Result<CallGraph> {
    let prog = frontend::build_program(source, filename)?;
    let state = solve(&prog, opts.state, &opts.solve, None)?;
    Ok(output::build_callgraph(prog, state, opts.id_style))
}

/// Analyse the entry files at `paths` and the project modules they import; return a `CallGraph`.
///
/// Chains `parse_files` (which reads the files as UTF-8), `solve` and `build_callgraph`.
/// Imports are followed transitively: a module is looked up in each entry's root directory (as
/// Python uses `sys.path[0]`), then in `opts.search_path`; imports that name no file there,
/// such as the standard library, stay external. Every entry's module body is a root of the
/// analysis. The returned graph keeps its final solver state in `.state`.
pub fn analyze_files(paths: &[String], opts: Options) -> Result<CallGraph> {
    let prog = frontend::parse_files(paths, &opts.search_path)?;
    let state = solve(&prog, opts.state, &opts.solve, None)?;
    Ok(output::build_callgraph(prog, state, opts.id_style))
}

/// Analyse the file at `path` and the project modules it imports; see `analyze_files`.
pub fn analyze_file(path: &str, opts: Options) -> Result<CallGraph> {
    analyze_files(&[path.to_owned()], opts)
}
