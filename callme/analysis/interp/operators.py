"""Layer 6 of the interpreter, `OperatorLayer`: operators."""
from __future__ import annotations

import ast

from ...frontend import make_site
from ...model import EMPTY, Builtin, Instance, Token, Vals
from ...stubs import STUBS
from .base import bind_attr
from .expressions import ExprLayer


BINOP_DUNDERS: dict[type[ast.operator], tuple[str, str, str]] = {
    ast.Add: ("__add__", "__radd__", "__iadd__"),
    ast.Sub: ("__sub__", "__rsub__", "__isub__"),
    ast.Mult: ("__mul__", "__rmul__", "__imul__"),
    ast.MatMult: ("__matmul__", "__rmatmul__", "__imatmul__"),
    ast.Div: ("__truediv__", "__rtruediv__", "__itruediv__"),
    ast.FloorDiv: ("__floordiv__", "__rfloordiv__", "__ifloordiv__"),
    ast.Mod: ("__mod__", "__rmod__", "__imod__"),
    ast.Pow: ("__pow__", "__rpow__", "__ipow__"),
    ast.LShift: ("__lshift__", "__rlshift__", "__ilshift__"),
    ast.RShift: ("__rshift__", "__rrshift__", "__irshift__"),
    ast.BitOr: ("__or__", "__ror__", "__ior__"),
    ast.BitXor: ("__xor__", "__rxor__", "__ixor__"),
    ast.BitAnd: ("__and__", "__rand__", "__iand__"),
}
UNARYOP_DUNDERS: dict[type[ast.unaryop], str] = {
    ast.UAdd: "__pos__",
    ast.USub: "__neg__",
    ast.Invert: "__invert__",
}
CMP_DUNDERS: dict[type[ast.cmpop], tuple[str, str]] = {
    ast.Eq: ("__eq__", "__eq__"),
    ast.NotEq: ("__ne__", "__ne__"),
    ast.Lt: ("__lt__", "__gt__"),
    ast.LtE: ("__le__", "__ge__"),
    ast.Gt: ("__gt__", "__lt__"),
    ast.GtE: ("__ge__", "__le__"),
}


class OperatorLayer(ExprLayer):
    """Layer 6: operators. The dunders behind binary and unary operators,
    comparisons, subscripts, f-string fields and augmented assignment.
    """

    # fields of BaseLayer that this layer assigns
    _weak_depth: int

    def e_Subscript(self, e: ast.Subscript) -> Vals:
        """Evaluate `x[i]` when it is read: call `__getitem__` on instances that define it.

        Other values are containers, collapsed to their elements, so their value plus their
        stored contents is the result. A store or delete target gives `EMPTY`.
        """
        slc = self.eval(e.slice)
        val = self.eval(e.value)
        if not isinstance(e.ctx, ast.Load):
            return EMPTY
        site = make_site(self.info.id, e, "op")
        out = EMPTY
        rest: set[Token] = set()
        self._weak_depth += 1
        try:
            for t in val:
                if isinstance(t, Instance):
                    fn = self.state.lookup_class_attr(t.cls, "__getitem__")
                    if fn:
                        bound = frozenset(bind_attr(f, t.cls, t) for f in fn)
                        out |= self.apply(bound, [slc], None, {}, None, site)
                        continue
                elif isinstance(t, Builtin) and f"{t.name}.__getitem__" in STUBS:
                    out |= self.apply(frozenset([Builtin(f"{t.name}.__getitem__", t.bound, t.bound_kw)]),
                                      [slc], None, {}, None, site)
                    continue
                rest.add(t)
        finally:
            self._weak_depth -= 1
        return out | self.state.deref(frozenset(rest))

    def e_BinOp(self, e: ast.BinOp) -> Vals:
        """Evaluate `a op b` by calling `a.__op__(b)`, or `b.__rop__(a)` if `a` does not have it."""
        left = self.eval(e.left)
        right = self.eval(e.right)
        spec = BINOP_DUNDERS.get(type(e.op))
        if spec is None:
            return EMPTY
        site = make_site(self.info.id, e, "op")
        found, out = self.invoke_dunder(left, spec[0], [right], site)
        if not found:
            _, out = self.invoke_dunder(right, spec[1], [left], site)
        return out

    def e_UnaryOp(self, e: ast.UnaryOp) -> Vals:
        """Evaluate `-a`, `+a`, `~a` or `not a` by calling the matching special method.

        `not a` calls `__bool__`, or `__len__` when no class defines it, and returns `EMPTY`.
        """
        v = self.eval(e.operand)
        site = make_site(self.info.id, e, "op")
        if isinstance(e.op, ast.Not):
            found, _ = self.invoke_dunder(v, "__bool__", [], site)
            if not found:
                self.invoke_dunder(v, "__len__", [], site)
            return EMPTY
        dunder = UNARYOP_DUNDERS.get(type(e.op))
        if dunder is None:
            return EMPTY
        _, out = self.invoke_dunder(v, dunder, [], site)
        return out

    def e_Compare(self, e: ast.Compare) -> Vals:
        """Evaluate a comparison chain by calling the comparison methods; the result is `EMPTY`.

        Each `a op b` calls `a.__op__(b)`, or the reflected method of `b` when no instance has
        the first; `in` and `not in` call `b.__contains__(a)`.
        """
        left = self.eval(e.left)
        site = make_site(self.info.id, e, "op")
        for i, (op, comp) in enumerate(zip(e.ops, e.comparators)):
            if i > 0:
                self._weak_depth += 1
            try:
                right = self.eval(comp)
                if isinstance(op, (ast.In, ast.NotIn)):
                    self.invoke_dunder(right, "__contains__", [left], site)
                else:
                    spec = CMP_DUNDERS.get(type(op))
                    if spec is not None:
                        found, _ = self.invoke_dunder(left, spec[0], [right], site)
                        if not found:
                            self.invoke_dunder(right, spec[1], [left], site)
                left = right
            finally:
                if i > 0:
                    self._weak_depth -= 1
        return EMPTY

    def e_FormattedValue(self, e: ast.FormattedValue) -> Vals:
        """Evaluate an f-string field, calling the special methods that formatting it would call.

        `!r` calls `__repr__` and `!s` calls `__str__`; otherwise `__format__` is tried first.
        Each falls back to the next of `__str__` and `__repr__` when no class defines it.
        """
        v = self.eval(e.value)
        self.eval(e.format_spec)
        site = make_site(self.info.id, e, "op")
        if e.conversion == ord("r"):
            self.invoke_dunder(v, "__repr__", [], site)
        elif e.conversion == ord("s"):
            found, _ = self.invoke_dunder(v, "__str__", [], site)
            if not found:
                self.invoke_dunder(v, "__repr__", [], site)
        else:
            found, _ = self.invoke_dunder(v, "__format__", [EMPTY], site)
            if not found:
                found, _ = self.invoke_dunder(v, "__str__", [], site)
            if not found:
                self.invoke_dunder(v, "__repr__", [], site)
        return EMPTY

    def s_AugAssign(self, s: ast.AugAssign) -> None:
        """Run `target op= value`, calling `__iop__` on the target, or `__op__` when it has none.

        `+=` and `|=` also store the value's elements into the target's containers, since lists,
        sets and dicts are mutated in place. In flow mode the target keeps its old value too,
        because it still refers to the same container.
        """
        rhs = self.eval(s.value)
        lhs = self.lookup(s.target.id) if isinstance(s.target, ast.Name) else EMPTY
        spec = BINOP_DUNDERS.get(type(s.op))
        res = EMPTY
        if spec is not None and lhs:
            site = make_site(self.info.id, s.value, "op")
            found, res = self.invoke_dunder(lhs, spec[2], [rhs], site)
            if not found:
                _, res = self.invoke_dunder(lhs, spec[0], [rhs], site)
        if isinstance(s.op, (ast.Add, ast.BitOr)):  # xs += ys / s |= t / d |= e mutate in place
            self.store(s.target, lhs, self.state.deref(rhs), fallback=False)
        # flow mode keeps lhs: `xs += ys` rebinds xs to the same (mutated) container
        self.assign(s.target, (lhs if self.flow else EMPTY) | res | rhs)
