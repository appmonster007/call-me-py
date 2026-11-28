"""Layer 9 of the interpreter, `FlowLayer`: control flow."""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Sequence
from typing import Final

from ...frontend import make_site, pattern_names
from ...model import EMPTY, Site, Vals
from ..state import Env, join_envs
from .base import End, LoopFrame
from .definitions import DefLayer


class FlowLayer(DefLayer):
    """Layer 9: control flow. Branches, loops, `break` / `continue`, `try`,
    `match` and `with`, each joining the environments of its paths in flow mode.
    """

    # fields of BaseLayer that this layer assigns
    env: Env
    live: bool

    def _path(self, start: Env, stmts: Iterable[ast.AST]) -> End:
        """Run `stmts` from a copy of `start` and return how the path ends.

        The result is the environment at the end and whether a live path gets there.
        """
        self.env, self.live = dict(start), True
        self.block(stmts)
        return self.env, self.live

    def _meet(self, ends: Sequence[End]) -> None:
        """Continue after alternative paths meet, from the join of the ends that are live.

        If none is live, the code that follows is dead: it is analysed from the join of all the
        ends, and the current path stays dead.
        """
        live = [e for e, ok in ends if ok]
        self.live = bool(live)
        pick = live or [e for e, _ in ends]
        self.env = dict(pick[0]) if len(pick) == 1 else join_envs(pick)

    def s_If(self, s: ast.If) -> None:
        """Run `if`: in flow mode both branches start from the same state, then meet."""
        if not self.flow:
            return self.generic_stmt(s)
        self.eval(s.test)
        start = self.env
        self._meet([self._path(start, s.body), self._path(start, s.orelse)])

    def _loop(self, s: ast.While | ast.For | ast.AsyncFor, enter: Callable[[], object]) -> None:
        """Run a loop body from the loop head until the head stops growing, then the `else` block.

        `enter` runs at the head before each iteration: the `while` test, or binding the `for`
        target. The loop is left from the final head, after the `else` block, joined with the
        state at every `break`; `continue` states join the head.
        """
        frame = LoopFrame()
        self.loops.append(frame)
        head = self.env
        while True:
            self.env, self.live = dict(head), True
            enter()
            self.block(s.body)
            grown = join_envs([head, *([self.env] if self.live else []), *frame.continues])
            if grown == head:
                break
            head = grown
        self.loops.pop()
        self.env, self.live = dict(head), True
        if isinstance(s, ast.While):
            self.eval(s.test)
        self.block(s.orelse)
        self._meet([(self.env, self.live), *((b, True) for b in frame.breaks)])

    def s_While(self, s: ast.While) -> None:
        """Run `while` as a loop whose head evaluates the test."""
        if not self.flow:
            return self.generic_stmt(s)
        self._loop(s, lambda: self.eval(s.test))

    def s_Break(self, _s: ast.Break) -> None:
        """End the current path at `break`, saving its state for the end of the loop."""
        if self.flow and self.loops:
            self.loops[-1].breaks.append(dict(self.env))
            self.live = False

    def s_Continue(self, _s: ast.Continue) -> None:
        """End the current path at `continue`, saving its state for the loop head."""
        if self.flow and self.loops:
            self.loops[-1].continues.append(dict(self.env))
            self.live = False

    def s_Try(self, s: ast.Try | ast.TryStar) -> None:
        """Run `try` or `try*` with its handlers, `else` block and finally block, in flow mode.

        Any point of the body may raise, so each handler starts from the join of every state
        the body passed through. The `else` block runs only after the body completes. The
        finally block starts from every normal end joined with every state seen in the body,
        the handlers and the `else` block; a `break` or `continue` inside the `try` passes
        through it before leaving.
        """
        if not self.flow:
            return self.generic_stmt(s)
        loop = self.loops[-1] if self.loops else None
        marks = (len(loop.breaks), len(loop.continues)) if loop else (0, 0)
        fin = dict(self.env) if s.finalbody else None
        if fin is not None:
            self.tries.append(fin)
        caught = dict(self.env)
        self.tries.append(caught)
        self.block(s.body)
        self.tries.pop()
        ends: list[End] = []
        body_live = self.live
        env, ok = self._path(self.env, s.orelse)  # else: only after the body completes
        ends.append((env, ok and body_live))
        for h in s.handlers:
            self.env, self.live = dict(caught), True
            self._handler(h)
            ends.append((self.env, self.live))
        if fin is None:
            self._meet(ends)
            return
        self.tries.pop()
        normal = [e for e, ok in ends if ok]
        self.env, self.live = join_envs([*normal, fin]), True
        self.block(s.finalbody)
        if loop is not None and self.live:  # a break / continue in the try ran this finally first
            if len(loop.breaks) > marks[0]:
                loop.breaks.append(dict(self.env))
            if len(loop.continues) > marks[1]:
                loop.continues.append(dict(self.env))
        self.live = self.live and bool(normal)

    s_TryStar: Final = s_Try

    def s_Match(self, s: ast.Match) -> None:
        """Run `match`, binding each case's captures to the subject's value.

        In flow mode the cases are alternatives. A case that does not match falls through to
        the next one with whatever its pattern and guard already bound. The path where no case
        matches meets the ends of the cases.
        """
        subject = self.eval(s.subject)
        captured = self.state.deref(subject)
        tried = self.env  # state when the next case is tried
        ends: list[End] = []
        for case in s.cases:
            if self.flow:
                self.env, self.live = dict(tried), True
            for n in ast.walk(case.pattern):
                if isinstance(n, ast.expr):  # value patterns, class names
                    self.eval(n)
                for name in pattern_names(n):
                    self.bind(name, captured)  # captures: some part of the subject
            self.eval(case.guard)
            if self.flow:
                tried = join_envs([tried, self.env])
            self.block(case.body)
            ends.append((self.env, self.live))
        if self.flow:
            self._meet([*ends, (tried, True)])  # `tried`: no case matched

    def s_For(self, s: ast.For | ast.AsyncFor) -> None:
        """Run `for` or `async for` over the iterable's elements, as a loop in flow mode."""
        elems = self._iter_expr(s.iter, is_async=isinstance(s, ast.AsyncFor))
        if self.flow:
            self._loop(s, lambda: self.assign(s.target, elems))
            return
        self.assign(s.target, elems)
        self.block(s.body)
        self.block(s.orelse)

    s_AsyncFor: Final = s_For

    def s_With(self, s: ast.With | ast.AsyncWith) -> None:
        """Run `with` or `async with`: call `__enter__`, run the body, then call `__exit__`.

        The `as` target is bound to what `__enter__` returns, or to the context manager itself
        when no class defines `__enter__`. In flow mode `__exit__` may swallow an exception from
        any point of the body, so the state after the statement also joins every state of the
        body.
        """
        enter_name = "__aenter__" if isinstance(s, ast.AsyncWith) else "__enter__"
        exit_name = "__aexit__" if isinstance(s, ast.AsyncWith) else "__exit__"
        exits: list[tuple[Vals, Site]] = []
        for it in s.items:
            v = self.eval(it.context_expr)
            site = make_site(self.info.id, it.context_expr, "with")
            found, entered = self.invoke_dunder(v, enter_name, [], site)
            exits.append((v, site))
            if it.optional_vars is not None:
                self.assign(it.optional_vars, entered if found else v)
        suppressed = dict(self.env) if self.flow else None  # every state of the body
        if suppressed is not None:
            self.tries.append(suppressed)
        self.block(s.body)
        if suppressed is not None:
            self.tries.pop()
        for v, site in reversed(exits):
            self.invoke_dunder(v, exit_name, [EMPTY, EMPTY, EMPTY], site)
        if suppressed is not None:  # __exit__ may swallow an exception from anywhere in the body
            self._meet([(self.env, self.live), (suppressed, True)])

    s_AsyncWith: Final = s_With
