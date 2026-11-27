"""Layer 5 of the interpreter, `ExprLayer`: expressions."""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Final, override

from ...frontend import make_site
from ...model import EMPTY, Vals
from ..state import mk_closure
from .attributes import AttributeLayer


class ExprLayer(AttributeLayer):
    """Layer 5: expressions. `eval` dispatches to `e_<Expr>`; names, lambdas,
    calls, literals and containers, conditional and boolean expressions, `await`, `yield` and
    comprehensions.
    """

    # fields of BaseLayer that this layer assigns
    ret: Vals
    _weak_depth: int

    @override
    def eval(self, e: ast.expr | None) -> Vals:
        """Evaluate expression `e` by dispatching to `e_<ExprType>`; `None` gives `EMPTY`."""
        if e is None:
            return EMPTY
        m: Callable[[ast.expr], Vals] | None = getattr(self, "e_" + type(e).__name__, None)
        return m(e) if m else self.generic(e)

    def generic(self, e: ast.expr) -> Vals:
        """Evaluate the subexpressions of `e` for their calls, and return `EMPTY`."""
        for c in ast.iter_child_nodes(e):
            if isinstance(c, ast.expr):
                self.eval(c)
        return EMPTY

    def e_Name(self, e: ast.Name) -> Vals:
        """Return the value of a variable that is read; a store or delete target gives `EMPTY`."""
        return self.lookup(e.id) if isinstance(e.ctx, ast.Load) else EMPTY

    def e_Lambda(self, e: ast.Lambda) -> Vals:
        """Evaluate a lambda's default values, then return its closure token."""
        fid = self.prog.by_node[id(e)]
        for d in [*e.args.defaults, *[k for k in e.args.kw_defaults if k is not None]]:
            self.eval(d)
        return frozenset([mk_closure(fid, self.node)])

    def e_Call(self, e: ast.Call) -> Vals:
        """Evaluate a call: the callee, then the arguments, then the call itself through `apply`.

        For a method call `recv.m(...)` the receiver is evaluated once. If `m` resolves to
        nothing, the call may be a container method such as `xs.append(f)`, and
        `_mutate_container` records what it stores.
        """
        recv: Vals | None = None  # receiver of a method call, evaluated once
        if isinstance(e.func, ast.Attribute):
            recv = self.eval(e.func.value)
            callee = self.attribute(recv, e.func)
        else:
            callee = self.eval(e.func)
        pos: list[Vals] = []
        star: Vals | None = None  # None = no */** at the call
        kws: dict[str, Vals] = {}
        kwstar: Vals | None = None
        for a in e.args:
            if isinstance(a, ast.Starred):
                star = (star or EMPTY) | self.state.deref(self.eval(a.value))
            else:
                pos.append(self.eval(a))
        for k in e.keywords:
            v = self.eval(k.value)
            if k.arg is None:
                kwstar = (kwstar or EMPTY) | self.state.deref(v)
            else:
                kws[k.arg] = kws.get(k.arg, EMPTY) | v
        out = self.apply(callee, pos, star, kws, kwstar, make_site(self.info.id, e))
        if not callee and recv is not None and isinstance(e.func, ast.Attribute):
            out |= self._mutate_container(e.func.value, recv, e.func.attr, pos, star, kws, kwstar)
        return out

    def _mutate_container(self, target: ast.expr, recv: Vals, method: str, pos: Sequence[Vals],
                          star: Vals | None, kws: Mapping[str, Vals],
                          kwstar: Vals | None) -> Vals:
        """Record the values a container method stores into `recv`, and return the call's result.

        Handles `append`, `add`, `extend`, `insert`, `setdefault` and `update`; the stored
        values are joined into the containers with `store`. Only `setdefault` returns something:
        the container's elements plus the default. Any other method returns `EMPTY`.

        target  the receiver expression, widened when the container has no `Box`
        """
        d = self.state.deref
        added = EMPTY
        if method in ("append", "appendleft", "add"):
            added = (pos[0] if pos else EMPTY) | (star or EMPTY)
        elif method in ("extend", "extendleft"):  # takes an iterable: its elements
            added = d((pos[0] if pos else EMPTY) | (star or EMPTY))
        elif method == "insert":
            added = (pos[1] if len(pos) > 1 else EMPTY) | (star or EMPTY)
        elif method == "setdefault":
            added = ((pos[1] if len(pos) > 1 else EMPTY)
                     | kws.get("default", EMPTY) | (star or EMPTY) | (kwstar or EMPTY))
            self.store(target, recv, added)
            return d(recv) | added
        elif method == "update":
            added = d((pos[0] if pos else EMPTY) | (star or EMPTY) | (kwstar or EMPTY))
            for v in kws.values():
                added |= v
        else:
            return EMPTY
        self.store(target, recv, added)
        return EMPTY

    def e_IfExp(self, e: ast.IfExp) -> Vals:
        """Evaluate `a if c else b`: the test for its calls, then the union of both branches."""
        self.eval(e.test)
        self._weak_depth += 1
        try:
            return self.eval(e.body) | self.eval(e.orelse)
        finally:
            self._weak_depth -= 1

    def e_BoolOp(self, e: ast.BoolOp) -> Vals:
        """Evaluate `and` or `or`: the union of all operands, since any may be the result."""
        out = self.eval(e.values[0]) if e.values else EMPTY
        if len(e.values) > 1:
            self._weak_depth += 1
            try:
                for v in e.values[1:]:
                    out |= self.eval(v)
            finally:
                self._weak_depth -= 1
        return out

    def e_NamedExpr(self, e: ast.NamedExpr) -> Vals:
        """Evaluate `target := value`; the binding is weak, since it may sit in a branch."""
        v = self.eval(e.value)
        self.bind(e.target.id, v, weak=True)
        return v

    def _union(self, elts: Iterable[ast.expr]) -> Vals:
        """Return the union of the values of the expressions in `elts`."""
        out = EMPTY
        for x in elts:
            out |= self.eval(x)
        return out

    def e_List(self, e: ast.List) -> Vals:
        """Evaluate a list display: its elements plus a new list `Box`."""
        return self._union(e.elts) | self.new_box("list", e)

    def e_Set(self, e: ast.Set) -> Vals:
        """Evaluate a set display: its elements plus a new set `Box`."""
        return self._union(e.elts) | self.new_box("set", e)

    def e_Tuple(self, e: ast.Tuple) -> Vals:  # immutable: no Box needed
        """Evaluate a tuple display: just its elements, since an immutable tuple needs no `Box`."""
        return self._union(e.elts)

    def e_Dict(self, e: ast.Dict) -> Vals:
        """Evaluate a dict display: its values, the contents of `**other`, and a new `Box`."""
        out = self.new_box("dict", e)
        for k, v in zip(e.keys, e.values):
            if k is None:  # {**other}
                out |= self.state.deref(self.eval(v))
            else:
                self.eval(k)
                out |= self.eval(v)
        return out

    def e_Starred(self, e: ast.Starred) -> Vals:  # [*xs]: the elements of xs
        """Evaluate `*xs` in a display: the elements of `xs`."""
        return self.state.deref(self.eval(e.value))

    def e_Await(self, e: ast.Await) -> Vals:
        """Evaluate `await x`: call `__await__` if defined, otherwise return `x` itself."""
        v = self.eval(e.value)
        found, out = self.invoke_dunder(v, "__await__", [], make_site(self.info.id, e, "op"))
        return out if found else v

    def e_Yield(self, e: ast.Yield | ast.YieldFrom) -> Vals:
        """Evaluate `yield x` or `yield from xs`: join the yielded values into the return value.

        The function's return value thus stands for what iterating the generator produces. The
        expression itself, the value sent in, is `EMPTY`.
        """
        v = self.eval(e.value)
        self.ret |= self.state.deref(v) if isinstance(e, ast.YieldFrom) else v
        return EMPTY

    e_YieldFrom: Final = e_Yield

    def _comp(self, gens: list[ast.comprehension]) -> dict[str, Vals | None]:
        """Bind the targets of a comprehension's generators to the elements of their iterables.

        Return the targets' values from the enclosing scope, so that `_end_comp` can restore
        them in flow mode. The `if` conditions are evaluated for their calls.
        """
        saved: dict[str, Vals | None] = {}
        if self.flow:
            for g in gens:
                for n in ast.walk(g.target):
                    if isinstance(n, ast.Name):
                        saved.setdefault(n.id, self.env.get(n.id))
        for i, g in enumerate(gens):
            if i > 0:
                self._weak_depth += 1
            try:
                self.assign(g.target, self._iter_expr(g.iter, is_async=bool(g.is_async)))
                self._weak_depth += 1
                try:
                    for c in g.ifs:
                        self.eval(c)
                finally:
                    self._weak_depth -= 1
            finally:
                if i > 0:
                    self._weak_depth -= 1
        return saved

    def _end_comp(self, saved: dict[str, Vals | None]) -> None:
        """Restore the variables a comprehension rebound, since its targets have their own scope."""
        for name, vals in saved.items():
            if vals is None:
                self.env.pop(name, None)
            else:
                self.env[name] = vals

    def e_GeneratorExp(self, e: ast.ListComp | ast.SetComp | ast.GeneratorExp) -> Vals:
        """Evaluate a generator expression, or a list or set comprehension's body: its elements."""
        saved = self._comp(e.generators)
        self._weak_depth += 1
        try:
            out = self.eval(e.elt)
        finally:
            self._weak_depth -= 1
        self._end_comp(saved)
        return out

    def e_ListComp(self, e: ast.ListComp) -> Vals:
        """Evaluate a list comprehension: its elements plus a new list `Box`."""
        return self.e_GeneratorExp(e) | self.new_box("list", e)

    def e_SetComp(self, e: ast.SetComp) -> Vals:
        """Evaluate a set comprehension: its elements plus a new set `Box`."""
        return self.e_GeneratorExp(e) | self.new_box("set", e)

    def e_DictComp(self, e: ast.DictComp) -> Vals:
        """Evaluate a dict comprehension: its values plus a new dict `Box`."""
        saved = self._comp(e.generators)
        self._weak_depth += 1
        try:
            self.eval(e.key)
            out = self.eval(e.value)
        finally:
            self._weak_depth -= 1
        self._end_comp(saved)
        return out | self.new_box("dict", e)
