"""Interprocedural CFG over (function, context) nodes.

Program points are pairs (Node, point index into the function's CFG). Each
context node gets a copy of its function's CFG edges; each context call edge
`Edge(caller, site, callee)` adds

  call    (caller, CALL point of site)  -> (callee, entry)
  return  (callee, exit)                -> (caller, AFTER_CALL point of site)

The CFG's call_to_return edge (CALL -> AFTER_CALL) carries the caller's own
state across the call. Because the return edge goes back to the same caller
context, only valid (call-matched) paths exist between contexts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, List, Tuple

from .extract import Program
from .model import CALL_EDGE, CFG, RETURN_EDGE, Edge, IEdge, Node

Point = Tuple[Node, int]


@dataclass(frozen=True, eq=False)
class ICFG:
    prog: Program
    nodes: Tuple[Node, ...]
    calls: Tuple[Edge, ...]  # context-sensitive call edges

    def cfg(self, node: Node) -> CFG:
        return self.prog.cfgs[node.func]

    def entry(self, node: Node) -> Point:
        return node, self.cfg(node).entry

    def exit(self, node: Node) -> Point:
        return node, self.cfg(node).exit

    def points(self) -> Iterator[Point]:
        for n in self.nodes:
            for p in self.cfg(n).nodes:
                yield n, p.idx

    def intra_edges(self) -> Iterator[IEdge]:
        for n in self.nodes:
            for e in self.cfg(n).edges:
                yield IEdge(n, e.src, n, e.dst, e.kind)

    def call_edges(self) -> Iterator[IEdge]:
        for e in self.calls:
            call_pt, _ = self.cfg(e.caller).calls[e.site]
            yield IEdge(e.caller, call_pt, e.callee, self.cfg(e.callee).entry, CALL_EDGE, e.site)

    def return_edges(self) -> Iterator[IEdge]:
        for e in self.calls:
            _, ret_pt = self.cfg(e.caller).calls[e.site]
            yield IEdge(e.callee, self.cfg(e.callee).exit, e.caller, ret_pt, RETURN_EDGE, e.site)

    def edges(self) -> Iterator[IEdge]:
        yield from self.intra_edges()
        yield from self.call_edges()
        yield from self.return_edges()

    def succ(self, pt: Point) -> List[IEdge]:
        return self._adj()[0].get(pt, [])

    def pred(self, pt: Point) -> List[IEdge]:
        return self._adj()[1].get(pt, [])

    def _adj(self):
        adj = self.__dict__.get("_adj_cache")
        if adj is None:
            out: Dict[Point, List[IEdge]] = {}
            inc: Dict[Point, List[IEdge]] = {}
            for e in self.edges():
                out.setdefault((e.src, e.src_pt), []).append(e)
                inc.setdefault((e.dst, e.dst_pt), []).append(e)
            adj = (out, inc)
            object.__setattr__(self, "_adj_cache", adj)
        return adj


def build_icfg(prog: Program, nodes, calls) -> ICFG:
    missing = [e.site for e in calls if e.site not in prog.cfgs[e.caller.func].calls]
    if missing:
        raise ValueError(f"call sites missing from the CFG: {missing[:3]}")
    return ICFG(prog, tuple(nodes), tuple(calls))
