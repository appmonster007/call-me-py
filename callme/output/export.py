"""JSON and Graphviz DOT output for a CallGraph."""
from __future__ import annotations

import json
from collections import defaultdict

from ..model import Node, Site, token_label
from .callgraph import CallGraph


def _site(s: Site) -> dict[str, int | str]:
    """Return the JSON form of a call site: line, column, text and kind."""
    return {"line": s.line, "col": s.col, "text": s.text, "kind": s.kind}


def _ctx(cg: CallGraph, node: Node) -> dict[str, list[str]]:
    """Return a node's context for JSON: token labels per parameter, or `{"*": []}` if widened."""
    if node.ctx.merged:
        return {"*": []}
    return {n: sorted({token_label(t, cg.style) for t in toks}) for n, toks in node.ctx.params}


def to_dict(cg: CallGraph, icfg: bool = False) -> dict[str, object]:
    """Return the call graph as a JSON-ready dict, laid out as documented in docs/output.md.

    The keys are `file` (the first entry), `modules` (every loaded module and its file),
    `functions` (every function, reachable or not), `sites` (each with its possible
    targets), `edges` (function level, without duplicates), `contexts` and `context_edges`.

    icfg  also add the `icfg` key from `icfg_dict`
    """
    prog = cg.prog
    reach = cg.reachable()
    out: dict[str, object] = {
        "file": prog.file,
        "modules": [{"name": name, "file": m.file} for name, m in prog.modules.items()],
        "functions": [
            {"id": cg.name(i.id), "kind": i.kind, "module": i.id.module, "file": i.id.file,
             "line": i.id.lineno,
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
    if icfg:
        out["icfg"] = icfg_dict(cg)
    return out


def _point(cg: CallGraph, node: Node, pt: int) -> dict[str, str | int]:
    """Return the JSON form of an ICFG program point: the node's label and the point index."""
    return {"context": cg.labels[node], "point": pt}


def icfg_dict(cg: CallGraph) -> dict[str, object]:
    """Return the ICFG as a JSON-ready dict with keys `cfgs` and `edges`.

    `cfgs` holds the CFG of each reachable function once, since its contexts share it.
    `edges` holds the call and return edges between context nodes: a call goes from a CALL
    point to the callee's entry, a return from the callee's exit to the AFTER_CALL point.
    """
    g = cg.icfg()
    reach = cg.reachable()
    return {
        "cfgs": {
            cg.name(fid): {
                "entry": c.entry, "exit": c.exit,
                "nodes": [{"id": n.idx, "kind": n.kind, "line": n.line, "col": n.col,
                           "text": n.text} for n in c.nodes],
                "edges": [{"src": e.src, "dst": e.dst, "kind": e.kind} for e in c.edges],
            }
            for fid, c in cg.prog.cfgs.items() if fid in reach
        },
        "edges": [
            {"kind": e.kind, "src": _point(cg, e.src, e.src_pt),
             "dst": _point(cg, e.dst, e.dst_pt), "site": _site(e.site) if e.site else None}
            for e in [*g.call_edges(), *g.return_edges()]
        ],
    }


def to_json(cg: CallGraph, indent: int = 2, icfg: bool = False) -> str:
    """Return `to_dict(cg, icfg)` as JSON text, indented by `indent` spaces."""
    return json.dumps(to_dict(cg, icfg), indent=indent)


def _q(s: str) -> str:
    """Return `s` as a quoted DOT string, with backslashes and quotes escaped."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_dot(cg: CallGraph, contexts: bool = False, returns: bool = False) -> str:
    """Return the call graph as Graphviz DOT text.

    By default there is one box per function: unreachable functions are dashed and gray,
    and each edge is labelled with its call lines, e.g. "L12,L15".

    contexts  draw one box per (function, context) node and one edge per context edge
    returns   add a dotted return edge from callee to caller for each call edge
    """
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


_EDGE_STYLE = {
    "flow": "", "true": ' [label="T"]', "false": ' [label="F"]',
    "exc": " [color=red, style=dashed]", "call_to_return": " [style=dotted]",
    "call": " [color=blue, penwidth=1.5]", "return": " [color=darkgreen, style=dashed]",
}
_NODE_SHAPE = {"entry": "oval", "exit": "oval", "branch": "diamond", "loop": "hexagon",
               "call": "box, style=filled, fillcolor=lightblue",
               "after_call": "box, style=filled, fillcolor=honeydew"}


def to_icfg_dot(cg: CallGraph) -> str:
    """Return the ICFG as Graphviz DOT text.

    Each (function, context) node is a cluster holding its function's CFG, and call and
    return edges link the clusters. Point kinds get their own shapes and edge kinds their
    own styles; edges at implicit sites have no CFG point and are not drawn.
    """
    g = cg.icfg()
    ids = {n: i for i, n in enumerate(cg.nodes)}

    def pid(node: Node, pt: int) -> str:
        """Return the DOT id of point `pt` of `node`."""
        return f"n{ids[node]}_{pt}"

    lines = ["digraph icfg {", '  node [shape=box, fontname="Helvetica", fontsize=10];',
             "  compound=true;"]
    for n in cg.nodes:
        c = g.cfg(n)
        lines.append(f"  subgraph cluster_{ids[n]} {{")
        lines.append(f"    label={_q(cg.labels[n])};")
        for p in c.nodes:
            text = f"{p.kind}" if p.kind in ("entry", "exit") else f"L{p.line}: {p.text}"
            lines.append(f"    {pid(n, p.idx)} [label={_q(text)}, "
                         + f"shape={_NODE_SHAPE.get(p.kind, 'box')}];")
        for e in c.edges:
            lines.append(f"    {pid(n, e.src)} -> {pid(n, e.dst)}{_EDGE_STYLE[e.kind]};")
        lines.append("  }")
    for e in [*g.call_edges(), *g.return_edges()]:
        lines.append(f"  {pid(e.src, e.src_pt)} -> {pid(e.dst, e.dst_pt)}{_EDGE_STYLE[e.kind]};")
    lines.append("}")
    return "\n".join(lines) + "\n"
