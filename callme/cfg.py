"""Step 1b: one control-flow graph per function (module body, def, lambda).

Program points (see model.py for the kinds):
  entry, exit       one each per function
  stmt              a statement's own effect, after the calls inside it
  branch            a test; successors are labelled true / false
  loop              loop head (while join point, for / comprehension "next item")
  handler           start of an except clause
  call, after_call  every call site is split in two, joined by a call_to_return
                    edge; the ICFG adds call edges out of `call` and return edges
                    into `after_call`

Calls are emitted in Python evaluation order. Nested functions are not entered
(their decorators and defaults are evaluated here, their bodies get their own
CFG); class bodies are inlined because they run in the enclosing function.

Exceptions: `raise`, failed `assert`, and every point inside a `try` body go to
that try's handlers (exc edges); `return` / `break` / `continue` / `raise`
leaving a `try ... finally` run the finally block first. Implicit exceptions
outside any `try` are not modelled.

Every statement gets a node even when unreachable (dead code), because the call
graph analysis still reports call sites in dead code (see transfer.Interp.block).
"""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Sequence
from typing import final

from .model import (AFTER_CALL, BRANCH, CALL, CALL_TO_RETURN, CFG, CFGEdge, CFGNode,
                    ENTRY, EXC, EXIT, FALSE, FLOW, HANDLER, LOOP, STMT, TRUE, FuncId, Site)

FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
COMPS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
Comprehension = ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp
TRY_NODES = (ast.Try, ast.TryStar)

Frontier = list[tuple[int, str]]  # dangling (source point, edge kind) pairs


def short(node: ast.AST, limit: int = 50) -> str:
    text = ast.unparse(node).split("\n", 1)[0]
    return text if len(text) <= limit else text[:limit - 3] + "..."


@final
class _Loop:
    def __init__(self, head: int) -> None:
        self.head = head
        self.breaks: Frontier = []


@final
class _Try:  # collects exception sources of a try body
    def __init__(self) -> None:
        self.exc: Frontier = []


@final
class _Finally:  # abrupt exits waiting to run a finally block
    def __init__(self) -> None:
        self.pending: list[tuple[Frontier, str]] = []


@final
class Builder:
    def __init__(self, fid: FuncId, make_site: Callable[..., Site]) -> None:
        self.fid, self.make_site = fid, make_site
        self.nodes: list[CFGNode] = []
        self.edges: dict[CFGEdge, None] = {}  # ordered set
        self.calls: dict[Site, tuple[int, int]] = {}
        self.frames: list[_Loop | _Try | _Finally] = []
        self.frontier: Frontier = []
        self.entry = self.new(ENTRY, None, "entry")
        self.exit = self.new(EXIT, None, "exit")
        self.frontier = [(self.entry, FLOW)]

    # ------------------------------------------------------------ plumbing
    def new(self, kind: str, node: ast.AST | None, text: str, site: Site | None = None) -> int:
        idx = len(self.nodes)
        self.nodes.append(CFGNode(idx, kind, getattr(node, "lineno", 0),
                                  getattr(node, "col_offset", 0), text, site))
        return idx

    def link(self, src: Frontier, dst: int) -> None:
        for s, k in src:
            self.edges[CFGEdge(s, dst, k)] = None

    def emit(self, kind: str, node: ast.AST | None, text: str, site: Site | None = None) -> int:
        """New point reached from the frontier; it becomes the new frontier."""
        idx = self.new(kind, node, text, site)
        self.link(self.frontier, idx)
        self.frontier = [(idx, FLOW)]
        for f in reversed(self.frames):  # any point inside a try body may raise
            if isinstance(f, _Try):
                f.exc.append((idx, EXC))
                break
            if isinstance(f, _Finally):
                f.pending.append(([(idx, EXC)], "raise"))
                break
        return idx

    def jump(self, kind: str) -> None:
        """Leave the frontier abruptly: return | raise | break | continue."""
        src, self.frontier = self.frontier, []
        if not src:
            return
        for f in reversed(self.frames):
            if isinstance(f, _Finally):
                f.pending.append((src, kind))
                return
            if kind == "raise" and isinstance(f, _Try):
                f.exc.extend((s, EXC) for s, _ in src)
                return
            if kind in ("break", "continue") and isinstance(f, _Loop):
                if kind == "break":
                    f.breaks.extend(src)
                else:
                    self.link(src, f.head)
                return
        self.link([(s, EXC if kind == "raise" else k) for s, k in src], self.exit)

    def branch(self, node: ast.AST, text: str) -> int:
        return self.emit(BRANCH, node, text)

    # ------------------------------------------------------------ expressions
    def expr(self, e: ast.AST | None) -> None:
        if e is None:
            return
        if isinstance(e, ast.Call):
            self.expr(e.func)
            for a in e.args:
                self.expr(a)
            for k in e.keywords:
                self.expr(k.value)
            self.call_point(e, self.make_site(self.fid, e))
        elif isinstance(e, ast.Lambda):  # body has its own CFG
            for d in [*e.args.defaults, *[k for k in e.args.kw_defaults if k is not None]]:
                self.expr(d)
        elif isinstance(e, ast.IfExp):
            self.expr(e.test)
            b = self.branch(e, short(e.test))
            self.frontier = [(b, TRUE)]
            self.expr(e.body)
            then = self.frontier
            self.frontier = [(b, FALSE)]
            self.expr(e.orelse)
            self.frontier = then + self.frontier
        elif isinstance(e, ast.BoolOp):  # short circuit
            done: Frontier = []
            stop = TRUE if isinstance(e.op, ast.Or) else FALSE
            go = FALSE if stop == TRUE else TRUE
            self.expr(e.values[0])
            for prev, v in zip(e.values, e.values[1:]):
                b = self.branch(prev, short(prev))
                done.append((b, stop))
                self.frontier = [(b, go)]
                self.expr(v)
            self.frontier = done + self.frontier
        elif isinstance(e, COMPS):
            self.comprehension(e)
        elif isinstance(e, ast.Dict):  # k1, v1, k2, v2, ...
            for k, v in zip(e.keys, e.values):
                self.expr(k)
                self.expr(v)
        else:
            for c in ast.iter_child_nodes(e):
                if not isinstance(c, (ast.expr_context, ast.operator, ast.unaryop,
                                      ast.cmpop, ast.boolop)):
                    self.expr(c)

    def call_point(self, node: ast.AST, site: Site) -> None:
        c = self.emit(CALL, node, site.text, site)
        self.frontier = [(c, CALL_TO_RETURN)]
        r = self.emit(AFTER_CALL, node, site.text, site)
        self.calls[site] = (c, r)

    def comprehension(self, e: Comprehension) -> None:
        """One loop head for all generators (nested loops are merged)."""
        gens = e.generators
        self.expr(gens[0].iter)
        head = self.emit(LOOP, e, f"for {short(gens[0].target)} in {short(gens[0].iter)}")
        self.frontier = [(head, TRUE)]
        back: Frontier = []
        for i, g in enumerate(gens):
            self.expr(g.target)
            if i:
                self.expr(g.iter)
            for cond in g.ifs:
                self.expr(cond)
                b = self.branch(cond, short(cond))
                back.append((b, FALSE))
                self.frontier = [(b, TRUE)]
        if isinstance(e, ast.DictComp):
            self.expr(e.key)
            self.expr(e.value)
        else:
            self.expr(e.elt)
        self.link(self.frontier + back, head)
        self.frontier = [(head, FALSE)]

    # ------------------------------------------------------------ statements
    def block(self, stmts: Iterable[ast.AST]) -> None:
        for s in stmts:
            self.stmt(s)

    def stmt(self, s: ast.AST) -> None:
        m = getattr(self, "s_" + type(s).__name__, None)
        if m is not None:
            m(s)
        elif isinstance(s, TRY_NODES):
            self.s_Try(s)
        else:  # simple statement: its expressions, then its effect
            for c in ast.iter_child_nodes(s):
                if not isinstance(c, (ast.expr_context, ast.operator)):
                    self.expr(c)
            self.emit(STMT, s, short(s))

    def s_Assign(self, s: ast.Assign) -> None:
        self.expr(s.value)
        for t in s.targets:
            self.expr(t)
        self.emit(STMT, s, short(s))

    def s_Return(self, s: ast.Return) -> None:
        self.expr(s.value)
        self.emit(STMT, s, short(s))
        self.jump("return")

    def s_Raise(self, s: ast.Raise) -> None:
        self.expr(s.exc)
        self.expr(s.cause)
        self.emit(STMT, s, short(s))
        self.jump("raise")

    def s_Break(self, s: ast.Break) -> None:
        self.emit(STMT, s, "break")
        self.jump("break")

    def s_Continue(self, s: ast.Continue) -> None:
        self.emit(STMT, s, "continue")
        self.jump("continue")

    def s_Assert(self, s: ast.Assert) -> None:
        self.expr(s.test)
        b = self.branch(s, short(s.test))
        self.frontier = [(b, FALSE)]
        self.expr(s.msg)
        self.jump("raise")
        self.frontier = [(b, TRUE)]

    def s_If(self, s: ast.If) -> None:
        self.expr(s.test)
        b = self.branch(s, short(s.test))
        self.frontier = [(b, TRUE)]
        self.block(s.body)
        then = self.frontier
        self.frontier = [(b, FALSE)]
        self.block(s.orelse)
        self.frontier = then + self.frontier

    def s_While(self, s: ast.While) -> None:
        head = self.emit(LOOP, s, "while")
        self.expr(s.test)
        b = self.branch(s.test, short(s.test))
        self.loop_body(head, [(b, TRUE)], [(b, FALSE)], s)

    def s_For(self, s: ast.For | ast.AsyncFor) -> None:
        self.expr(s.iter)
        head = self.emit(LOOP, s, f"for {short(s.target)} in {short(s.iter)}")
        self.frontier = [(head, TRUE)]
        self.expr(s.target)  # subscript / attribute targets may contain calls
        self.loop_body(head, self.frontier, [(head, FALSE)], s)

    s_AsyncFor = s_For

    def loop_body(self, head: int, enter: Frontier, leave: Frontier,
                  s: ast.While | ast.For | ast.AsyncFor) -> None:
        loop = _Loop(head)
        self.frames.append(loop)
        self.frontier = enter
        self.block(s.body)
        self.link(self.frontier, head)
        self.frames.pop()
        self.frontier = leave
        self.block(s.orelse)
        self.frontier += loop.breaks

    def s_With(self, s: ast.With | ast.AsyncWith) -> None:
        for it in s.items:
            self.expr(it.context_expr)
            self.expr(it.optional_vars)
        self.emit(STMT, s, "with " + ", ".join(short(i.context_expr, 30) for i in s.items))
        self.block(s.body)

    s_AsyncWith = s_With

    def s_Try(self, s: ast.Try | ast.TryStar) -> None:
        fin = _Finally() if s.finalbody else None
        if fin:
            self.frames.append(fin)
        body = _Try()
        self.frames.append(body)
        self.block(s.body)
        self.frames.pop()
        self.block(s.orelse)
        normal = self.frontier
        for h in s.handlers:
            self.frontier = list(body.exc)
            self.expr(h.type)
            self.emit(HANDLER, h, "except" + (f" {short(h.type)}" if h.type else "")
                      + (f" as {h.name}" if h.name else ""))
            self.block(h.body)
            normal = normal + self.frontier
        self.frontier = list(body.exc)  # not caught here (handler types are unknown)
        self.jump("raise")
        if not fin:
            self.frontier = normal
            return
        self.frames.pop()
        kinds = [k for _, k in fin.pending]
        self.frontier = normal + [p for src, _ in fin.pending for p in src]
        self.block(s.finalbody)
        end = self.frontier
        for k in dict.fromkeys(kinds):  # then continue the abrupt exits
            self.frontier = end
            self.jump(k)
        self.frontier = end if normal else []

    def s_FunctionDef(self, s: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for d in s.decorator_list:
            self.expr(d)
        a = s.args
        for d in [*a.defaults, *[k for k in a.kw_defaults if k is not None]]:
            self.expr(d)
        self.decorate(s)
        self.emit(STMT, s, f"def {s.name}")

    s_AsyncFunctionDef = s_FunctionDef

    def s_ClassDef(self, s: ast.ClassDef) -> None:
        for x in [*s.decorator_list, *s.bases, *[k.value for k in s.keywords]]:
            self.expr(x)
        self.block(s.body)  # the class body runs here
        self.decorate(s)
        self.emit(STMT, s, f"class {s.name}")

    def decorate(self, s: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        for d in reversed(s.decorator_list):  # applied bottom-up
            self.call_point(d, self.make_site(self.fid, d, "decorator"))

    def s_Match(self, s: ast.Match) -> None:
        self.expr(s.subject)
        out: Frontier = []
        for case in s.cases:
            self.expr(case.pattern)
            b = self.branch(case.pattern, "case " + short(case.pattern))
            miss = [(b, FALSE)]
            self.frontier = [(b, TRUE)]
            if case.guard is not None:
                self.expr(case.guard)
                g = self.branch(case.guard, short(case.guard))
                miss.append((g, FALSE))
                self.frontier = [(g, TRUE)]
            self.block(case.body)
            out += self.frontier
            self.frontier = miss
        self.frontier = out + self.frontier

    # ------------------------------------------------------------ result
    def finish(self) -> CFG:
        self.link(self.frontier, self.exit)
        return CFG(self.fid, tuple(self.nodes), tuple(self.edges), dict(self.calls),
                   self.entry, self.exit)


def build_cfg(fid: FuncId, node: ast.AST, body: Sequence[ast.AST],
              make_site: Callable[..., Site]) -> CFG:
    """CFG of one function (FuncInfo's id, node and body): module, def or lambda."""
    b = Builder(fid, make_site)
    if isinstance(node, ast.Lambda):
        b.expr(node.body)
        b.emit(STMT, node.body, "return " + short(node.body))
    else:
        b.block(body)
    return b.finish()
