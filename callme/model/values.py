"""Abstract values: the tokens, `Vals`, and the contexts and nodes that closures capture.

An abstract value `Vals` is a frozenset of tokens. A token's `depth` says how deeply other
tokens nest inside it; the analysis drops or widens tokens that nest too deeply, which keeps
every value finite. `Closure` points to the `Node` it was created in, and a `Node`'s `Ctx`
holds tokens, so the three live in one module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import override

from .ids import ClassId, FuncId, Site


@dataclass(frozen=True)
class Closure:
    """Token for the function `func`, created while the node `parent` was running.

    The parent decides which context the closure's free variables are read from. It is None when
    the defining context is unknown, or when keeping it would nest contexts deeper than
    `analysis.MAX_DEPTH`; free variables are then read from every context of the enclosing
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
    """Token for a builtin or stdlib callable, or a stub module, modelled in `callme.stubs`.

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
