"""Core data types shared by every stage of the analysis.

Vocabulary:
  FuncId   identity of one function (def, lambda or the module body)
  Closure  a *token*: the abstract value "this function, created in that context"
  Ctx      a *context*: the abstract entry state of a
           function (param -> set of closures) plus the captured defining node
  Node     (function, context): one analysed instance of a function
  Edge     call edge between two nodes at a syntactic call site
  CFG      per-function control-flow graph over program points (CFGNode)
  IEdge    ICFG edge between program points of two (function, context) nodes
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Dict, FrozenSet, List, Optional, Tuple


@dataclass(frozen=True)
class FuncId:
    """Identity of one function, stored as separate parts; strings are built on demand.

    file      source path as given (e.g. "pkg/util.py")
    qualname  dotted scope path of the function (e.g. "Outer.make.inner")
    lineno    1-based line of the definition
    col       0-based column of the definition
    """

    qualname: str
    file: str
    lineno: int
    col: int

    @property
    def name(self) -> str:
        """Last component of the qualname ("inner")."""
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def scope(self) -> str:
        """Enclosing scope part of the qualname ("Outer.make"), "" at top level."""
        return self.qualname.rpartition(".")[0]

    @property
    def module(self) -> str:
        """Dotted module name derived from the file path ("pkg.util")."""
        parts = PurePath(self.file).with_suffix("").parts
        parts = tuple(p for p in parts if p not in ("/", ".", ".."))
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts) or self.file

    @property
    def uid(self) -> str:
        """Unique id: file:line:col:qualname (stable even when names repeat)."""
        return self.render("full")

    def render(self, style: str = "full") -> str:
        """Build the string form: "full" = file:line:col:qualname, "module" =
        module.qualname, "short" = qualname."""
        if style == "full":
            return f"{self.file}:{self.lineno}:{self.col}:{self.qualname}"
        if style == "module":
            return f"{self.module}.{self.qualname}"
        if style == "short":
            return self.qualname
        raise ValueError(f"unknown id style: {style}")

    def __str__(self) -> str:
        return self.uid


@dataclass(frozen=True)
class Closure:
    """Token: function `func` created while `parent` was running.

    `parent` is None when the defining context is unknown or was widened to keep
    the analysis finite; free variables are then read from every context.
    """

    func: FuncId
    parent: Optional["Node"] = None
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash((self.func, self.parent)))

    def __hash__(self) -> int:
        return self._h


ParamState = Tuple[Tuple[str, FrozenSet[Closure]], ...]


@dataclass(frozen=True)
class Ctx:
    """Abstract entry state. Only parameters holding functions are recorded."""

    params: ParamState = ()
    parent: Optional["Node"] = None
    merged: bool = False  # widened context: entry state lives in State.entry
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash((self.params, self.parent, self.merged)))

    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Node:
    func: FuncId
    ctx: Ctx
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash((self.func, self.ctx)))

    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Site:
    """A syntactic call site (or decorator application)."""

    caller: FuncId
    line: int
    col: int
    text: str
    kind: str = "call"  # "call" | "decorator"


@dataclass(frozen=True)
class Edge:
    caller: Node
    site: Site
    callee: Node


# ---------------------------------------------------------------- control flow
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
    """One program point of a function body."""

    idx: int
    kind: str
    line: int
    col: int
    text: str
    site: Optional[Site] = None  # set for CALL / AFTER_CALL


@dataclass(frozen=True)
class CFGEdge:
    src: int
    dst: int
    kind: str = FLOW


@dataclass(frozen=True, eq=False)
class CFG:
    """Control-flow graph of one function. Context independent: every
    (function, context) node of the analysis shares its function's CFG."""

    func: FuncId
    nodes: Tuple[CFGNode, ...]
    edges: Tuple[CFGEdge, ...]
    calls: Dict[Site, Tuple[int, int]]  # site -> (CALL point, AFTER_CALL point)
    entry: int = 0
    exit: int = 1

    def succ(self, i: int) -> List[CFGEdge]:
        return self._adj()[0].get(i, [])

    def pred(self, i: int) -> List[CFGEdge]:
        return self._adj()[1].get(i, [])

    def _adj(self):
        adj = self.__dict__.get("_adj_cache")
        if adj is None:
            out: Dict[int, List[CFGEdge]] = {}
            inc: Dict[int, List[CFGEdge]] = {}
            for e in self.edges:
                out.setdefault(e.src, []).append(e)
                inc.setdefault(e.dst, []).append(e)
            adj = (out, inc)
            object.__setattr__(self, "_adj_cache", adj)
        return adj


@dataclass(frozen=True)
class IEdge:
    """ICFG edge from program point `src_pt` of `src` to `dst_pt` of `dst`.

    kind: an intraprocedural kind (src is dst), CALL_EDGE (CALL point -> callee
    entry) or RETURN_EDGE (callee exit -> AFTER_CALL point of the same caller
    context, so returns go back only to the context that made the call).
    """

    src: Node
    src_pt: int
    dst: Node
    dst_pt: int
    kind: str
    site: Optional[Site] = None
