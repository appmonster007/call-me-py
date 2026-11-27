"""Command-line interface for call-me-py (`python -m callme`).

Parses CLI options, runs the call graph analysis on one or more Python entry
files and the project modules they import, and writes the result as JSON,
Graphviz DOT, or an interprocedural CFG (ICFG) DOT graph. Nothing is written
unless `-o`/`--output` is given (except `--print-graph` to stderr).

Usage:
  python -m callme FILE [FILE ...] -o OUT [--format json|dot|icfg] [--contexts]
                               [--returns] [--icfg] [--flow-insensitive]
                               [--path DIR] [--load-state S] [--save-state S]

Imported modules are looked up in each FILE's root directory, then in every
`--path DIR`. Multi-pass from the shell: `--save-state` writes the solver state
to JSON and `--load-state` resumes it for the same, unchanged files (e.g. a
second pass with `--include-unreachable`).
"""
import argparse
import sys
from collections.abc import Sequence

from . import CallGraph, analyze_files, load_state, save_state, to_dot, to_icfg_dot, to_json
from .extract import call_sites, parse_files


def print_graph(cg: CallGraph) -> None:
    """Write the program's structure, as `cg` names it, to stderr.

    Prints two lists in `cg`'s id style: the lexical nesting of functions
    (`parent -> child`), then every syntactic call site (`caller -> text @line:col [kind]`).
    """
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
    """Parse the command-line arguments, analyse the files, and write the requested outputs.

    Optionally resumes from a saved solver state (`--load-state`), saves the final state
    (`--save-state`), prints the program structure to stderr (`--print-graph`), and writes
    JSON, DOT or ICFG DOT to `-o`/`--output`.

    argv  argument list to parse (defaults to `sys.argv[1:]` when None)

    Returns 0 on success.
    """
    ap = argparse.ArgumentParser(prog="callme", description=__doc__)
    ap.add_argument("files", nargs="+", metavar="FILE",
                    help="entry file; the project modules it imports are analysed too")
    ap.add_argument("--path", action="append", default=[], metavar="DIR",
                    help="also search DIR for imported modules (repeatable, like PYTHONPATH)")
    ap.add_argument("--format", choices=["json", "dot", "icfg"], default="json",
                    help="icfg = DOT of the interprocedural CFG (one cluster per context)")
    ap.add_argument("--icfg", action="store_true", help="JSON: include CFGs and call/return edges")
    ap.add_argument("--contexts", action="store_true", help="DOT: one node per (function, context)")
    ap.add_argument("--returns", action="store_true", help="DOT: add dotted return edges")
    ap.add_argument("--include-unreachable", action="store_true",
                    help="also analyse functions never called and modules never imported")
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
                         + " default: flow-sensitive, or the loaded state's mode)")
    ap.add_argument("--load-state", metavar="PATH",
                    help="resume from a state saved for these files")
    ap.add_argument("--save-state", metavar="PATH", help="save the solver state after this pass")
    ap.add_argument("-o", "--output", help="write the graph here; without it nothing is printed")
    a = ap.parse_args(argv)
    state = (load_state(parse_files(a.files, search_path=a.path), a.load_state)
             if a.load_state else None)
    cg = analyze_files(a.files, id_style=a.id_style, search_path=a.path,
                       include_unreachable=a.include_unreachable, max_contexts=a.max_contexts,
                       flow_sensitive=a.flow_sensitive, state=state)
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
