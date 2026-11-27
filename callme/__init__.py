"""Public API for call-me-py: context-sensitive call graph construction for Python.

Provides the high-level entry points (`analyze_source`, `analyze_file`) that run
the full extract -> solve -> call-graph pipeline, and re-exports the core public
interface:
  CallGraph                              analysis result (nodes, edges, sites, ICFG)
  to_dict, to_json, to_dot, to_icfg_dot  JSON and Graphviz DOT exporters
  save_state, load_state                 serialize and reload solver state
  solve, CallGraphPass, Pass, Facts,
  run_passes                             fixpoint solver and multi-pass pipeline

Usage:
  Single pass:   cg = analyze_file("x.py")
  Chained pass:  cg2 = analyze_file("x.py", state=cg.state, include_unreachable=True)
  Pipeline:      facts = run_passes(prog, [CallGraphPass(), ...])  # see callme.pipeline
"""
import os

from .callgraph import CallGraph, build_callgraph
from .export import to_dict, to_dot, to_icfg_dot, to_json
from .extract import build_program, parse_file
from .persist import load_state, save_state
from .pipeline import Facts, Pass, run_passes
from .solver import CallGraphPass, solve
from .transfer import State


def analyze_source(source: str, filename: str = "<string>", id_style: str = "full", *,
                   state: State | None = None, include_unreachable: bool = False,
                   max_contexts: int | None = None, flow_sensitive: bool | None = None,
                   max_rounds: int = 10_000) -> CallGraph:
    """Parse Python `source`, run the call graph fixpoint solver, and return a `CallGraph`.

    Chains `build_program`, `solve`, and `build_callgraph`. The returned graph keeps
    its final solver state in `.state` so later passes can build on it.

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


def analyze_file(path: str | os.PathLike[str], id_style: str = "full", *,
                 state: State | None = None, include_unreachable: bool = False,
                 max_contexts: int | None = None, flow_sensitive: bool | None = None,
                 max_rounds: int = 10_000) -> CallGraph:
    """Read a UTF-8 Python file at `path`, run the call graph fixpoint solver, and return a `CallGraph`.

    Chains `parse_file`, `solve`, and `build_callgraph`. The returned graph keeps
    its final solver state in `.state` so later passes can build on it.

    path                 path to the Python file; `str(path)` is used in IDs and fingerprint
    id_style             rendered ID format: "full" (file:line:col:qualname),
                         "module" (module.qualname), or "short" (qualname)
    state                earlier `State` for the same, unchanged file (e.g. `cg.state`
                         or from `load_state`) to resume and extend (copied, not mutated)
    include_unreachable  also analyse functions never called from the module body
    max_contexts         per-function context budget before widening (default 16,
                         or `state`'s budget when resuming)
    flow_sensitive       track locals per program point instead of merging all
                         bindings (default True, or `state`'s mode when resuming)
    max_rounds           maximum fixpoint iterations before raising `RuntimeError`
    """
    prog = parse_file(path)
    state = solve(prog, state, include_unreachable=include_unreachable,
                  max_contexts=max_contexts, flow_sensitive=flow_sensitive,
                  max_rounds=max_rounds)
    return build_callgraph(prog, state, id_style)


__all__ = ["analyze_source", "analyze_file", "to_json", "to_dot", "to_dict", "to_icfg_dot",
           "CallGraph", "CallGraphPass", "Facts", "Pass", "run_passes", "solve",
           "save_state", "load_state"]
