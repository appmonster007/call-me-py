"""Public API for call-me-py: context-sensitive call graph construction for Python.

Provides the high-level entry points (`analyze_source`, `analyze_file`,
`analyze_files`) that run the full extract -> solve -> call-graph pipeline, and
re-exports the core public interface:
  CallGraph                              analysis result (nodes, edges, sites, ICFG)
  to_dict, to_json, to_dot, to_icfg_dot  JSON and Graphviz DOT exporters
  save_state, load_state                 serialize and reload solver state
  solve, CallGraphPass, Pass, Facts,
  run_passes                             fixpoint solver and multi-pass pipeline

Usage:
  Single pass:   cg = analyze_file("x.py")            # follows x.py's project imports
  Several roots: cg = analyze_files(["a.py", "b.py"], search_path=["src"])
  Chained pass:  cg2 = analyze_file("x.py", state=cg.state, include_unreachable=True)
  Pipeline:      facts = run_passes(prog, [CallGraphPass(), ...])  # see callme.pipeline
"""
import os
from collections.abc import Iterable

from .callgraph import CallGraph, build_callgraph
from .export import to_dict, to_dot, to_icfg_dot, to_json
from .extract import build_program, parse_files
from .persist import load_state, save_state
from .pipeline import Facts, Pass, run_passes
from .solver import CallGraphPass, solve
from .transfer import State


def analyze_source(source: str, filename: str = "<string>", id_style: str = "full", *,
                   state: State | None = None, include_unreachable: bool = False,
                   max_contexts: int | None = None, flow_sensitive: bool | None = None,
                   max_rounds: int = 10_000) -> CallGraph:
    """Parse Python `source`, run the call graph fixpoint solver, and return a `CallGraph`.

    Chains `build_program`, `solve`, and `build_callgraph`. The source is a single
    module named after `filename`; its imports of other files are not followed (use
    `analyze_files` for that). The returned graph keeps its final solver state in
    `.state` so later passes can build on it.

    filename             label recorded in function/class IDs and program fingerprint
    id_style             rendered ID format: "full" (file:line:col:qualname),
                         "module" (module.qualname), or "short" (qualname)
    state                earlier `State` for the same `(filename, source)` to resume
                         and extend (copied, never mutated in place)
    include_unreachable  also analyse functions never called from the module body
    max_contexts         per-function context budget before widening (default 16,
                         or `state`'s budget when resuming)
    flow_sensitive       track locals per program point instead of merging all
                         bindings (default True, or `state`'s mode when resuming)
    max_rounds           maximum fixpoint iterations before raising `RuntimeError`
    """
    prog = build_program(source, filename)
    state = solve(prog, state, include_unreachable=include_unreachable,
                  max_contexts=max_contexts, flow_sensitive=flow_sensitive,
                  max_rounds=max_rounds)
    return build_callgraph(prog, state, id_style)


def analyze_files(paths: Iterable[str | os.PathLike[str]], id_style: str = "full", *,
                  search_path: Iterable[str | os.PathLike[str]] = (),
                  state: State | None = None, include_unreachable: bool = False,
                  max_contexts: int | None = None, flow_sensitive: bool | None = None,
                  max_rounds: int = 10_000) -> CallGraph:
    """Analyse the entry files at `paths` and the project modules they import; return a `CallGraph`.

    Chains `parse_files` (which reads the files as UTF-8), `solve`, and
    `build_callgraph`. Imports are followed transitively: a module is looked up in
    each entry's root directory (as Python uses `sys.path[0]`), then in
    `search_path`; imports that name no file there, such as the standard library,
    stay external. Every entry's module body is a root of the analysis. The
    returned graph keeps its final solver state in `.state` so later passes can
    build on it.

    paths                entry files; `str(path)` is used in IDs and fingerprint
    id_style             rendered ID format: "full" (file:line:col:qualname),
                         "module" (module.qualname, the module named relative to
                         its import root), or "short" (qualname)
    search_path          more directories to search for imported modules
    state                earlier `State` for the same, unchanged files (e.g. `cg.state`
                         or from `load_state`) to resume and extend (copied, not mutated)
    include_unreachable  also analyse functions never called and modules never imported
    max_contexts         per-function context budget before widening (default 16,
                         or `state`'s budget when resuming)
    flow_sensitive       track locals per program point instead of merging all
                         bindings (default True, or `state`'s mode when resuming)
    max_rounds           maximum fixpoint iterations before raising `RuntimeError`
    """
    prog = parse_files(paths, search_path=search_path)
    state = solve(prog, state, include_unreachable=include_unreachable,
                  max_contexts=max_contexts, flow_sensitive=flow_sensitive,
                  max_rounds=max_rounds)
    return build_callgraph(prog, state, id_style)


def analyze_file(path: str | os.PathLike[str], id_style: str = "full", *,
                 search_path: Iterable[str | os.PathLike[str]] = (),
                 state: State | None = None, include_unreachable: bool = False,
                 max_contexts: int | None = None, flow_sensitive: bool | None = None,
                 max_rounds: int = 10_000) -> CallGraph:
    """Analyse the file at `path` and the project modules it imports, and return a `CallGraph`.

    Same as `analyze_files([path], ...)`, which describes every parameter. The
    returned graph keeps its final solver state in `.state` so later passes can
    build on it.
    """
    return analyze_files([path], id_style, search_path=search_path, state=state,
                         include_unreachable=include_unreachable, max_contexts=max_contexts,
                         flow_sensitive=flow_sensitive, max_rounds=max_rounds)


__all__ = ["analyze_source", "analyze_file", "analyze_files",
           "to_json", "to_dot", "to_dict", "to_icfg_dot",
           "CallGraph", "CallGraphPass", "Facts", "Pass", "run_passes", "solve",
           "save_state", "load_state"]
