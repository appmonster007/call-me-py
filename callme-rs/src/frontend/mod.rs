//! Step 1: function identifiers, scopes, call sites, control flow.
//!
//! Builds a `Program`: every function (def, async def, lambda, module body) gets a `FuncId`,
//! a lexical parent, a signature, its set of local names and a CFG. `parse_files` also
//! follows imports: starting from the entry files, it loads every project module they
//! import, transitively, and resolves module names the way Python's path finder does.
//!
//! The modules: `program` (the `Program` and its records), `scopes` (locals and
//! declarations), `sites` (call sites), `cfg` (the CFG builder), `builder`
//! (`ProgramBuilder`, `build_program`) and `loader` (`parse_files`).

pub mod builder;
pub mod cfg;
pub mod loader;
pub mod program;
pub mod scopes;
pub mod sites;

pub use builder::{ProgramBuilder, absolute_module, build_program, fingerprint, module_all};
pub use cfg::build_cfg;
pub use loader::{entry_module, parse_file, parse_files};
pub use program::{
    Body, ClassInfo, DefaultRef, FuncInfo, FuncKind, MODULE_NAME, ModuleInfo, NameSet, Program, ScopeNode, Sig,
};
pub use scopes::{alias_binding, pattern_names, scope_decls, scope_locals, scope_must_writes, walk_scope};
pub use sites::{call_sites, make_site};
