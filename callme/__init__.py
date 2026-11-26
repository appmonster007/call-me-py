"""call-me-py: context-sensitive call graph construction for Python."""
from .callgraph import CallGraph, build_callgraph
from .export import to_dict, to_dot, to_icfg_dot, to_json
from .extract import build_program, parse_file
from .solver import solve


def analyze_source(source: str, filename: str = "<string>", id_style: str = "full", **opts) -> CallGraph:
    prog = build_program(source, filename)
    return build_callgraph(prog, solve(prog, **opts), id_style)


def analyze_file(path, id_style: str = "full", **opts) -> CallGraph:
    prog = parse_file(path)
    return build_callgraph(prog, solve(prog, **opts), id_style)


__all__ = ["analyze_source", "analyze_file", "to_json", "to_dot", "to_dict", "to_icfg_dot", "CallGraph"]
