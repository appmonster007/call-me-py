"""The `Program`: what step 1 builds, with the per-function, per-class and per-module records.

`FuncInfo`, `ClassInfo` and `ModuleInfo` describe one definition each; `Sig` is a
function's signature. `builder.ProgramBuilder` fills them in.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from ..model import CFG, ClassId, FuncId

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
