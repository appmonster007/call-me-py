"""Graphs: call edges between nodes, the CFG of one function, and ICFG edges."""
from __future__ import annotations

from dataclasses import dataclass

from .ids import FuncId, Site
from .values import Node


@dataclass(frozen=True)
class Edge:
    """A call edge: the node `caller` may call the node `callee` at `site`."""

    caller: Node
    site: Site
    callee: Node


# CFG node kinds
ENTRY, EXIT = "entry", "exit"
STMT = "stmt"              # a statement's own effect (after the calls inside it)
BRANCH = "branch"          # a test: if / while / assert / case / and-or / comprehension filter
LOOP = "loop"              # loop head: while join point, for / comprehension "next item"
HANDLER = "handler"        # start of an except clause
CALL = "call"              # the call is made here ...
AFTER_CALL = "after_call"  # ... and its result arrives here (the return point)

# edge kinds: intraprocedural ...
FLOW, TRUE, FALSE, EXC, CALL_TO_RETURN = "flow", "true", "false", "exc", "call_to_return"
# ... and interprocedural (ICFG only)
CALL_EDGE, RETURN_EDGE = "call", "return"


@dataclass(frozen=True)
class CFGNode:
    """One program point of a function body.

    idx   index of the point in `CFG.nodes`
    kind  ENTRY, EXIT, STMT, BRANCH, LOOP, HANDLER, CALL or AFTER_CALL
    site  the call made at a CALL point or returning at its AFTER_CALL point, else None
    """

    idx: int
    kind: str
    line: int
    col: int
    text: str
    site: Site | None = None  # set for CALL / AFTER_CALL


@dataclass(frozen=True)
class CFGEdge:
    """Edge between two points of one CFG; `kind` is FLOW, TRUE, FALSE, EXC or CALL_TO_RETURN."""

    src: int
    dst: int
    kind: str = FLOW


@dataclass(frozen=True, eq=False)
class CFG:
    """Control-flow graph of one function's body.

    It does not depend on contexts: every node of the function shares it. `calls` maps each
    syntactic call site to its (CALL point, AFTER_CALL point) pair.
    """

    func: FuncId
    nodes: tuple[CFGNode, ...]
    edges: tuple[CFGEdge, ...]
    calls: dict[Site, tuple[int, int]]  # site -> (CALL point, AFTER_CALL point)
    entry: int = 0
    exit: int = 1

    def succ(self, i: int) -> list[CFGEdge]:
        """Return the edges leaving point `i`."""
        return self._adj()[0].get(i, [])

    def pred(self, i: int) -> list[CFGEdge]:
        """Return the edges entering point `i`."""
        return self._adj()[1].get(i, [])

    def _adj(self):
        """Return the outgoing and incoming edges of every point, built once and then cached."""
        adj = self.__dict__.get("_adj_cache")
        if adj is None:
            out: dict[int, list[CFGEdge]] = {}
            inc: dict[int, list[CFGEdge]] = {}
            for e in self.edges:
                out.setdefault(e.src, []).append(e)
                inc.setdefault(e.dst, []).append(e)
            adj = (out, inc)
            object.__setattr__(self, "_adj_cache", adj)
        return adj


@dataclass(frozen=True)
class IEdge:
    """ICFG edge from point `src_pt` of node `src` to point `dst_pt` of node `dst`.

    kind  an intraprocedural kind when `src` is `dst`; CALL_EDGE from a CALL point to the callee's
          entry; or RETURN_EDGE from the callee's exit to the AFTER_CALL point of the same caller
          node, so a return goes back only to the context that made the call
    site  the call site of a CALL_EDGE or RETURN_EDGE, else None
    """

    src: Node
    src_pt: int
    dst: Node
    dst_pt: int
    kind: str
    site: Site | None = None
