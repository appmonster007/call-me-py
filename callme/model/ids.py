"""Identities: of functions and classes, of call sites, and module names from file paths."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import override


def module_name(file: str) -> str:
    """Return the dotted module name for a file path taken as is, e.g. "pkg.util" for "pkg/util.py".

    A trailing `__init__` is dropped. If no name is left, the file path is returned as is.
    Programs loaded from disk name their modules relative to the import root instead.
    """
    parts = PurePath(file).with_suffix("").parts
    parts = tuple(p for p in parts if p not in ("/", ".", ".."))
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or file


@dataclass(frozen=True)
class _SymId:
    """Identity layout shared by functions and classes; the string forms are built on demand.

    file      source path as given, e.g. "pkg/util.py"
    qualname  dotted scope path of the definition, e.g. "Outer.make.inner"
    lineno    1-based line of the definition
    col       0-based column of the definition
    module    dotted name of the defining module, e.g. "pkg.util"; when left empty it is
              derived from `file` (see `module_name`). It is not part of the identity.
    """

    qualname: str
    file: str
    lineno: int
    col: int
    module: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        """Derive `module` from `file` when it was not given."""
        if not self.module:
            object.__setattr__(self, "module", module_name(self.file))

    @property
    def name(self) -> str:
        """Return the last part of the qualname, e.g. "inner" for "Outer.make.inner"."""
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def scope(self) -> str:
        """Return the qualname without its last part, e.g. "Outer.make", or "" at top level."""
        return self.qualname.rpartition(".")[0]

    @property
    def uid(self) -> str:
        """Return the "full" form `file:line:col:qualname`, unique even when names repeat."""
        return self.render("full")

    def render(self, style: str = "full") -> str:
        """Return this id as a string in the given style.

        style  "full" (file:line:col:qualname, always unique), "module" (module.qualname) or
               "short" (qualname alone, which can collide)

        Raises `ValueError` for any other style.
        """
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
class FuncId(_SymId):
    """Identity of one function: a def, async def, lambda or the module body (`<module>`)."""


@dataclass(frozen=True)
class ClassId(_SymId):
    """Identity of one class definition."""


@dataclass(frozen=True)
class Site:
    """A call site, identified by its caller and source position.

    text  source text of the call, at most 60 characters; decorators start with "@"
    kind  "call" or "decorator" for syntactic sites, which are CFG call points; "op", "attr",
          "iter", "with" or "class" for the implicit dunder calls behind operators, attribute
          access, iteration, `with` and class creation, which have no CFG point
    """

    caller: FuncId
    line: int
    col: int
    text: str
    kind: str = "call"  # "call" | "decorator"
