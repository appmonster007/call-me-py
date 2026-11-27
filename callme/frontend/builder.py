"""Building a `Program` from parsed modules: ids, scopes, signatures, star imports and CFGs.

`ProgramBuilder` takes one module at a time (`add_module`) and then links them (`finish`).
`build_program` uses it for a single source string; `loader._Loader` for files on disk.
"""
from __future__ import annotations

import ast
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import PurePath

from ..model import ClassId, FuncId, module_name
from .cfg import build_cfg
from .program import FUNC_NODES, MODULE_NAME, ClassInfo, FuncInfo, ModuleInfo, Program, Sig
from .scopes import (
    class_known_attrs,
    make_sig,
    outer_parts,
    scope_decls,
    scope_locals,
    scope_must_writes,
    walk_scope,
)
from .sites import make_site


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


class ProgramBuilder:
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
    b = ProgramBuilder()
    info = b.add_module(source, filename, module_name(filename),
                        PurePath(filename).stem == "__init__")
    return b.finish([info])
