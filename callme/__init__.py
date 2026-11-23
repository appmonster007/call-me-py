"""call-me-py: context-sensitive call graph construction for Python."""
import os

from .callgraph import CallGraph, build_callgraph
from .export import to_dict, to_dot, to_json
from .extract import build_program, parse_file
from .solver import solve


def analyze_source(source: str, filename: str = "<string>", id_style: str = "full", *,
                   include_unreachable: bool = False, max_contexts: int = 16,
                   max_rounds: int = 10_000) -> CallGraph:
    prog = build_program(source, filename)
    state = solve(prog, include_unreachable=include_unreachable,
                  max_contexts=max_contexts, max_rounds=max_rounds)
    return build_callgraph(prog, state, id_style)


def analyze_file(path: str | os.PathLike[str], id_style: str = "full", *,
                 include_unreachable: bool = False, max_contexts: int = 16,
                 max_rounds: int = 10_000) -> CallGraph:
    prog = parse_file(path)
    state = solve(prog, include_unreachable=include_unreachable,
                  max_contexts=max_contexts, max_rounds=max_rounds)
    return build_callgraph(prog, state, id_style)


__all__ = ["analyze_source", "analyze_file", "to_json", "to_dot", "to_dict", "CallGraph"]
