"""Core data types shared by every stage of the analysis.

Vocabulary:
  FuncId   identity of one function (def, lambda or the module body)
  Closure  a *token*: the abstract value "this function, created in that context"
  Builtin  a *token* for a builtin / stdlib callable modelled by a stub (see stubs.py)
  Ctx      a *context*: the abstract entry state of a
           function (param -> set of closures) plus the captured defining node
  Node     (function, context): one analysed instance of a function
  Edge     call edge between two nodes at a syntactic call site
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import override


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

    @override
    def __str__(self) -> str:
        return self.uid


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


Token = Closure | Builtin


def token_label(tok: Token, style: str = "full") -> str:
    """Printable name of a token: the function id, or the builtin's name."""
    if isinstance(tok, Builtin):
        if tok.bound and tok.name.startswith("<partial"):
            inner = ",".join(sorted(token_label(t, style) for t in tok.bound[0]))
            return f"{tok.name[:-1]}:{inner}>"
        return tok.name
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
