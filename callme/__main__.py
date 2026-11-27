"""python -m callme FILE -o OUT [--format json|dot|icfg] [--contexts] [--returns] [--icfg]
                    [--flow-insensitive] [--load-state S] [--save-state S]

Multi-pass from the shell: --save-state keeps the solver state, --load-state resumes it
(only for the same, unchanged FILE), e.g. a second run with --include-unreachable."""
import argparse
import sys
from collections.abc import Sequence

from . import CallGraph, analyze_file, load_state, save_state, to_dot, to_icfg_dot, to_json
from .extract import call_sites, parse_file


def print_graph(cg: CallGraph) -> None:
    """Step-1 graph: function nesting edges and caller -> call-site edges (to stderr)."""
    prog, name = cg.prog, cg.name
    err = sys.stderr
    print("# function identifier graph (lexical parent -> child)", file=err)
    for parent, child in prog.nesting_edges():
        print(f"{name(parent)} -> {name(child)}", file=err)
    print("# call sites (caller -> call site @line:col)", file=err)
    for fid in prog.funcs:
        for s in call_sites(prog, fid):
            print(f"{name(fid)} -> {s.text} @{s.line}:{s.col} [{s.kind}]", file=err)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="callme", description=__doc__)
    ap.add_argument("file")
    ap.add_argument("--format", choices=["json", "dot", "icfg"], default="json",
                    help="icfg = DOT of the interprocedural CFG (one cluster per context)")
    ap.add_argument("--icfg", action="store_true", help="JSON: include CFGs and call/return edges")
    ap.add_argument("--contexts", action="store_true", help="DOT: one node per (function, context)")
    ap.add_argument("--returns", action="store_true", help="DOT: add dotted return edges")
    ap.add_argument("--include-unreachable", action="store_true",
                    help="also analyse functions never called from the module body")
    ap.add_argument("--id-style", choices=["full", "module", "short"], default="full",
                    help="function id text: file:line:col:qualname | module.qualname | qualname")
    ap.add_argument("--short-ids", dest="id_style", action="store_const", const="short",
                    help="same as --id-style short")
    ap.add_argument("--print-graph", action="store_true",
                    help="print the intermediate graph (nesting + call-site edges, source -> target) to stderr")
    ap.add_argument("--max-contexts", type=int, default=None,
                    help="context budget per function (default 16, or the loaded state's)")
    ap.add_argument("--flow-insensitive", dest="flow_sensitive", action="store_const",
                    const=False, default=None,
                    help="treat each local as the union of all its bindings (less precise;"
                         " default: flow-sensitive, or the loaded state's mode)")
    ap.add_argument("--load-state", metavar="PATH", help="resume from a state saved for this file")
    ap.add_argument("--save-state", metavar="PATH", help="save the solver state after this pass")
    ap.add_argument("-o", "--output", help="write the graph here; without it nothing is printed")
    a = ap.parse_args(argv)
    state = load_state(parse_file(a.file), a.load_state) if a.load_state else None
    cg = analyze_file(a.file, id_style=a.id_style, include_unreachable=a.include_unreachable,
                      max_contexts=a.max_contexts, flow_sensitive=a.flow_sensitive, state=state)
    if a.save_state:
        save_state(cg.state, a.save_state)
    if a.print_graph:
        print_graph(cg)
    if a.output:  # without -o nothing is written or printed (except --print-graph)
        if a.format == "json":
            text = to_json(cg, icfg=a.icfg)
        elif a.format == "icfg":
            text = to_icfg_dot(cg)
        else:
            text = to_dot(cg, a.contexts, a.returns)
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
