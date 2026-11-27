"""Layer 2 of the interpreter, `ModuleLayer`: modules."""
from __future__ import annotations

import ast
from abc import ABC

from ...frontend import absolute_module
from ...model import EMPTY, Builtin, Ctx, Module, Node, Vals
from ...stubs import BUILTIN_NAMES, STUBS, STUB_MODULES, STUB_PACKAGES
from .names import NameLayer


class ModuleLayer(NameLayer, ABC):
    """Layer 2: modules. Running an imported module's body, reading `mod.attr`,
    and the `import` statements.
    """

    def run_module(self, name: str) -> None:
        """Run the body of program module `name` and of its parent packages, as an import does.

        Each body's node is registered, so that it is analysed, but no call edge is recorded,
        because a traced run shows no call into a module body either. A namespace package has
        no body, and read-only mode registers nothing.
        """
        if self.readonly:
            return
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            info = self.prog.modules.get(".".join(parts[:i]))
            if info is not None and info.func is not None:
                mod_node = Node(info.func, Ctx())
                self.state.register(mod_node)
                if self.flow:
                    mw = self.state.writes.get(mod_node)
                    if mw:
                        self._apply_writes(mw)

    def module_attr(self, name: str, attr: str) -> Vals:
        """Return attribute `attr` of program module `name`: one of its globals, or a submodule.

        A global's value is its summary in the module body, plus what other scopes stored
        into it (`global` writes and `module.attr = ...`, in `State.extern`). A submodule
        `name.attr` of the program is an attribute too, as Python sets it on its package once
        it is imported.
        """
        out = EMPTY
        body = self.prog.modules[name].func if name in self.prog.modules else None
        if body is not None:
            out = (self.state.union_env(body, attr)
                   | self.state.extern.get(body, {}).get(attr, EMPTY))
        sub = f"{name}.{attr}"
        if sub in self.prog.modules:
            out |= frozenset([Module(sub)])
        return out

    def import_any(self) -> Vals:
        """Return every module of the program, and run each, as an import of an unknown name may.

        This models `__import__(name)` and `importlib.import_module(name)`: the analysis does
        not track strings, so any module may be the one imported.
        """
        for name in self.prog.modules:
            self.run_module(name)
        return frozenset(Module(n) for n in self.prog.modules)

    def s_Import(self, s: ast.Import) -> None:
        """Run `import`: bind program modules as `Module`s and stub modules as `Builtin`s.

        `import a.b.c` runs `a`, `a.b` and `a.b.c` and binds `a`; `import a.b.c as x` binds
        `x` to `a.b.c` itself. A program module wins over a stub module of the same name. Any
        other module binds nothing, so calls through it get no edge.
        """
        for a in s.names:
            top = a.name.split(".")[0]
            if a.name in self.prog.modules:
                self.run_module(a.name)
                self.bind(a.asname or top, frozenset([Module(a.name if a.asname else top)]))
            elif a.asname and (a.name in STUB_MODULES or a.name in STUB_PACKAGES):
                self.bind(a.asname, frozenset([Builtin(a.name)]))
            elif not a.asname and (a.name in STUB_MODULES or top in STUB_MODULES or top in STUB_PACKAGES):
                self.bind(top, frozenset([Builtin(top)]))

    def s_ImportFrom(self, s: ast.ImportFrom) -> None:
        """Run `from module import name`: bind attributes of a program module, or stubs.

        For a program module, the module and its parent packages run, and each name is bound
        to the module's attribute (`module_attr`); a name that is a submodule runs as well.
        `from module import *` binds every name in the module's `exports`. A relative import
        resolves against the current module's package. For other modules only the names
        that stubs provide are bound.
        """
        base = absolute_module(self.prog.module_of(self.info.id), s.level, s.module)
        if base is not None and base in self.prog.modules:
            self.run_module(base)
            for a in s.names:
                star = a.name == "*"
                for n in sorted(self.prog.modules[base].exports) if star else [a.name]:
                    if f"{base}.{n}" in self.prog.modules:
                        self.run_module(f"{base}.{n}")
                    self.bind(n if star else a.asname or n, self.module_attr(base, n))
            return
        if s.level or (s.module not in STUB_MODULES and s.module not in STUB_PACKAGES):
            return
        for a in s.names:
            if a.name == "*":
                if s.module == "builtins":
                    for n in BUILTIN_NAMES:
                        self.bind(n, frozenset([Builtin(n)]))
                else:
                    prefix = f"{s.module}."
                    for full in STUBS:
                        if full.startswith(prefix) and "." not in full[len(prefix):]:
                            self.bind(full[len(prefix):], frozenset([Builtin(full)]))
                continue
            full = f"{s.module}.{a.name}"
            if full in STUBS or full in STUB_MODULES or full in STUB_PACKAGES:
                self.bind(a.asname or a.name, frozenset([Builtin(full)]))
            elif s.module == "builtins" and a.name in BUILTIN_NAMES:
                self.bind(a.asname or a.name, frozenset([Builtin(a.name)]))
