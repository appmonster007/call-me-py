"""Step 1: function identifiers, scopes, call sites, control flow.

Builds a `Program`: every function (def, async def, lambda, module body) gets a
`FuncId`, a lexical parent, a signature, its set of local names and a CFG.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .cfg import build_cfg
from .model import CFG, FuncId, Site

FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPE_NODES = FUNC_NODES + (ast.Lambda, ast.ClassDef)
MODULE_NAME = "<module>"


@dataclass(frozen=True)
class Sig:
    pos: Tuple[str, ...]
    kwonly: Tuple[str, ...]
    vararg: Optional[str]
    kwarg: Optional[str]
    defaults: Tuple[Tuple[str, ast.expr], ...]
    required: Tuple[str, ...]


@dataclass(eq=False)
class FuncInfo:
    id: FuncId
    kind: str  # "module" | "def" | "lambda"
    node: ast.AST
    parent: Optional[FuncId]  # lexical parent function (classes are skipped)
    sig: Sig
    locals: frozenset
    body: Tuple[ast.AST, ...]


@dataclass(eq=False)
class Program:
    file: str
    tree: ast.Module
    module: FuncId
    funcs: Dict[FuncId, FuncInfo]
    by_node: Dict[int, FuncId]
    cfgs: Dict[FuncId, CFG] = field(default_factory=dict)  # one per function

    def nesting_edges(self) -> List[Tuple[FuncId, FuncId]]:
        """The function-identifier graph: (lexical parent, child)."""
        return [(i.parent, i.id) for i in self.funcs.values() if i.parent is not None]


# ---------------------------------------------------------------- scopes
def outer_parts(n: ast.AST) -> List[ast.AST]:
    """Parts of a scope node that are evaluated in the *enclosing* scope."""
    if isinstance(n, ast.ClassDef):
        return [*n.decorator_list, *n.bases, *[k.value for k in n.keywords]]
    a = n.args  # FunctionDef / AsyncFunctionDef / Lambda
    deco = list(getattr(n, "decorator_list", []))
    return [*deco, *a.defaults, *[d for d in a.kw_defaults if d is not None]]


def walk_scope(nodes) -> Iterator[ast.AST]:
    """All nodes of one scope; nested scopes are yielded but not entered."""
    stack = list(reversed(list(nodes)))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, SCOPE_NODES):
            stack.extend(reversed(outer_parts(n)))
        else:
            stack.extend(reversed(list(ast.iter_child_nodes(n))))


def make_sig(args: ast.arguments) -> Sig:
    pos = [a.arg for a in args.posonlyargs + args.args]
    defaults: Dict[str, ast.expr] = {}
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


def pattern_names(n: ast.AST) -> Tuple[str, ...]:
    """Names captured by one `match` pattern node (MatchAs / MatchStar / MatchMapping)."""
    field = {"MatchAs": "name", "MatchStar": "name", "MatchMapping": "rest"}.get(type(n).__name__)
    name = getattr(n, field, None) if field else None
    return (name,) if name else ()


def scope_locals(body, params) -> frozenset:
    names = set(params)
    declared = set()
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


# ---------------------------------------------------------------- program
def build_program(source: str, filename: str = "<string>") -> Program:
    tree = ast.parse(source, filename=filename)
    funcs: Dict[FuncId, FuncInfo] = {}
    by_node: Dict[int, FuncId] = {}

    def add(node, qualname, parent, kind, body, args) -> FuncId:
        fid = FuncId(qualname, filename, getattr(node, "lineno", 1), getattr(node, "col_offset", 0))
        sig = make_sig(args) if args is not None else Sig((), (), None, None, (), ())
        params = [*sig.pos, *sig.kwonly, *([sig.vararg] if sig.vararg else []),
                  *([sig.kwarg] if sig.kwarg else [])]
        funcs[fid] = FuncInfo(fid, kind, node, parent, sig, scope_locals(body, params), tuple(body))
        by_node[id(node)] = fid
        return fid

    mod = FuncId(MODULE_NAME, filename, 1, 0)
    funcs[mod] = FuncInfo(mod, "module", tree, None, Sig((), (), None, None, (), ()),
                          scope_locals(tree.body, []), tuple(tree.body))
    by_node[id(tree)] = mod

    def walk(node: ast.AST, prefix: str, parent: FuncId) -> None:
        if isinstance(node, FUNC_NODES):
            fid = add(node, prefix + node.name, parent, "def",
                      node.body, node.args)
            for n in node.decorator_list:
                walk(n, prefix, parent)
            for n in outer_parts(node)[len(node.decorator_list):]:
                walk(n, funcs[fid].id.qualname + ".", parent)  # default lambdas: def.<lambda>
            for s in node.body:
                walk(s, funcs[fid].id.qualname + ".", fid)
        elif isinstance(node, ast.Lambda):
            fid = add(node, f"{prefix}<lambda>@{node.lineno}",
                      parent, "lambda", [node.body], node.args)
            for n in outer_parts(node):
                walk(n, prefix, parent)
            walk(node.body, prefix, fid)
        elif isinstance(node, ast.ClassDef):
            for n in outer_parts(node):
                walk(n, prefix, parent)
            for s in node.body:
                walk(s, prefix + node.name + ".", parent)
        else:
            for c in ast.iter_child_nodes(node):
                walk(c, prefix, parent)

    for s in tree.body:
        walk(s, "", mod)
    cfgs = {fid: build_cfg(info, make_site) for fid, info in funcs.items()}
    return Program(filename, tree, mod, funcs, by_node, cfgs)


def parse_file(path) -> Program:
    p = Path(path)
    return build_program(p.read_text(encoding="utf-8"), str(p))


# ---------------------------------------------------------------- call sites
def make_site(caller: FuncId, node: ast.AST, kind: str = "call") -> Site:
    text = ast.unparse(node)
    if kind == "decorator":
        text = "@" + text
    if len(text) > 60:
        text = text[:57] + "..."
    return Site(caller, node.lineno, node.col_offset, text, kind)


def call_sites(prog: Program, fid: FuncId) -> Tuple[Site, ...]:
    """Call sites (and decorator applications) executed by `fid`, including those
    in class bodies; taken from the CFG so both always agree."""
    return tuple(sorted(prog.cfgs[fid].calls, key=lambda s: (s.line, s.col, s.kind)))
