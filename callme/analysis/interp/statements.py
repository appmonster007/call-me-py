"""Layer 7 of the interpreter, `StmtLayer`: statements."""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterable

from ...frontend import make_site
from ...model import EMPTY, Alloc, ClassObj, Instance, Property, Vals
from .base import bind_attr
from .operators import OperatorLayer


class StmtLayer(OperatorLayer):
    """Layer 7: statements. `block` dispatches to `s_<Stmt>`; assignments,
    `assert`, `del`, `return`, `raise`, and `run`, which runs the whole body.
    """

    # fields of BaseLayer that this layer assigns
    ret: Vals
    live: bool
    _weak_depth: int

    def block(self, stmts: Iterable[ast.AST]) -> None:
        """Run statements in order, dispatching each to `s_<StmtType>` or `generic_stmt`.

        In flow mode, code after a `return`, `raise`, `break` or `continue` is still analysed,
        so its call sites stay in the graph. It starts from the environment at the jump, but
        the block stays dead afterwards, so its state never reaches live code.
        """
        reached = True
        for s in stmts:
            if not self.live:  # flow mode: dead code after a jump is still analysed (its call
                reached = False  # sites stay in the graph), from the env at the jump ...
                self.live = True
            m: Callable[[ast.AST], None] | None = getattr(self, "s_" + type(s).__name__, None)
            if m:
                m(s)
            else:
                self.generic_stmt(s)
        if not reached:  # ... but its state never reaches live code
            self.live = False

    def generic_stmt(self, s: ast.AST) -> None:
        """Run a statement without its own handler: its expressions, nested statements and handlers.

        This is also how compound statements run in flow-insensitive mode, where branches and
        loops need no special treatment.
        """
        for c in ast.iter_child_nodes(s):
            if isinstance(c, ast.expr):
                self.eval(c)
            elif isinstance(c, ast.stmt):
                self.block([c])
            elif isinstance(c, ast.ExceptHandler):
                self._handler(c)

    def _handler(self, h: ast.ExceptHandler) -> None:
        """Run an `except` clause, binding `as name` to an instance of each caught class."""
        exc = self.eval(h.type)
        if h.name:
            at = Alloc(self.info.id, h.lineno, h.col_offset)
            self.bind(h.name, frozenset(Instance(t.cls, at) for t in exc if isinstance(t, ClassObj)))
        self.block(h.body)

    def s_Raise(self, s: ast.Raise) -> None:
        """Evaluate `raise` for its calls and end the current path."""
        self.eval(s.exc)
        self.eval(s.cause)
        if self.flow:  # handlers start from their try's accumulator, which already holds this state
            self.live = False

    def s_Assign(self, s: ast.Assign) -> None:
        """Run `a = b = value`: evaluate the value once and assign it to every target."""
        v = self.eval(s.value)
        for t in s.targets:
            self.assign(t, v)

    def s_AnnAssign(self, s: ast.AnnAssign) -> None:
        """Run `target: T = value`; an annotation without a value binds nothing."""
        if s.value is not None:
            self.assign(s.target, self.eval(s.value))

    def s_Assert(self, s: ast.Assert) -> None:
        """Evaluate `assert test, msg`; `msg` runs only on failure so its effects stay weak."""
        self.eval(s.test)
        if s.msg is not None:
            self._weak_depth += 1
            try:
                self.eval(s.msg)
            finally:
                self._weak_depth -= 1

    def s_Delete(self, s: ast.Delete) -> None:
        """Run `del` on each target, calling the special methods involved.

        `del obj.attr` calls a property's deleter or a descriptor's `__delete__`, and `del x[i]`
        calls `__delitem__`. In flow mode `del name` leaves a local empty until it is rebound.
        """
        for t in s.targets:
            if isinstance(t, ast.Attribute):
                site = make_site(self.info.id, t, "attr")
                self._weak_depth += 1
                try:
                    for obj in self.eval(t.value):
                        if isinstance(obj, Instance):
                            cls_vals = self.state.lookup_class_attr(obj.cls, t.attr)
                            fdels = EMPTY
                            for tok in cls_vals:
                                if isinstance(tok, Property) and tok.fdel:
                                    fdels |= tok.fdel
                                elif isinstance(tok, Instance):
                                    del_fn = self.state.lookup_class_attr(tok.cls, "__delete__")
                                    if del_fn:
                                        bound = frozenset(bind_attr(f, tok.cls, tok) for f in del_fn)
                                        self.apply(bound, [frozenset([obj])], None, {}, None, site)
                            if fdels:
                                self.apply(fdels, [frozenset([obj])], None, {}, None, site)
                finally:
                    self._weak_depth -= 1
            elif isinstance(t, ast.Subscript):
                slc = self.eval(t.slice)
                obj = self.eval(t.value)
                self.invoke_dunder(obj, "__delitem__", [slc], make_site(self.info.id, t, "op"))
            elif (self.flow and isinstance(t, ast.Name) and not self.class_stack
                  and t.id in self.info.locals):
                self.env.pop(t.id, None)  # `del x`: x holds nothing until rebound
            else:
                self.eval(t)

    def s_Return(self, s: ast.Return) -> None:
        """Join the returned value into the node's return value and end the current path."""
        self.ret |= self.eval(s.value)
        if self.flow:
            self.live = False

    def run(self) -> None:
        """Run the function's body: a lambda's expression becomes its return value."""
        node = self.info.node
        if self.info.kind == "lambda" and isinstance(node, ast.Lambda):
            self.ret = self.eval(node.body)
        else:
            self.block(self.info.body)
