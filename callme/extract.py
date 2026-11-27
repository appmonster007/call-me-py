"""Step 1: function identifiers, scopes, call sites, control flow.

Builds a `Program`: every function (def, async def, lambda, module body) gets a
`FuncId`, a lexical parent, a signature, its set of local names and a CFG.
"""
from __future__ import annotations

import ast
import hashlib
import os
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .cfg import build_cfg
from .model import CFG, ClassId, FuncId, Site

FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPE_NODES = FUNC_NODES + (ast.Lambda, ast.ClassDef)
MODULE_NAME = "<module>"

ScopeNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef


@dataclass(frozen=True)
class Sig:
    """Signature of a function, as needed to bind call arguments to parameters.

    pos       positional parameters, positional-only ones included, in order
    kwonly    keyword-only parameters
    vararg    name of the `*args` parameter, or None
    kwarg     name of the `**kwargs` parameter, or None
    defaults  (parameter, default expression) pairs; the expressions are evaluated where the
              function is defined
    required  parameters without a default
    """

    pos: tuple[str, ...]
    kwonly: tuple[str, ...]
    vararg: str | None
    kwarg: str | None
    defaults: tuple[tuple[str, ast.expr], ...]
    required: tuple[str, ...]


@dataclass(eq=False)
class FuncInfo:
    """Everything the analysis knows statically about one function.

    kind       "module", "def" or "lambda"
    node       the AST node: the module, a def or a lambda
    parent     lexically enclosing function, or None for the module body; classes in between
               are skipped, as in Python's own scoping
    locals     names bound in the function's own scope, parameters included and names
               declared `global` or `nonlocal` excluded
    body       statements to run; a lambda's body is its single expression
    owner      class whose body defines the function directly, or None
    globals    names declared `global`, which are written in the module
    nonlocals  names declared `nonlocal`, which are written in an enclosing function
    """

    id: FuncId
    kind: str  # "module" | "def" | "lambda"
    node: ast.AST
    parent: FuncId | None  # lexical parent function (classes are skipped)
    sig: Sig
    locals: frozenset[str]
    body: tuple[ast.AST, ...]
    owner: ClassId | None = None  # lexically enclosing class, if defined in a class body
    globals: frozenset[str] = frozenset()  # names declared `global` (written in the module)
    nonlocals: frozenset[str] = frozenset()  # names declared `nonlocal` (written in an outer def)


@dataclass(eq=False)
class ClassInfo:
    """Everything the analysis knows statically about one class definition.

    parent       function (or module body) whose code runs the class statement
    body         statements of the class body
    known_attrs  names annotated in the class body or assigned as `self.<name>` in its methods
    """

    id: ClassId
    node: ast.ClassDef
    parent: FuncId  # lexical enclosing function (or module)
    body: tuple[ast.stmt, ...]
    known_attrs: frozenset[str] = frozenset()  # statically declared / self-assigned attrs


@dataclass(eq=False)
class Program:
    """A parsed source file: its functions, classes and CFGs, keyed by id.

    module         id of the module body, the root of the analysis
    by_node        function id of each def, lambda and module node, keyed by `id(node)`
    class_by_node  class id of each class node, keyed by `id(node)`
    cfgs           one CFG per function
    fingerprint    hash of the file name and source; saved states are only valid for the
                   program with the same fingerprint
    """

    file: str
    tree: ast.Module
    module: FuncId
    funcs: dict[FuncId, FuncInfo]
    by_node: dict[int, FuncId]
    classes: dict[ClassId, ClassInfo]
    class_by_node: dict[int, ClassId]
    cfgs: dict[FuncId, CFG] = field(default_factory=dict)  # one per function
    fingerprint: str = ""  # hash of file name + source; identifies this program version

    def nesting_edges(self) -> list[tuple[FuncId, FuncId]]:
        """Return the function-identifier graph as (lexical parent, child) pairs."""
        return [(i.parent, i.id) for i in self.funcs.values() if i.parent is not None]


# ---------------------------------------------------------------- scopes
def outer_parts(n: ScopeNode) -> list[ast.expr]:
    """Return the parts of a def, lambda or class that run in the enclosing scope.

    These are the decorators, default values, base classes and class keywords: they are
    evaluated where the definition is, not inside it.
    """
    if isinstance(n, ast.ClassDef):
        return [*n.decorator_list, *n.bases, *[k.value for k in n.keywords]]
    a = n.args  # FunctionDef / AsyncFunctionDef / Lambda
    deco = n.decorator_list if not isinstance(n, ast.Lambda) else []
    return [*deco, *a.defaults, *[d for d in a.kw_defaults if d is not None]]


def walk_scope(nodes: Iterable[ast.AST]) -> Iterator[ast.AST]:
    """Yield every AST node that belongs to one scope, in source order.

    Nested defs, lambdas and classes are yielded, together with their parts that run in this
    scope (`outer_parts`), but their bodies are not entered.
    """
    stack: list[ast.AST] = list(reversed(list(nodes)))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, SCOPE_NODES):
            stack.extend(reversed(outer_parts(n)))
        else:
            stack.extend(reversed(list(ast.iter_child_nodes(n))))


def make_sig(args: ast.arguments) -> Sig:
    """Return the `Sig` of a function's argument list."""
    pos = [a.arg for a in args.posonlyargs + args.args]
    defaults: dict[str, ast.expr] = {}
    for name, d in zip(pos[len(pos) - len(args.defaults):], args.defaults):
        defaults[name] = d
    kwonly = [a.arg for a in args.kwonlyargs]
    for name, d in zip(kwonly, args.kw_defaults):
        if d is not None:
            defaults[name] = d
    required = tuple(n for n in pos + kwonly if n not in defaults)
    return Sig(
        tuple(pos), tuple(kwonly),
        args.vararg.arg if args.vararg else None,
        args.kwarg.arg if args.kwarg else None,
        tuple(defaults.items()), required,
    )


def pattern_names(n: ast.AST) -> tuple[str, ...]:
    """Return the names that one `match` pattern node binds itself (not its sub-patterns)."""
    if isinstance(n, (ast.MatchAs, ast.MatchStar)):
        return (n.name,) if n.name else ()
    if isinstance(n, ast.MatchMapping):
        return (n.rest,) if n.rest else ()
    return ()


def scope_locals(body: Iterable[ast.AST], params: Iterable[str]) -> frozenset[str]:
    """Return the names local to one scope: its parameters and every name the scope binds.

    Assignments, deletions, defs, classes, imports, `except ... as` and `match` captures
    count; names declared `global` or `nonlocal` are removed.
    """
    names = set(params)
    declared: set[str] = set()
    for n in walk_scope(body):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, FUNC_NODES + (ast.ClassDef,)):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if a.name != "*":
                    names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            declared.update(n.names)
        names.update(pattern_names(n))
    return frozenset(names - declared)


def scope_decls(body: Iterable[ast.AST]) -> tuple[frozenset[str], frozenset[str]]:
    """Return the names declared `global` and those declared `nonlocal` in one scope."""
    glob: set[str] = set()
    nonloc: set[str] = set()
    for n in walk_scope(body):
        if isinstance(n, ast.Global):
            glob.update(n.names)
        elif isinstance(n, ast.Nonlocal):
            nonloc.update(n.names)
    return frozenset(glob), frozenset(nonloc)


def class_known_attrs(body: Sequence[ast.stmt]) -> frozenset[str]:
    """Return the names annotated in a class body or assigned as `self.<name>` in its methods."""
    names: set[str] = set()
    for n in walk_scope(body):
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, FUNC_NODES):
            pos = [a.arg for a in n.args.posonlyargs + n.args.args]
            if not pos:
                continue
            self_name = pos[0]
            for inner in walk_scope(n.body):
                if (isinstance(inner, ast.Attribute) and isinstance(inner.ctx, ast.Store)
                        and isinstance(inner.value, ast.Name) and inner.value.id == self_name):
                    names.add(inner.attr)
    return frozenset(names)


# ---------------------------------------------------------------- program
def build_program(source: str, filename: str = "<string>") -> Program:
    """Parse `source` and return its `Program`, with ids, scopes, signatures and CFGs.

    Every def, async def and lambda gets a `FuncInfo`, and every class a `ClassInfo`. A
    qualname is the dotted path of enclosing defs and classes. A lambda is named
    `<lambda>@<line>`, and a lambda in a def's default values is put under that def.
    `filename` is recorded in every id and in the fingerprint. Raises `SyntaxError` for
    invalid source.
    """
    tree = ast.parse(source, filename=filename)
    funcs: dict[FuncId, FuncInfo] = {}
    by_node: dict[int, FuncId] = {}
    classes: dict[ClassId, ClassInfo] = {}
    class_by_node: dict[int, ClassId] = {}

    def add(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, qualname: str, parent: FuncId,
            kind: str, body: Sequence[ast.AST], args: ast.arguments,
            owner: ClassId | None = None) -> FuncId:
        """Register a def or lambda under `qualname` and return its new id."""
        fid = FuncId(qualname, filename, node.lineno, node.col_offset)
        sig = make_sig(args)
        params = [*sig.pos, *sig.kwonly, *([sig.vararg] if sig.vararg else []),
                  *([sig.kwarg] if sig.kwarg else [])]
        glob, nonloc = scope_decls(body)
        funcs[fid] = FuncInfo(fid, kind, node, parent, sig, scope_locals(body, params),
                              tuple(body), owner, glob, nonloc)
        by_node[id(node)] = fid
        return fid

    mod = FuncId(MODULE_NAME, filename, 1, 0)
    funcs[mod] = FuncInfo(mod, "module", tree, None, Sig((), (), None, None, (), ()),
                          scope_locals(tree.body, []), tuple(tree.body))
    by_node[id(tree)] = mod

    def walk(node: ast.AST, prefix: str, parent: FuncId, owner: ClassId | None = None) -> None:
        """Register every function and class found under `node`.

        prefix  qualname prefix of the scope being walked, e.g. "Outer.make."
        parent  function whose code runs `node`
        owner   class whose body `node` is directly in, if any
        """
        if isinstance(node, FUNC_NODES):
            fid = add(node, prefix + node.name, parent, "def",
                      node.body, node.args, owner)
            for n in node.decorator_list:
                walk(n, prefix, parent, None)
            for n in outer_parts(node)[len(node.decorator_list):]:
                walk(n, funcs[fid].id.qualname + ".", parent, None)  # default lambdas: def.<lambda>
            for s in node.body:
                walk(s, funcs[fid].id.qualname + ".", fid, None)
        elif isinstance(node, ast.Lambda):
            fid = add(node, f"{prefix}<lambda>@{node.lineno}",
                      parent, "lambda", [node.body], node.args)
            for n in outer_parts(node):
                walk(n, prefix, parent, None)
            walk(node.body, prefix, fid, None)
        elif isinstance(node, ast.ClassDef):
            cid = ClassId(prefix + node.name, filename, node.lineno, node.col_offset)
            classes[cid] = ClassInfo(cid, node, parent, tuple(node.body),
                                     class_known_attrs(node.body))
            class_by_node[id(node)] = cid
            for n in outer_parts(node):
                walk(n, prefix, parent, None)
            for s in node.body:
                walk(s, prefix + node.name + ".", parent, cid)
        else:
            for c in ast.iter_child_nodes(node):
                walk(c, prefix, parent, owner)

    for s in tree.body:
        walk(s, "", mod)
    # `global x` makes x a module variable even if the module itself never assigns it
    funcs[mod].locals |= frozenset(n for f in funcs.values() for n in f.globals)
    cfgs = {fid: build_cfg(fid, info.node, info.body, make_site) for fid, info in funcs.items()}
    return Program(filename, tree, mod, funcs, by_node, classes, class_by_node,
                   cfgs, fingerprint(source, filename))


def fingerprint(source: str, filename: str) -> str:
    """Return a hash that is the same for two parses exactly when file name and source match."""
    return hashlib.sha256(f"{filename}\0{source}".encode()).hexdigest()


def parse_file(path: str | os.PathLike[str]) -> Program:
    """Read the UTF-8 Python file at `path` and return its `Program`, named `str(path)`."""
    p = Path(path)
    return build_program(p.read_text(encoding="utf-8"), str(p))


# ---------------------------------------------------------------- call sites
def make_site(caller: FuncId, node: ast.expr, kind: str = "call") -> Site:
    """Return the `Site` of a call node, or of a decorator when `kind` is "decorator".

    The text is the call's source, prefixed with "@" for a decorator and cut to 60 characters.
    The CFG builder and the interpreter both build their sites with this function, so the two
    always agree.
    """
    text = ast.unparse(node)
    if kind == "decorator":
        text = "@" + text
    if len(text) > 60:
        text = text[:57] + "..."
    return Site(caller, node.lineno, node.col_offset, text, kind)


def call_sites(prog: Program, fid: FuncId) -> tuple[Site, ...]:
    """Return the call sites that function `fid` executes, sorted by position.

    Decorator applications and calls in class bodies the function runs are included. They
    are read from the function's CFG, so both always agree.
    """
    return tuple(sorted(prog.cfgs[fid].calls, key=lambda s: (s.line, s.col, s.kind)))
