"""Interprocedural CFG over (function, context) nodes.

Program points are pairs (Node, point index into the function's CFG). Each
context node gets a copy of its function's CFG edges; each context call edge
`Edge(caller, site, callee)` adds

  call    (caller, CALL point of site)  -> (callee, entry)
  return  (callee, exit)                -> (caller, AFTER_CALL point of site)

The CFG's call_to_return edge (CALL -> AFTER_CALL) carries the caller's own
state across the call. Because the return edge goes back to the same caller
context, only valid (call-matched) paths exist between contexts.

Edges at implicit sites (operator dunders, descriptors, `with`, iteration,
`__init_subclass__`: Site.kind not in SYNTACTIC_KINDS) have no CALL point in the
CFG; they are kept in `ICFG.implicit` and get no call / return edges.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Iterator

from ..analysis import State
from ..frontend import Program
from ..model import CALL_EDGE, CFG, RETURN_EDGE, Edge, IEdge, Node

Point = tuple[Node, int]
SYNTACTIC_KINDS = ("call", "decorator")  # site kinds that are CFG call points


@dataclass(frozen=True, eq=False)
class ICFG:
    """Interprocedural CFG: each node's copy of its function's CFG, plus call and return edges.

    A program point is a (node, CFG point index) pair. The edge lists are built on demand
    from `calls`; `succ` and `pred` cache them.

    calls     context call edges at CFG call points, which get call and return edges
    implicit  context call edges at implicit sites, which have no CFG point and get no edges
    """

    prog: Program
    nodes: tuple[Node, ...]
    calls: tuple[Edge, ...]  # context-sensitive call edges at CFG call points
    implicit: tuple[Edge, ...] = ()  # call edges at implicit sites (no CFG point)

    def cfg(self, node: Node) -> CFG:
        """Return the CFG of `node`'s function, which all its contexts share."""
        return self.prog.cfgs[node.func]

    def entry(self, node: Node) -> Point:
        """Return the entry point of `node`."""
        return node, self.cfg(node).entry

    def exit(self, node: Node) -> Point:
        """Return the exit point of `node`."""
        return node, self.cfg(node).exit

    def points(self) -> Iterator[Point]:
        """Yield every program point of every node."""
        for n in self.nodes:
            for p in self.cfg(n).nodes:
                yield n, p.idx

    def intra_edges(self) -> Iterator[IEdge]:
        """Yield each node's copy of its function's CFG edges."""
        for n in self.nodes:
            for e in self.cfg(n).edges:
                yield IEdge(n, e.src, n, e.dst, e.kind)

    def call_edges(self) -> Iterator[IEdge]:
        """Yield one edge per call: from the caller's CALL point to the callee's entry."""
        for e in self.calls:
            call_pt, _ = self.cfg(e.caller).calls[e.site]
            yield IEdge(e.caller, call_pt, e.callee, self.cfg(e.callee).entry, CALL_EDGE, e.site)

    def return_edges(self) -> Iterator[IEdge]:
        """Yield one edge per call: from the callee's exit to the caller's AFTER_CALL point.

        A return goes back only to the caller node that made the call, so only paths with
        matching calls and returns exist between contexts.
        """
        for e in self.calls:
            _, ret_pt = self.cfg(e.caller).calls[e.site]
            yield IEdge(e.callee, self.cfg(e.callee).exit, e.caller, ret_pt, RETURN_EDGE, e.site)

    def edges(self) -> Iterator[IEdge]:
        """Yield every edge: intraprocedural, then call, then return edges."""
        yield from self.intra_edges()
        yield from self.call_edges()
        yield from self.return_edges()

    def succ(self, pt: Point) -> list[IEdge]:
        """Return the edges leaving point `pt`."""
        return self._adj()[0].get(pt, [])

    def pred(self, pt: Point) -> list[IEdge]:
        """Return the edges entering point `pt`."""
        return self._adj()[1].get(pt, [])

    def _adj(self) -> tuple[dict[Point, list[IEdge]], dict[Point, list[IEdge]]]:
        """Return the outgoing and incoming edges of every point, built once and then cached."""
        adj: tuple[dict[Point, list[IEdge]], dict[Point, list[IEdge]]] | None = \
            self.__dict__.get("_adj_cache")
        if adj is None:
            out: dict[Point, list[IEdge]] = {}
            inc: dict[Point, list[IEdge]] = {}
            for e in self.edges():
                out.setdefault((e.src, e.src_pt), []).append(e)
                inc.setdefault((e.dst, e.dst_pt), []).append(e)
            adj = (out, inc)
            object.__setattr__(self, "_adj_cache", adj)
        return adj


def icfg_from_state(prog: Program, state: State) -> ICFG:
    """Return the ICFG of a call graph solver state, which is what a later pass reads."""
    return build_icfg(prog, state.order, state.edges)


def build_icfg(prog: Program, nodes: Iterable[Node], calls: Iterable[Edge]) -> ICFG:
    """Return the ICFG over `nodes` with the context call edges `calls`.

    Edges at implicit sites (operator dunders, descriptors, `with`, iteration and class
    creation) are kept apart in `ICFG.implicit`. Raises `ValueError` if a syntactic call or
    decorator site has no point in its CFG, which would mean the CFG builder and the
    interpreter disagree.
    """
    wired: list[Edge] = []
    implicit: list[Edge] = []
    for e in calls:
        if e.site in prog.cfgs[e.caller.func].calls:
            wired.append(e)
        elif e.site.kind in SYNTACTIC_KINDS:
            raise ValueError(f"call site missing from the CFG: {e.site}")
        else:
            implicit.append(e)
    return ICFG(prog, tuple(nodes), tuple(wired), tuple(implicit))
