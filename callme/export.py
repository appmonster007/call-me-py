"""JSON and Graphviz DOT output for a CallGraph."""
from __future__ import annotations

import json
from collections import defaultdict

from .callgraph import CallGraph
from .model import Node, Site


def _site(s: Site) -> dict[str, int | str]:
    return {"line": s.line, "col": s.col, "text": s.text, "kind": s.kind}


def _ctx(cg: CallGraph, node: Node) -> dict[str, list[str]]:
    if node.ctx.merged:
        return {"*": []}
    return {n: sorted({cg.name(t.func) for t in toks}) for n, toks in node.ctx.params}


def to_dict(cg: CallGraph) -> dict[str, object]:
    prog = cg.prog
    reach = cg.reachable()
    return {
        "file": prog.file,
        "functions": [
            {"id": cg.name(i.id), "kind": i.kind, "file": i.id.file, "line": i.id.lineno,
             "parent": cg.name(i.parent) if i.parent else None,
             "name": i.id.qualname, "col": i.id.col,
             "reachable": i.id in reach}
            for i in prog.funcs.values()
        ],
        "sites": [
            {"caller": cg.name(s.caller), **_site(s), "targets": sorted(cg.targets(s))}
            for s in cg.sites
        ],
        "edges": [  # function-level, deduplicated
            {"caller": c, "callee": d}
            for c, d in sorted(cg.function_edges())
        ],
        "contexts": [
            {"id": cg.labels[n], "function": cg.name(n.func), "context": _ctx(cg, n)}
            for n in cg.nodes
        ],
        "context_edges": [
            {"caller": cg.labels[e.caller], "callee": cg.labels[e.callee], "site": _site(e.site)}
            for e in cg.edges
        ],
    }


def to_json(cg: CallGraph, indent: int = 2) -> str:
    return json.dumps(to_dict(cg), indent=indent)


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_dot(cg: CallGraph, contexts: bool = False, returns: bool = False) -> str:
    """Function-level graph by default; `contexts` shows one box per (function, context);
    `returns` adds dotted return edges (callee -> caller)."""
    lines: list[str] = ["digraph callgraph {", "  rankdir=LR;",
                        '  node [shape=box, fontname="Helvetica"];']
    if contexts:
        for n in cg.nodes:
            lines.append(f"  {_q(cg.labels[n])};")
        for e in cg.edges:
            lbl = f"L{e.site.line}"
            lines.append(f"  {_q(cg.labels[e.caller])} -> {_q(cg.labels[e.callee])} [label={_q(lbl)}];")
            if returns:
                lines.append(f"  {_q(cg.labels[e.callee])} -> {_q(cg.labels[e.caller])} "
                             + "[style=dotted, constraint=false];")
    else:
        reach = cg.reachable()
        for info in cg.prog.funcs.values():
            style = "" if info.id in reach else " [style=dashed, color=gray, fontcolor=gray]"
            lines.append(f"  {_q(cg.name(info.id))}{style};")
        sites: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
        for e in cg.edges:
            sites[(cg.name(e.caller.func), cg.name(e.callee.func))].add(e.site.line)
        for (c, d), ls in sorted(sites.items()):
            lbl = ",".join(f"L{x}" for x in sorted(ls))
            lines.append(f"  {_q(c)} -> {_q(d)} [label={_q(lbl)}];")
            if returns:
                lines.append(f"  {_q(d)} -> {_q(c)} [style=dotted, constraint=false];")
    lines.append("}")
    return "\n".join(lines) + "\n"
