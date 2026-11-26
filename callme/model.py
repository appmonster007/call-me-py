"""Core data types shared by every stage of the analysis.

Vocabulary:
  FuncId       identity of one function (def, lambda or the module body)
  ClassId      identity of one class definition
  Closure      a *token*: the abstract value "this function, created in that context"
  Builtin      a *token* for a builtin / stdlib callable modelled by a stub (see stubs.py)
  ClassObj     a *token* representing a class object
  Instance     a *token* representing an abstract instance of a class (per allocation site)
  Box          a *token* for a mutable container (list / dict / set), per allocation site
  Alloc        an allocation site: where an instance or container is created
  BoundMethod  a *token*: a callable with a receiver pre-bound as first argument
  ClassMethod  a *token*: `classmethod(func)` descriptor binding the owner class
  StaticMethod a *token*: `staticmethod(func)` descriptor returning `func` unbound
  Super        a *token*: `super(start, receiver)` proxy along `receiver`'s MRO
  Property     a *token*: `property(fget, fset, fdel)` descriptor
  Ctx          a *context*: the abstract entry state of a
               function (param -> set of tokens) plus the captured defining node
  Node         (function, context): one analysed instance of a function
  Edge         call edge between two nodes at a syntactic call site
  CFG          per-function control-flow graph over program points (CFGNode)
  IEdge        ICFG edge between program points of two (function, context) nodes
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import override


@dataclass(frozen=True)
class _SymId:
    """Shared identity layout for functions and classes; strings are built on demand.

    file      source path as given (e.g. "pkg/util.py")
    qualname  dotted scope path of the definition (e.g. "Outer.make.inner")
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

    @override
    def __str__(self) -> str:
        return self.uid


@dataclass(frozen=True)
class FuncId(_SymId):
    """Identity of one function (def, async def, lambda or module body)."""


@dataclass(frozen=True)
class ClassId(_SymId):
    """Identity of one class definition."""


@dataclass(frozen=True)
class Closure:
    """Token: function `func` created while `parent` was running.

    `parent` is None when the defining context is unknown or was widened to keep
    the analysis finite; free variables are then read from every context.
    """

    func: FuncId
    parent: Node | None = None
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash((self.func, self.parent)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Builtin:
    """Token: a builtin or stdlib callable (or stub module) modelled in stubs.py.

    `bound` / `bound_kw` are pre-bound arguments prepended to every call: the
    receiver of a builtin method (`list.sort`) or the arguments of a partial.
    """

    name: str
    bound: tuple[frozenset[Token], ...] = ()
    bound_kw: tuple[tuple[str, frozenset[Token]], ...] = ()
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        toks = [t for v in self.bound for t in v] + [t for _, v in self.bound_kw for t in v]
        if toks:
            object.__setattr__(self, "depth", 1 + max(t.depth for t in toks))
        object.__setattr__(self, "_h", hash((self.name, self.bound, self.bound_kw)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class ClassObj:
    """Token: a class object."""

    cls: ClassId
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash(("class", self.cls)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Alloc:
    """Allocation site: the expression in function `func` that creates an object."""

    func: FuncId
    line: int
    col: int

    @classmethod
    def of(cls, site: Site) -> Alloc:
        """Allocation site of an object created by the call at `site`."""
        return cls(site.caller, site.line, site.col)


@dataclass(frozen=True)
class Instance:
    """Token: an abstract instance of `cls` created at `alloc` (None = unknown site).

    Instance attributes are stored per token (State.inst_attrs), so two objects of
    the same class created at different sites keep separate fields.
    """

    cls: ClassId
    alloc: Alloc | None = None
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash(("inst", self.cls, self.alloc)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class BoundMethod:
    """Token: callable `func` with `receiver` pre-bound as first argument."""

    func: Token
    receiver: ClassObj | Instance
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "depth", 1 + max(self.func.depth, self.receiver.depth))
        object.__setattr__(self, "_h", hash(("bound", self.func, self.receiver)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class ClassMethod:
    """Token: `classmethod(func)` descriptor binding the owner class as first arg."""

    func: Token
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "depth", 1 + self.func.depth)
        object.__setattr__(self, "_h", hash(("classmethod", self.func)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class StaticMethod:
    """Token: `staticmethod(func)` descriptor returning `func` unbound."""

    func: Token
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "depth", 1 + self.func.depth)
        object.__setattr__(self, "_h", hash(("staticmethod", self.func)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Super:
    """Token: `super(start, receiver)` proxy searching `receiver`'s MRO after `start`."""

    start: ClassId
    receiver: ClassObj | Instance
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "depth", 1 + self.receiver.depth)
        object.__setattr__(self, "_h", hash(("super", self.start, self.receiver)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Property:
    """Token: `property(fget, fset, fdel)` descriptor."""

    fget: frozenset[Token] = frozenset()
    fset: frozenset[Token] = frozenset()
    fdel: frozenset[Token] = frozenset()
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        toks = [*self.fget, *self.fset, *self.fdel]
        if toks:
            object.__setattr__(self, "depth", 1 + max(t.depth for t in toks))
        object.__setattr__(self, "_h", hash(("property", self.fget, self.fset, self.fdel)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Box:
    """Token: the mutable container (`kind` = list / dict / set) created at `alloc`.

    Values stay collapsed: a container literal evaluates to its elements plus its
    Box. What is stored into the container later (`xs.append(f)`, `d[k] = f`) goes
    to State.heap[box], so every alias of the container sees it (State.deref).
    """

    kind: str
    alloc: Alloc
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash(("box", self.kind, self.alloc)))

    @override
    def __hash__(self) -> int:
        return self._h


Token = (Closure | Builtin | ClassObj | Instance | BoundMethod
         | ClassMethod | StaticMethod | Super | Property | Box)


def token_label(tok: Token, style: str = "full") -> str:
    """Printable name of a token."""
    if isinstance(tok, Builtin):
        if tok.bound and tok.name.startswith("<partial"):
            inner = ",".join(sorted(token_label(t, style) for t in tok.bound[0]))
            return f"{tok.name[:-1]}:{inner}>"
        return tok.name
    if isinstance(tok, ClassObj):
        return f"<class {tok.cls.render(style)}>"
    if isinstance(tok, Instance):
        at = f"@{tok.alloc.line}:{tok.alloc.col}" if tok.alloc else ""
        return f"{tok.cls.render(style)}(){at}"
    if isinstance(tok, Box):
        return f"<{tok.kind}@{tok.alloc.line}:{tok.alloc.col}>"
    if isinstance(tok, BoundMethod):
        return f"{token_label(tok.func, style)}@{token_label(tok.receiver, style)}"
    if isinstance(tok, ClassMethod):
        return f"classmethod({token_label(tok.func, style)})"
    if isinstance(tok, StaticMethod):
        return f"staticmethod({token_label(tok.func, style)})"
    if isinstance(tok, Super):
        return f"super({tok.start.render(style)}, {token_label(tok.receiver, style)})"
    if isinstance(tok, Property):
        inner = ",".join(sorted(token_label(t, style) for t in tok.fget))
        return f"property({inner})"
    return tok.func.render(style)


Vals = frozenset[Token]  # abstract value: which callables an expression may be
EMPTY: Vals = frozenset()
ParamState = tuple[tuple[str, frozenset[Token]], ...]


@dataclass(frozen=True)
class Ctx:
    """Abstract entry state. Only parameters holding functions are recorded."""

    params: ParamState = ()
    parent: Node | None = None
    merged: bool = False  # widened context: entry state lives in State.entry
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash((self.params, self.parent, self.merged)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Node:
    func: FuncId
    ctx: Ctx
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash((self.func, self.ctx)))

    @override
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
    site: Site | None = None  # set for CALL / AFTER_CALL


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
    nodes: tuple[CFGNode, ...]
    edges: tuple[CFGEdge, ...]
    calls: dict[Site, tuple[int, int]]  # site -> (CALL point, AFTER_CALL point)
    entry: int = 0
    exit: int = 1

    def succ(self, i: int) -> list[CFGEdge]:
        return self._adj()[0].get(i, [])

    def pred(self, i: int) -> list[CFGEdge]:
        return self._adj()[1].get(i, [])

    def _adj(self):
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
    site: Site | None = None
