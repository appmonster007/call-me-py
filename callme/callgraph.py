"""Call graph: context-sensitive nodes/edges plus the collapsed function-level view."""
from __future__ import annotations

from dataclasses import dataclass

from .extract import Program, call_sites
from .icfg import ICFG, build_icfg
from .model import Edge, FuncId, Node, Site, token_label
from .transfer import State


def node_label(node: Node, style: str = "full") -> str:
    c, fn = node.ctx, node.func.render(style)
    if c.merged:
        return f"{fn}[*]"
    if not c.params:
        return fn
    parts = ", ".join(
        f"{n}={{{','.join(sorted({token_label(t, style) for t in toks}))}}}" for n, toks in c.params)
    return f"{fn}[{parts}]"


@dataclass(frozen=True, eq=False)
class CallGraph:
    prog: Program
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    sites: tuple[Site, ...]  # every syntactic call site of reachable functions
    labels: dict[Node, str]  # unique printable id per node
    state: State  # the solver state this graph was built from (for later passes)
    style: str = "full"  # id style: full | module | short (see FuncId.render)

    def name(self, f: FuncId) -> str:
        return f.render(self.style)

    def reachable(self) -> frozenset[FuncId]:
        return frozenset(n.func for n in self.nodes)

    def function_edges(self) -> frozenset[tuple[str, str]]:
        """Context-insensitive view: (caller name, callee name) pairs."""
        return frozenset((self.name(e.caller.func), self.name(e.callee.func)) for e in self.edges)

    def icfg(self) -> ICFG:
        """Interprocedural CFG of the analysed (function, context) nodes."""
        cached: ICFG | None = self.__dict__.get("_icfg")
        if cached is None:
            cached = build_icfg(self.prog, self.nodes, self.edges)
            object.__setattr__(self, "_icfg", cached)
        return cached

    def targets(self, site: Site) -> frozenset[str]:
        return frozenset(self.name(e.callee.func) for e in self.edges if e.site == site)


def build_callgraph(prog: Program, state: State, style: str = "full") -> CallGraph:
    nodes = tuple(state.order)
    edges = tuple(sorted(state.edges, key=lambda e: (
        e.caller.func.uid, e.site.line, e.site.col, e.site.kind, node_label(e.callee))))
    reach = {n.func for n in nodes}
    syntactic = [s for f in prog.funcs if f in reach for s in call_sites(prog, f)]
    syn_set = set(syntactic)
    implicit = sorted({e.site for e in edges if e.site not in syn_set},
                      key=lambda s: (s.caller.uid, s.line, s.col, s.kind))
    sites = tuple(syntactic) + tuple(implicit)
    by_label: dict[str, list[Node]] = {}
    for n in nodes:
        by_label.setdefault(node_label(n, style), []).append(n)
    labels: dict[Node, str] = {}
    for label, group in by_label.items():
        for i, n in enumerate(group):
            labels[n] = label if len(group) == 1 else f"{label}#{i + 1}"
    return CallGraph(prog, nodes, edges, sites, labels, state, style)
