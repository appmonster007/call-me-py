"""Step 1: function identifiers, scopes, call sites.

Builds a `Program`: every function (def, async def, lambda, module body) gets a
`FuncId`, a lexical parent, a signature and its set of local names.
"""
from __future__ import annotations

import ast
import os
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from .model import ClassId, FuncId, Site

FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPE_NODES = FUNC_NODES + (ast.Lambda, ast.ClassDef)
MODULE_NAME = "<module>"

ScopeNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef


@dataclass(frozen=True)
class Sig:
    pos: tuple[str, ...]
    kwonly: tuple[str, ...]
    vararg: str | None
    kwarg: str | None
    defaults: tuple[tuple[str, ast.expr], ...]
    required: tuple[str, ...]


@dataclass(eq=False)
class FuncInfo:
    id: FuncId
    kind: str  # "module" | "def" | "lambda"
    node: ast.AST
    parent: FuncId | None  # lexical parent function (classes are skipped)
    sig: Sig
    locals: frozenset[str]
    body: tuple[ast.AST, ...]
    owner: ClassId | None = None  # lexically enclosing class, if defined in a class body


@dataclass(eq=False)
class ClassInfo:
    id: ClassId
    node: ast.ClassDef
    parent: FuncId  # lexical enclosing function (or module)
    body: tuple[ast.stmt, ...]


@dataclass(eq=False)
class Program:
    file: str
    tree: ast.Module
    module: FuncId
    funcs: dict[FuncId, FuncInfo]
    by_node: dict[int, FuncId]
    classes: dict[ClassId, ClassInfo]
    class_by_node: dict[int, ClassId]

    def nesting_edges(self) -> list[tuple[FuncId, FuncId]]:
        """The function-identifier graph: (lexical parent, child)."""
        return [(i.parent, i.id) for i in self.funcs.values() if i.parent is not None]


# ---------------------------------------------------------------- scopes
def outer_parts(n: ScopeNode) -> list[ast.expr]:
    """Parts of a scope node that are evaluated in the *enclosing* scope."""
    if isinstance(n, ast.ClassDef):
        return [*n.decorator_list, *n.bases, *[k.value for k in n.keywords]]
    a = n.args  # FunctionDef / AsyncFunctionDef / Lambda
    deco = n.decorator_list if not isinstance(n, ast.Lambda) else []
    return [*deco, *a.defaults, *[d for d in a.kw_defaults if d is not None]]


def walk_scope(nodes: Iterable[ast.AST]) -> Iterator[ast.AST]:
    """All nodes of one scope; nested scopes are yielded but not entered."""
    stack: list[ast.AST] = list(reversed(list(nodes)))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, SCOPE_NODES):
            stack.extend(reversed(outer_parts(n)))
        else:
            stack.extend(reversed(list(ast.iter_child_nodes(n))))


def make_sig(args: ast.arguments) -> Sig:
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


def scope_locals(body: Iterable[ast.AST], params: Iterable[str]) -> frozenset[str]:
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
    return frozenset(names - declared)


# ---------------------------------------------------------------- program
def build_program(source: str, filename: str = "<string>") -> Program:
    tree = ast.parse(source, filename=filename)
    funcs: dict[FuncId, FuncInfo] = {}
    by_node: dict[int, FuncId] = {}
    classes: dict[ClassId, ClassInfo] = {}
    class_by_node: dict[int, ClassId] = {}

    def add(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, qualname: str, parent: FuncId,
            kind: str, body: Sequence[ast.AST], args: ast.arguments,
            owner: ClassId | None = None) -> FuncId:
        fid = FuncId(qualname, filename, node.lineno, node.col_offset)
        sig = make_sig(args)
        params = [*sig.pos, *sig.kwonly, *([sig.vararg] if sig.vararg else []),
                  *([sig.kwarg] if sig.kwarg else [])]
        funcs[fid] = FuncInfo(fid, kind, node, parent, sig, scope_locals(body, params),
                              tuple(body), owner)
        by_node[id(node)] = fid
        return fid

    mod = FuncId(MODULE_NAME, filename, 1, 0)
    funcs[mod] = FuncInfo(mod, "module", tree, None, Sig((), (), None, None, (), ()),
                          scope_locals(tree.body, []), tuple(tree.body))
    by_node[id(tree)] = mod

    def walk(node: ast.AST, prefix: str, parent: FuncId, owner: ClassId | None = None) -> None:
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
            classes[cid] = ClassInfo(cid, node, parent, tuple(node.body))
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
    return Program(filename, tree, mod, funcs, by_node, classes, class_by_node)


def parse_file(path: str | os.PathLike[str]) -> Program:
    p = Path(path)
    return build_program(p.read_text(encoding="utf-8"), str(p))


# ---------------------------------------------------------------- call sites
def make_site(caller: FuncId, node: ast.expr, kind: str = "call") -> Site:
    text = ast.unparse(node)
    if kind == "decorator":
        text = "@" + text
    if len(text) > 60:
        text = text[:57] + "..."
    return Site(caller, node.lineno, node.col_offset, text, kind)


def call_sites(prog: Program, fid: FuncId) -> tuple[Site, ...]:
    """Syntactic call sites (and decorator applications) executed by `fid`."""
    out: set[Site] = set()
    work: list[Iterable[ast.AST]] = [prog.funcs[fid].body]
    while work:
        for n in walk_scope(work.pop()):
            if isinstance(n, ast.Call):
                out.add(make_site(fid, n, "call"))
            elif isinstance(n, FUNC_NODES):
                for d in n.decorator_list:
                    out.add(make_site(fid, d, "decorator"))
            elif isinstance(n, ast.ClassDef):
                for d in n.decorator_list:
                    out.add(make_site(fid, d, "decorator"))
                work.append(n.body)
    return tuple(sorted(out, key=lambda s: (s.line, s.col, s.kind)))
