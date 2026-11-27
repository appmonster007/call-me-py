"""Call sites: the `Site` of a syntactic call, shared by the CFG builder and the interpreter."""
from __future__ import annotations

import ast

from ..model import FuncId, Site
from .program import Program


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
