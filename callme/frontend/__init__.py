"""Step 1: function identifiers, scopes, call sites, control flow.

Builds a `Program`: every function (def, async def, lambda, module body) gets a
`FuncId`, a lexical parent, a signature, its set of local names and a CFG.
`parse_files` also follows imports: starting from the entry files, it loads every
project module they import, transitively, and resolves module names the way
Python's path finder does.

The modules: `program` (the `Program` and its records), `scopes` (locals and declarations),
`sites` (call sites), `cfg` (the CFG builder), `builder` (`ProgramBuilder`, `build_program`)
and `loader` (`parse_files`). Import the names from this package.
"""
from __future__ import annotations

from .builder import ProgramBuilder, absolute_module, build_program, fingerprint, module_all
from .cfg import build_cfg
from .loader import entry_module, parse_file, parse_files
from .program import MODULE_NAME, ClassInfo, FuncInfo, ModuleInfo, Program, Sig
from .scopes import pattern_names, scope_decls, scope_locals, scope_must_writes, walk_scope
from .sites import call_sites, make_site

__all__ = [
    "ProgramBuilder", "absolute_module", "build_program", "fingerprint", "module_all",
    "build_cfg",
    "entry_module", "parse_file", "parse_files",
    "MODULE_NAME", "ClassInfo", "FuncInfo", "ModuleInfo", "Program", "Sig",
    "pattern_names", "scope_decls", "scope_locals", "scope_must_writes", "walk_scope",
    "call_sites", "make_site",
]
