"""Step 1: function identifiers, scopes, call sites, control flow.

Builds a `Program`: every function (def, async def, lambda, module body) gets a
`FuncId`, a lexical parent, a signature, its set of local names and a CFG.
`parse_files` also follows imports: starting from the entry files, it loads every
project module they import, transitively, and resolves module names the way
Python's path finder does.
"""
from __future__ import annotations

import ast
import hashlib
import os
import sys
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import final

from .cfg import build_cfg
from .model import CFG, ClassId, FuncId, Site, module_name

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
    must_writes  subset of `globals | nonlocals` assigned on every normal return path
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
    must_writes: frozenset[str] = frozenset()  # globals / nonlocals assigned on every normal return


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
class ModuleInfo:
    """One module of the program: a source file, or a namespace package that has none.

    name     dotted module name, e.g. "pkg.util"
    file     source path, or None for a namespace package (a directory without `__init__.py`)
    func     id of the module body, or None for a namespace package
    package  True for a package; relative imports in its `__init__` resolve against itself
    exports  names that `from <name> import *` binds: the literal `__all__` when there is
             one, otherwise every module-level name that does not start with "_"
    """

    name: str
    file: str | None
    func: FuncId | None
    package: bool
    exports: frozenset[str] = frozenset()


@dataclass(eq=False)
class Program:
    """A parsed program: the functions, classes and CFGs of all its modules, keyed by id.

    file           path of the first entry file
    tree           AST of the first entry file
    module         id of the first entry's module body
    by_node        function id of each def, lambda and module node, keyed by `id(node)`
    class_by_node  class id of each class node, keyed by `id(node)`
    cfgs           one CFG per function
    fingerprint    hash of every file name and source; saved states are only valid for the
                   program with the same fingerprint
    modules        every module of the program by dotted name, namespace packages included
    entries        module bodies of the entry files, the roots of the analysis
    """

    file: str
    tree: ast.Module
    module: FuncId
    funcs: dict[FuncId, FuncInfo]
    by_node: dict[int, FuncId]
    classes: dict[ClassId, ClassInfo]
    class_by_node: dict[int, ClassId]
    cfgs: dict[FuncId, CFG] = field(default_factory=dict)  # one per function
    fingerprint: str = ""  # hash of file names + sources; identifies this program version
    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    entries: tuple[FuncId, ...] = ()

    def nesting_edges(self) -> list[tuple[FuncId, FuncId]]:
        """Return the function-identifier graph as (lexical parent, child) pairs."""
        return [(i.parent, i.id) for i in self.funcs.values() if i.parent is not None]

    def module_of(self, fid: FuncId | ClassId) -> ModuleInfo:
        """Return the module that defines function or class `fid`."""
        return self.modules[fid.module]

    def module_body(self, fid: FuncId | ClassId) -> FuncId:
        """Return the id of the module body that defines function or class `fid`."""
        body = self.module_of(fid).func
        assert body is not None, "a function is always defined in a module with a body"
        return body


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


def _target_names(t: ast.expr, tracked: frozenset[str]) -> set[str]:
    """Return names in `tracked` directly bound by assignment target `t`."""
    if isinstance(t, ast.Name):
        return {t.id} if t.id in tracked else set()
    if isinstance(t, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for elt in t.elts:
            out.update(_target_names(elt, tracked))
        return out
    if isinstance(t, ast.Starred):
        return _target_names(t.value, tracked)
    return set()


def _stmt_writes(s: ast.stmt, tracked: frozenset[str]) -> set[str]:
    """Return names in `tracked` unconditionally assigned by simple statement `s`."""
    if isinstance(s, ast.Assign):
        out: set[str] = set()
        for t in s.targets:
            out.update(_target_names(t, tracked))
        return out
    if isinstance(s, ast.AnnAssign):
        return _target_names(s.target, tracked) if s.value is not None else set()
    if isinstance(s, FUNC_NODES + (ast.ClassDef,)):
        return {s.name} if s.name in tracked else set()
    if isinstance(s, (ast.Import, ast.ImportFrom)):
        return {n for a in s.names if a.name != "*"
                and (n := a.asname or a.name.split(".")[0]) in tracked}
    return set()


def _all_deletes(stmts: Sequence[ast.stmt], tracked: frozenset[str]) -> frozenset[str]:
    """Return every name in `tracked` deleted anywhere in `stmts` (within the same scope)."""
    return frozenset(
        t.id for n in walk_scope(stmts) if isinstance(n, ast.Delete)
        for t in n.targets if isinstance(t, ast.Name) and t.id in tracked
    )


def _is_irrefutable(pat: ast.pattern) -> bool:
    """Return True if match pattern `pat` always matches any subject."""
    if isinstance(pat, ast.MatchAs):
        return pat.pattern is None
    if isinstance(pat, ast.MatchOr):
        return any(_is_irrefutable(p) for p in pat.patterns)
    return False


def scope_must_writes(node: ast.AST, body: Sequence[ast.AST],
                      tracked: frozenset[str]) -> frozenset[str]:
    """Return names in `tracked` that `node` definitely assigns on every normal-return path.

    Async functions and generators return empty, because calling them does not execute their
    body immediately. Loops, `with` blocks and `try` handlers are treated conservatively so
    the result is always a sound under-approximation of guaranteed writes.
    """
    if not tracked or not isinstance(node, ast.FunctionDef):
        return frozenset()
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in walk_scope(body)):
        return frozenset()
    stmts = [s for s in body if isinstance(s, ast.stmt)]
    returns: list[frozenset[str]] = []
    loop_breaks: list[list[frozenset[str]]] = []

    def intersect(sets: Sequence[frozenset[str]]) -> frozenset[str]:
        """Return the intersection of non-empty sequence `sets`."""
        return sets[0].intersection(*sets[1:])

    def walk(seq: Sequence[ast.stmt], cur: frozenset[str]) -> tuple[frozenset[str], bool]:
        """Return (must-written set at fallthrough, whether fallthrough is reachable)."""
        for s in seq:
            if isinstance(s, ast.Return):
                returns.append(cur)
                return cur, False
            if isinstance(s, ast.Raise):
                return cur, False
            if isinstance(s, ast.Break):
                if loop_breaks:
                    loop_breaks[-1].append(cur)
                return cur, False
            if isinstance(s, ast.Continue):
                return cur, False
            if isinstance(s, ast.If):
                m1, l1 = walk(s.body, cur)
                m2, l2 = walk(s.orelse, cur)
                live = [m for m, ok in ((m1, l1), (m2, l2)) if ok]
                if not live:
                    return cur, False
                cur = intersect(live)
            elif isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
                head = cur - _all_deletes(s.body, tracked)
                loop_breaks.append([])
                walk(s.body, head)
                breaks = loop_breaks.pop()
                m_else, l_else = walk(s.orelse, head)
                exits = ([m_else] if l_else else []) + breaks
                if not exits:
                    return cur, False
                cur = intersect(exits)
            elif isinstance(s, (ast.Try, ast.TryStar)):
                ret_mark = len(returns)
                body_dels = _all_deletes(s.body, tracked)
                h_start = cur - body_dels
                m_body, l_body = walk(s.body, cur)
                if l_body:
                    m_else, l_else = walk(s.orelse, m_body)
                else:
                    m_else, l_else = m_body, False
                ends: list[tuple[frozenset[str], bool]] = [(m_else, l_else)]
                for h in s.handlers:
                    ends.append(walk(h.body, h_start))
                live = [m for m, ok in ends if ok]
                if s.finalbody:
                    fin_dels = _all_deletes(s.finalbody, tracked)
                    if fin_dels:
                        for i in range(ret_mark, len(returns)):
                            returns[i] = returns[i] - fin_dels
                    if live:
                        cur, ok = walk(s.finalbody, intersect(live))
                        if not ok:
                            return cur, False
                    else:
                        walk(s.finalbody, h_start)
                        return cur, False
                else:
                    if not live:
                        return cur, False
                    cur = intersect(live)
            elif isinstance(s, (ast.With, ast.AsyncWith)):
                base = cur - _all_deletes(s.body, tracked)
                m_body, l_body = walk(s.body, cur)
                cur = (base & m_body) if l_body else base
            elif isinstance(s, ast.Match):
                ends = [walk(c.body, cur) for c in s.cases]
                exhaustive = any(c.guard is None and _is_irrefutable(c.pattern) for c in s.cases)
                if not exhaustive:
                    ends.append((cur, True))
                live = [m for m, ok in ends if ok]
                if not live:
                    return cur, False
                cur = intersect(live)
            elif isinstance(s, ast.Delete):
                dels = {t.id for t in s.targets if isinstance(t, ast.Name) and t.id in tracked}
                if dels:
                    cur = cur - dels
            else:
                writes = _stmt_writes(s, tracked)
                if writes:
                    cur = cur | writes
        return cur, True

    end_must, falls_through = walk(stmts, frozenset())
    exits = [*returns, *([end_must] if falls_through else [])]
    return intersect(exits) if exits else frozenset()


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
def module_all(body: Sequence[ast.stmt]) -> frozenset[str] | None:
    """Return the names a module lists in `__all__`, or None when it has no literal `__all__`.

    Module-level `__all__ = [...]`, `__all__ += [...]`, `__all__.extend([...])` and
    `__all__.append("x")` count, with string constants only. Any other write to `__all__`
    makes it unknown, and None is returned.
    """
    names: set[str] = set()
    seen = False

    def strings(e: ast.expr) -> list[str] | None:
        """Return the strings of a literal list or tuple (or one string), or None."""
        elts = e.elts if isinstance(e, (ast.List, ast.Tuple)) else [e]
        out = [x.value for x in elts if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        return out if len(out) == len(elts) else None

    for n in walk_scope(body):
        value: ast.expr | None = None
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__"
                                             for t in n.targets):
            value = n.value
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)) and isinstance(n.target, ast.Name) \
                and n.target.id == "__all__":
            value = n.value
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name) and n.func.value.id == "__all__"
              and n.func.attr in ("extend", "append") and len(n.args) == 1):
            value = n.args[0]
        else:
            continue
        found = strings(value) if value is not None else None
        if found is None:
            return None
        seen = True
        names.update(found)
    return frozenset(names) if seen else None


def absolute_module(current: ModuleInfo, level: int, module: str | None) -> str | None:
    """Return the absolute name of the module that an import in `current` names.

    level   number of leading dots; 0 is an absolute import, which returns `module` itself
    module  the dotted name after the dots, or None for `from . import x`

    A relative import counts from `current`'s package (`current` itself for a package).
    Return None when it climbs above the top-level package, where Python raises
    `ImportError`.
    """
    if level == 0:
        return module
    pkg = current.name if current.package else current.name.rpartition(".")[0]
    parts = pkg.split(".") if pkg else []
    if level - 1 >= len(parts):
        return None
    base = ".".join(parts[:len(parts) - (level - 1)])
    return f"{base}.{module}" if module else base


def fingerprint(files: Mapping[str, str]) -> str:
    """Return a hash that is the same for two programs exactly when file names and sources match.

    files  source text by file name; a single file hashes as `f"{file}\\0{source}"` always did
    """
    text = "\0".join(f"{name}\0{src}" for name, src in sorted(files.items()))
    return hashlib.sha256(text.encode()).hexdigest()


class _Builder:
    """Collects the functions, classes and modules of a program while its files are parsed."""

    def __init__(self) -> None:
        """Start with no modules."""
        self.funcs: dict[FuncId, FuncInfo] = {}
        self.by_node: dict[int, FuncId] = {}
        self.classes: dict[ClassId, ClassInfo] = {}
        self.class_by_node: dict[int, ClassId] = {}
        self.modules: dict[str, ModuleInfo] = {}
        self.sources: dict[str, str] = {}  # file name -> source

    def tree(self, info: ModuleInfo) -> ast.Module | None:
        """Return the AST of module `info`, or None for a namespace package."""
        if info.func is None:
            return None
        node = self.funcs[info.func].node
        assert isinstance(node, ast.Module)
        return node

    def add_namespace(self, name: str) -> ModuleInfo:
        """Register namespace package `name`, which has no source of its own, and return it."""
        info = ModuleInfo(name, None, None, True)
        self.modules[name] = info
        return info

    def add_module(self, source: str, filename: str, name: str, package: bool) -> ModuleInfo:
        """Parse `source` as module `name` and register its functions and classes.

        Every def, async def and lambda gets a `FuncInfo`, and every class a `ClassInfo`. A
        qualname is the dotted path of enclosing defs and classes. A lambda is named
        `<lambda>@<line>`, and a lambda in a def's default values is put under that def.
        `filename` and `name` are recorded in every id. Raises `SyntaxError` for invalid
        source.
        """
        tree = ast.parse(source, filename=filename)
        funcs, by_node = self.funcs, self.by_node
        classes, class_by_node = self.classes, self.class_by_node

        def add(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, qualname: str,
                parent: FuncId, kind: str, body: Sequence[ast.AST], args: ast.arguments,
                owner: ClassId | None = None) -> FuncId:
            """Register a def or lambda under `qualname` and return its new id."""
            fid = FuncId(qualname, filename, node.lineno, node.col_offset, name)
            sig = make_sig(args)
            params = [*sig.pos, *sig.kwonly, *([sig.vararg] if sig.vararg else []),
                      *([sig.kwarg] if sig.kwarg else [])]
            glob, nonloc = scope_decls(body)
            must = scope_must_writes(node, body, glob | nonloc)
            funcs[fid] = FuncInfo(fid, kind, node, parent, sig, scope_locals(body, params),
                                  tuple(body), owner, glob, nonloc, must)
            by_node[id(node)] = fid
            return fid

        mod = FuncId(MODULE_NAME, filename, 1, 0, name)
        funcs[mod] = FuncInfo(mod, "module", tree, None, Sig((), (), None, None, (), ()),
                              scope_locals(tree.body, []), tuple(tree.body))
        by_node[id(tree)] = mod
        own: list[FuncInfo] = [funcs[mod]]

        def walk(node: ast.AST, prefix: str, parent: FuncId, owner: ClassId | None = None) -> None:
            """Register every function and class found under `node`.

            prefix  qualname prefix of the scope being walked, e.g. "Outer.make."
            parent  function whose code runs `node`
            owner   class whose body `node` is directly in, if any
            """
            if isinstance(node, FUNC_NODES):
                fid = add(node, prefix + node.name, parent, "def",
                          node.body, node.args, owner)
                own.append(funcs[fid])
                for n in node.decorator_list:
                    walk(n, prefix, parent, None)
                for n in outer_parts(node)[len(node.decorator_list):]:
                    # lambdas in default values are named def.<lambda>
                    walk(n, funcs[fid].id.qualname + ".", parent, None)
                for s in node.body:
                    walk(s, funcs[fid].id.qualname + ".", fid, None)
            elif isinstance(node, ast.Lambda):
                fid = add(node, f"{prefix}<lambda>@{node.lineno}",
                          parent, "lambda", [node.body], node.args)
                own.append(funcs[fid])
                for n in outer_parts(node):
                    walk(n, prefix, parent, None)
                walk(node.body, prefix, fid, None)
            elif isinstance(node, ast.ClassDef):
                cid = ClassId(prefix + node.name, filename, node.lineno, node.col_offset, name)
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
        funcs[mod].locals |= frozenset(n for f in own for n in f.globals)
        info = ModuleInfo(name, filename, mod, package)
        self.modules[name] = info
        self.sources[filename] = source
        return info

    def link_star_imports(self) -> None:
        """Bind the names of every `from m import *` of a program module, and set `exports`.

        A star import makes the exported names module-level locals of the importer, so that
        `bind` and `lookup` see them. Star imports of star imports are followed until nothing
        is added. Names from modules outside the program stay unknown.
        """
        bodies: dict[str, FuncInfo] = {}  # module name -> module body
        literal: dict[str, frozenset[str] | None] = {}
        stars: dict[str, list[str]] = {}  # module name -> modules it star-imports
        for name, info in self.modules.items():
            tree = self.tree(info)
            if tree is None or info.func is None:
                continue
            bodies[name] = self.funcs[info.func]
            literal[name] = module_all(tree.body)
            stars[name] = []
            for n in walk_scope(tree.body):
                if isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names):
                    base = absolute_module(info, n.level, n.module)
                    if base is not None and base in self.modules:
                        stars[name].append(base)

        def exports(name: str) -> frozenset[str]:
            """Return what `from <name> import *` binds, as far as is known now."""
            if name not in bodies:  # a namespace package
                return frozenset()
            lit = literal[name]
            if lit is not None:
                return lit
            return frozenset(n for n in bodies[name].locals if not n.startswith("_"))

        changed = True
        while changed:
            changed = False
            for name, bases in stars.items():
                body = bodies[name]
                new = frozenset[str]().union(*(exports(b) for b in bases)) - body.locals
                if new:
                    body.locals |= new
                    changed = True
        for name, info in self.modules.items():
            info.exports = exports(name)

    def finish(self, entries: Sequence[ModuleInfo]) -> Program:
        """Link star imports, build every CFG, and return the `Program` rooted at `entries`."""
        self.link_star_imports()
        cfgs = {fid: build_cfg(fid, info.node, info.body, make_site)
                for fid, info in self.funcs.items()}
        first = entries[0]
        assert first.func is not None and first.file is not None
        tree = self.tree(first)
        assert tree is not None
        return Program(first.file, tree, first.func, self.funcs, self.by_node, self.classes,
                       self.class_by_node, cfgs, fingerprint(self.sources), self.modules,
                       tuple(e.func for e in entries if e.func is not None))


def build_program(source: str, filename: str = "<string>") -> Program:
    """Parse `source` and return its `Program`, with ids, scopes, signatures and CFGs.

    The program has one module, named after `filename` (`model.module_name`); its imports
    are not followed. Every def, async def and lambda gets a `FuncInfo`, and every class a
    `ClassInfo`. `filename` is recorded in every id and in the fingerprint. Raises
    `SyntaxError` for invalid source.
    """
    b = _Builder()
    info = b.add_module(source, filename, module_name(filename),
                        PurePath(filename).stem == "__init__")
    return b.finish([info])


# ---------------------------------------------------------------- loading files
@dataclass(frozen=True)
class _Found:
    """Where a module was found on the search path.

    file  its source file, or None for a namespace package
    dirs  directories its submodules are searched in; empty for a module that is no package
    """

    file: Path | None
    dirs: tuple[Path, ...]


def entry_module(path: Path) -> tuple[Path, str]:
    """Return the import root and the dotted module name of the entry file at `path`.

    The name is the file's stem, prefixed by every enclosing directory that is a package
    (has an `__init__.py`), as pyright and mypy name a file; the first directory that is not
    a package is the root. `main.py` is `main`, and `app/core.py` in package `app` is
    `app.core` with root `.`.
    """
    parts = [] if path.stem == "__init__" else [path.stem]
    d = path.parent
    while (d / "__init__.py").is_file():
        name = d.resolve().name
        if not name:  # the file system root
            break
        parts.insert(0, name)
        d = Path(os.path.normpath(d / ".."))
    return d, ".".join(parts)


def _dynamic_import_name(n: ast.Call) -> str | None:
    """Return the module named by `__import__("m")` or `importlib.import_module("m")`, or None.

    Only a string literal as the first argument counts, and relative names are skipped.
    """
    f = n.func
    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
    if name not in ("__import__", "import_module") or not n.args:
        return None
    a = n.args[0]
    if isinstance(a, ast.Constant) and isinstance(a.value, str) and not a.value.startswith("."):
        return a.value
    return None


@final
class _Loader:
    """Loads entry files and, transitively, every project module they import.

    Modules are searched in the entry roots, then in the extra search path, the way Python's
    path finder searches `sys.path`: in each directory a package (`name/__init__.py`) wins
    over `name.py`, and a directory without `__init__.py` is a namespace package only if no
    regular module is found anywhere. Submodules are searched in their package's directories.
    """

    def __init__(self, entries: Sequence[Path], search_path: Sequence[Path]) -> None:
        """Prepare to load `entries`, searching their roots and then `search_path`."""
        self.b = _Builder()
        self.entries = [(p, *entry_module(p)) for p in entries]
        roots: list[Path] = []
        for d in [root for _, root, _ in self.entries] + list(search_path):
            if d not in roots:
                roots.append(d)
        self.roots = roots
        self.found: dict[str, _Found | None] = {}
        self.by_file: dict[Path, str] = {}  # resolved source path -> module name
        self.todo: deque[ModuleInfo] = deque()

    def find(self, name: str) -> _Found | None:
        """Return where module `name` is, or None when it is not a project module (cached).

        Names of modules built into the interpreter (`sys.builtin_module_names`) are never
        project modules, and a top-level namespace directory never shadows a standard library
        module, which Python would find later on `sys.path`.
        """
        if name in self.found:
            return self.found[name]
        parent, _, last = name.rpartition(".")
        result: _Found | None = None
        dirs: Sequence[Path] = ()
        if not last.isidentifier():
            pass
        elif parent:
            loc = self.find(parent)
            dirs = loc.dirs if loc is not None else ()
        elif name not in sys.builtin_module_names:
            dirs = self.roots
        namespace: list[Path] = []
        for d in dirs:
            pkg = d / last
            if (pkg / "__init__.py").is_file():
                result = _Found(pkg / "__init__.py", (pkg,))
                break
            if (d / f"{last}.py").is_file():
                result = _Found(d / f"{last}.py", ())
                break
            if pkg.is_dir():
                namespace.append(pkg)
        if result is None and namespace and (parent or name not in sys.stdlib_module_names):
            result = _Found(None, tuple(namespace))
        self.found[name] = result
        return result

    def need(self, name: str) -> bool:
        """Load module `name` and its parent packages if they are project modules.

        Return True if `name` is a module of the program. A newly loaded file is queued, so
        that its own imports are loaded too.
        """
        if name in self.b.modules:
            return True
        loc = self.find(name)
        if loc is None:
            return False
        parent = name.rpartition(".")[0]
        if parent:
            self.need(parent)
        if loc.file is None:
            self.b.add_namespace(name)
            return True
        key = loc.file.resolve()
        if key in self.by_file:  # the same file under a second name: one module
            self.b.modules[name] = self.b.modules[self.by_file[key]]
            return True
        self.load(loc.file, str(loc.file), name, loc.file.name == "__init__.py")
        return True

    def load(self, path: Path, filename: str, name: str, package: bool) -> ModuleInfo:
        """Parse the file at `path` as module `name`, queue it for its imports, and return it.

        Raises `ValueError` if another file already is module `name`, as two entry files with
        the same name in different directories would be.
        """
        other = self.b.modules.get(name)
        if other is not None:
            raise ValueError(f"{filename} and {other.file} would both be module {name!r}")
        self.by_file[path.resolve()] = name
        info = self.b.add_module(path.read_text(encoding="utf-8"), filename, name, package)
        self.todo.append(info)
        return info

    def imports(self, info: ModuleInfo) -> Iterator[str]:
        """Yield the names of the modules that module `info` may import, in source order.

        Every scope counts: imports inside functions, `try` blocks and `if TYPE_CHECKING:`.
        `from X import n` also yields `X.n`, which may be a submodule, and a star import of a
        package yields the submodules its `__all__` lists. Dynamic imports count when they
        name the module with a string literal.
        """
        tree = self.b.tree(info)
        if tree is None:
            return
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                yield from (a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                base = absolute_module(info, n.level, n.module)
                if base is None:
                    continue
                yield base
                for a in n.names:
                    if a.name != "*":
                        yield f"{base}.{a.name}"
                    elif self.need(base) and (sub := self.b.tree(self.b.modules[base])):
                        yield from (f"{base}.{x}" for x in sorted(module_all(sub.body) or ()))
            elif isinstance(n, ast.Call) and (dyn := _dynamic_import_name(n)) is not None:
                yield dyn

    def run(self) -> Program:
        """Load the entries and every module they import, and return the `Program`."""
        entries: list[ModuleInfo] = []
        for path, _, name in self.entries:
            key = path.resolve()
            if key in self.by_file:
                entries.append(self.b.modules[self.by_file[key]])
                continue
            parent = name.rpartition(".")[0]
            if parent:
                self.need(parent)
            entries.append(self.load(path, str(path), name, path.name == "__init__.py"))
        while self.todo:
            for dep in self.imports(self.todo.popleft()):
                self.need(dep)
        return self.b.finish(entries)


def parse_files(paths: Iterable[str | os.PathLike[str]], *,
                search_path: Iterable[str | os.PathLike[str]] = ()) -> Program:
    """Read the UTF-8 Python entry files at `paths` and every project module they import.

    Return one `Program` whose roots are the entries' module bodies. A module is named by
    its dotted path relative to its import root (`entry_module`), and each file keeps the
    path it was found under (`str(path)` for the entries). Imports that name no file under
    the search path, such as the standard library, stay external. Raises `SyntaxError` for
    invalid source in any loaded file, and `ValueError` when `paths` is empty.

    search_path  more directories to search for imported modules, after the entries' own
                 roots, as `PYTHONPATH` adds to `sys.path`
    """
    entries = [Path(p) for p in paths]
    if not entries:
        raise ValueError("parse_files needs at least one entry file")
    return _Loader(entries, [Path(d) for d in search_path]).run()


def parse_file(path: str | os.PathLike[str], *,
               search_path: Iterable[str | os.PathLike[str]] = ()) -> Program:
    """Read the file at `path` and every project module it imports; see `parse_files`."""
    return parse_files([path], search_path=search_path)


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
