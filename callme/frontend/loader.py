"""Loading files: find the entry modules and every project module they import, transitively.

Module names are resolved the way Python's path finder does: regular packages, plain
modules and namespace packages, searched along the search path.
"""
from __future__ import annotations

import ast
import os
import sys
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import final

from .builder import ProgramBuilder, absolute_module, module_all
from .program import ModuleInfo, Program


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
        self.b = ProgramBuilder()
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
