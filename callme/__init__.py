"""call-me-py: context-sensitive call graph construction for Python.

Single pass:   cg = analyze_file("x.py")
Chained pass:  cg2 = analyze_file("x.py", state=cg.state, include_unreachable=True)
Pipeline:      facts = run_passes(prog, [CallGraphPass(), ...]); see callme.pipeline
"""
from .callgraph import CallGraph, build_callgraph
from .export import to_dict, to_dot, to_icfg_dot, to_json
from .extract import build_program, parse_file
from .persist import load_state, save_state
from .pipeline import Facts, Pass, run_passes
from .solver import CallGraphPass, solve


def analyze_source(source: str, filename: str = "<string>", id_style: str = "full", **opts) -> CallGraph:
    """`opts` go to `solve` (state=, include_unreachable=, max_contexts=, ...).
    The returned graph keeps its solver state in `.state` for a later pass."""
    prog = build_program(source, filename)
    return build_callgraph(prog, solve(prog, **opts), id_style)


def analyze_file(path, id_style: str = "full", **opts) -> CallGraph:
    """Like analyze_source; a `state` from an earlier run of the same, unchanged file can be resumed."""
    prog = parse_file(path)
    return build_callgraph(prog, solve(prog, **opts), id_style)


__all__ = ["analyze_source", "analyze_file", "to_json", "to_dot", "to_dict", "to_icfg_dot",
           "CallGraph", "CallGraphPass", "Facts", "Pass", "run_passes", "solve",
           "save_state", "load_state"]
