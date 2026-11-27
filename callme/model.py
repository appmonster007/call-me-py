"""Core data types shared by every stage of the analysis.

Vocabulary:
  FuncId       identity of one function (def, lambda or the module body)
  ClassId      identity of one class definition
  Closure      a *token*: the abstract value "this function, created in that context"
  Builtin      a *token* for a builtin / stdlib callable modelled by a stub (see stubs.py)
  ClassObj     a *token* representing a class object
  Instance     a *token* representing an abstract instance of a class (per allocation site)
  Box          a *token* for a mutable container (list / dict / set), per allocation site
  Module       a *token* for a module object of the program, by dotted name
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

Tokens, `Ctx` and `Node` are frozen dataclasses that compute their hash once, in
`__post_init__`, and keep it in `_h`, because they are hashed in every set operation.
A token's `depth` says how deeply other tokens nest inside it; the analysis drops or
widens tokens that nest too deeply, which keeps every value finite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import override


def module_name(file: str) -> str:
    """Return the dotted module name for a file path taken as is, e.g. "pkg.util" for "pkg/util.py".

    A trailing `__init__` is dropped. If no name is left, the file path is returned as is.
    Programs loaded from disk name their modules relative to the import root instead.
    """
    parts = PurePath(file).with_suffix("").parts
    parts = tuple(p for p in parts if p not in ("/", ".", ".."))
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or file


@dataclass(frozen=True)
class _SymId:
    """Identity layout shared by functions and classes; the string forms are built on demand.

    file      source path as given, e.g. "pkg/util.py"
    qualname  dotted scope path of the definition, e.g. "Outer.make.inner"
    lineno    1-based line of the definition
    col       0-based column of the definition
    module    dotted name of the defining module, e.g. "pkg.util"; when left empty it is
              derived from `file` (see `module_name`). It is not part of the identity.
    """

    qualname: str
    file: str
    lineno: int
    col: int
    module: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        """Derive `module` from `file` when it was not given."""
        if not self.module:
            object.__setattr__(self, "module", module_name(self.file))

    @property
    def name(self) -> str:
        """Return the last part of the qualname, e.g. "inner" for "Outer.make.inner"."""
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def scope(self) -> str:
        """Return the qualname without its last part, e.g. "Outer.make", or "" at top level."""
        return self.qualname.rpartition(".")[0]

    @property
    def uid(self) -> str:
        """Return the "full" form `file:line:col:qualname`, unique even when names repeat."""
        return self.render("full")

    def render(self, style: str = "full") -> str:
        """Return this id as a string in the given style.

        style  "full" (file:line:col:qualname, always unique), "module" (module.qualname) or
               "short" (qualname alone, which can collide)

        Raises `ValueError` for any other style.
        """
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
    """Identity of one function: a def, async def, lambda or the module body (`<module>`)."""


@dataclass(frozen=True)
class ClassId(_SymId):
    """Identity of one class definition."""


@dataclass(frozen=True)
class Closure:
    """Token for the function `func`, created while the node `parent` was running.

    The parent decides which context the closure's free variables are read from. It is None when
    the defining context is unknown, or when keeping it would nest contexts deeper than
    `transfer.MAX_DEPTH`; free variables are then read from every context of the enclosing
    function. `depth` counts how many closures are nested through `parent`'s context.
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
    """Token for a builtin or stdlib callable, or a stub module, modelled by a stub in stubs.py.

    name      stub name, e.g. "map", "functools.partial", or "<partial>" for an internal token
    bound     argument values prepended to every call: the receiver of a builtin method such
              as `list.sort`, or the function and positional arguments of a partial
    bound_kw  keyword arguments bound the same way, as (name, values) pairs
    """

    name: str
    bound: tuple[frozenset[Token], ...] = ()
    bound_kw: tuple[tuple[str, frozenset[Token]], ...] = ()
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Set `depth` to one more than the deepest bound token, and cache the hash."""
        toks = [t for v in self.bound for t in v] + [t for _, v in self.bound_kw for t in v]
        if toks:
            object.__setattr__(self, "depth", 1 + max(t.depth for t in toks))
        object.__setattr__(self, "_h", hash((self.name, self.bound, self.bound_kw)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class ClassObj:
    """Token for the class object of `cls`."""

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
    """Allocation site: where in function `func` the expression that creates an object is."""

    func: FuncId
    line: int
    col: int

    @classmethod
    def of(cls, site: Site) -> Alloc:
        """Return the allocation site of an object created by the call at `site`."""
        return cls(site.caller, site.line, site.col)


@dataclass(frozen=True)
class Instance:
    """Token for the instances of `cls` created at `alloc`, or at an unknown site when it is None.

    Instance attributes are stored per token in `State.inst_attrs`, so objects of the same class
    created at different sites keep separate fields.
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
    """Token for the callable `func` with `receiver` bound as its first argument."""

    func: Token
    receiver: ClassObj | Instance
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Set `depth` to one more than the deeper of `func` and `receiver`, and cache the hash."""
        object.__setattr__(self, "depth", 1 + max(self.func.depth, self.receiver.depth))
        object.__setattr__(self, "_h", hash(("bound", self.func, self.receiver)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class ClassMethod:
    """Token for `classmethod(func)`, a descriptor that binds the owner class as first argument."""

    func: Token
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Set `depth` to one more than the depth of `func`, and cache the hash."""
        object.__setattr__(self, "depth", 1 + self.func.depth)
        object.__setattr__(self, "_h", hash(("classmethod", self.func)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class StaticMethod:
    """Token for `staticmethod(func)`: a descriptor that returns `func` without binding it."""

    func: Token
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Set `depth` to one more than the depth of `func`, and cache the hash."""
        object.__setattr__(self, "depth", 1 + self.func.depth)
        object.__setattr__(self, "_h", hash(("staticmethod", self.func)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Super:
    """Token for `super(start, receiver)`: attribute lookup in `receiver`'s MRO after `start`."""

    start: ClassId
    receiver: ClassObj | Instance
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Set `depth` to one more than the depth of `receiver`, and cache the hash."""
        object.__setattr__(self, "depth", 1 + self.receiver.depth)
        object.__setattr__(self, "_h", hash(("super", self.start, self.receiver)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Property:
    """Token for `property(fget, fset, fdel)`; each accessor is the set of functions it may be."""

    fget: frozenset[Token] = frozenset()
    fset: frozenset[Token] = frozenset()
    fdel: frozenset[Token] = frozenset()
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Set `depth` to one more than the deepest accessor token, and cache the hash."""
        toks = [*self.fget, *self.fset, *self.fdel]
        if toks:
            object.__setattr__(self, "depth", 1 + max(t.depth for t in toks))
        object.__setattr__(self, "_h", hash(("property", self.fget, self.fset, self.fdel)))

    @override
    def __hash__(self) -> int:
        return self._h


@dataclass(frozen=True)
class Box:
    """Token for the mutable container of `kind` "list", "dict" or "set" created at `alloc`.

    Values stay collapsed: a container literal evaluates to its elements plus its Box. Values
    stored into the container later, as in `xs.append(f)` or `d[k] = f`, are recorded under the
    box in `State.heap`, so every alias of the container sees them through `State.deref`.
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


@dataclass(frozen=True)
class Module:
    """Token for the module object of the program's module `name`, e.g. "pkg.util".

    Its attributes are the module's globals and its submodules (`Interp.module_attr`). Modules
    outside the program are not `Module`s: stub modules are `Builtin`s, and others are unknown.
    """

    name: str
    depth: int = field(default=0, compare=False)
    _h: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_h", hash(("module", self.name)))

    @override
    def __hash__(self) -> int:
        return self._h


Token = (Closure | Builtin | ClassObj | Instance | BoundMethod
         | ClassMethod | StaticMethod | Super | Property | Box | Module)


def token_label(tok: Token, style: str = "full") -> str:
    """Return the printable name of a token, with ids rendered in `style`.

    Closures print as their function id, classes as `<class C>`, instances as `C()@line:col`,
    boxes as `<list@line:col>`, modules as `<module pkg.util>` and partials as `<partial:f>`;
    the other tokens print as calls, e.g. `classmethod(f)` or `super(C, C()@3:0)`.
    """
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
    if isinstance(tok, Module):
        return f"<module {tok.name}>"
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
    """Context of a node: the abstract entry state a function is analysed under.

    params  (parameter, tokens) pairs for the parameters that hold tokens, sorted by name;
            parameters with no tokens are left out, so they do not split contexts
    parent  the called closure's parent: the node that was running when it was created,
            or None when that is unknown
    merged  True for the one widened context of a function that ran out of contexts; its
            entry state is kept in `State.entry` instead of `params`
    """

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
    """One analysed instance of a function: the function `func` under the context `ctx`."""

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
    """A call site, identified by its caller and source position.

    text  source text of the call, at most 60 characters; decorators start with "@"
    kind  "call" or "decorator" for syntactic sites, which are CFG call points; "op", "attr",
          "iter", "with" or "class" for the implicit dunder calls behind operators, attribute
          access, iteration, `with` and class creation, which have no CFG point
    """

    caller: FuncId
    line: int
    col: int
    text: str
    kind: str = "call"  # "call" | "decorator"


@dataclass(frozen=True)
class Edge:
    """A call edge: the node `caller` may call the node `callee` at `site`."""

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
