"""Call graph: context-sensitive nodes/edges plus the collapsed function-level view."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple

from .extract import Program, call_sites
from .icfg import ICFG, build_icfg
from .model import Edge, FuncId, Node, Site
from .transfer import State


def node_label(node: Node, style: str = "full") -> str:
    c, fn = node.ctx, node.func.render(style)
    if c.merged:
        return f"{fn}[*]"
    if not c.params:
        return fn
    parts = ", ".join(
        f"{n}={{{','.join(sorted({t.func.render(style) for t in toks}))}}}" for n, toks in c.params)
    return f"{fn}[{parts}]"


@dataclass(frozen=True, eq=False)
class CallGraph:
    prog: Program
    nodes: Tuple[Node, ...]
    edges: Tuple[Edge, ...]
    sites: Tuple[Site, ...]  # every syntactic call site of reachable functions
    labels: Dict[Node, str]  # unique printable id per node
    style: str = "full"  # id style: full | module | short (see FuncId.render)

    def name(self, f: FuncId) -> str:
        return f.render(self.style)

    def reachable(self) -> FrozenSet[FuncId]:
        return frozenset(n.func for n in self.nodes)

    def function_edges(self) -> FrozenSet[Tuple[str, str]]:
        """Context-insensitive view: (caller name, callee name) pairs."""
        return frozenset((self.name(e.caller.func), self.name(e.callee.func)) for e in self.edges)

    def icfg(self) -> ICFG:
        """Interprocedural CFG of the analysed (function, context) nodes."""
        cached = self.__dict__.get("_icfg")
        if cached is None:
            cached = build_icfg(self.prog, self.nodes, self.edges)
            object.__setattr__(self, "_icfg", cached)
        return cached

    def targets(self, site: Site) -> FrozenSet[str]:
        return frozenset(self.name(e.callee.func) for e in self.edges if e.site == site)


def build_callgraph(prog: Program, state: State, style: str = "full") -> CallGraph:
    nodes = tuple(state.order)
    edges = tuple(sorted(state.edges, key=lambda e: (
        e.caller.func.uid, e.site.line, e.site.col, e.site.kind, node_label(e.callee))))
    reach = {n.func for n in nodes}
    sites = tuple(s for f in prog.funcs if f in reach for s in call_sites(prog, f))
    by_label: Dict[str, list] = {}
    for n in nodes:
        by_label.setdefault(node_label(n, style), []).append(n)
    labels = {}
    for label, group in by_label.items():
        for i, n in enumerate(group):
            labels[n] = label if len(group) == 1 else f"{label}#{i + 1}"
    return CallGraph(prog, nodes, edges, sites, labels, style)
