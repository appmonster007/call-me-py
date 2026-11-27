"""Layer 8 of the interpreter, `DefLayer`: definitions."""
from __future__ import annotations

import ast
from typing import Final

from ...frontend import make_site
from ...model import EMPTY, ClassId, ClassObj, Site, Vals, token_label
from ..state import mk_closure
from .base import bind_attr
from .statements import StmtLayer


class DefLayer(StmtLayer):
    """Layer 8: definitions. `def` and `class` statements, and decorators.
    """

    def _apply_decorator(self, dec: ast.expr, callee: Vals, vals: Vals) -> Vals:
        """Apply decorator `dec` with evaluated value `callee` to `vals` and return the result.

        When `callee` is non-empty, it is called at the decorator site. When `callee` is empty,
        it is treated as the identity only if `(self.node, site)` has already been confirmed
        unresolved in `State.unknown_decos`; otherwise `site` is recorded in `empty_decos` and
        `EMPTY` is returned so that decorator factories whose return value has not converged yet
        do not leak spurious identity bindings on early rounds.
        """
        site = make_site(self.info.id, dec, "decorator")
        if callee:
            self.empty_decos.discard(site)
            return self.apply(callee, [vals], None, {}, None, site)
        if self.node is not None and (self.node, site) in self.state.unknown_decos:
            return vals  # confirmed unresolved decorator: assume identity
        self.empty_decos.add(site)
        return EMPTY

    def s_FunctionDef(self, s: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Run a def: evaluate its defaults, create its closure, apply its decorators, bind it.

        Decorators are applied from the bottom one up, each at its own decorator site. A
        decorator that remains unresolved after the fixpoint converges is assumed to be the
        identity, so the function keeps its own value (`_apply_decorator`).
        """
        fid = self.prog.by_node[id(s)]
        for d in [*s.args.defaults, *[k for k in s.args.kw_defaults if k is not None]]:
            self.eval(d)
        vals: Vals = frozenset([mk_closure(fid, self.node)])
        for dec in reversed(s.decorator_list):
            vals = self._apply_decorator(dec, self.eval(dec), vals)
        self.bind(s.name, vals)

    s_AsyncFunctionDef: Final = s_FunctionDef

    def s_ClassDef(self, s: ast.ClassDef) -> None:
        """Run a class statement and bind the name to the class object, as Python does.

        The decorators, bases and keywords are evaluated first; the bases and the `metaclass`
        keyword are recorded in `State`. The body then runs with the class namespace on
        `class_stack`, so its bindings become class attributes. Then, at an implicit "class"
        site, the first `__init_subclass__` of a base is called with the other keywords, and
        each metaclass's `__new__` and `__init__` are called with the bases and the namespace
        values. Last, the decorators are applied from the bottom one up; an unknown decorator
        is assumed to be the identity.
        """
        cid = self.prog.class_by_node[id(s)]
        decs = [self.eval(dec) for dec in s.decorator_list]
        base_ids: list[ClassId] = []
        bases_val = EMPTY
        for b in s.bases:
            bv = self.eval(b)
            bases_val |= bv
            for t in sorted(bv, key=token_label):
                if isinstance(t, ClassObj):
                    base_ids.append(t.cls)
        meta_vals = EMPTY
        extra_kws: dict[str, Vals] = {}
        extra_kwstar: Vals | None = None
        for k in s.keywords:
            kv = self.eval(k.value)
            if k.arg == "metaclass":
                meta_vals |= kv
            elif k.arg is None:
                extra_kwstar = (extra_kwstar or EMPTY) | kv
            else:
                extra_kws[k.arg] = extra_kws.get(k.arg, EMPTY) | kv
        if not self.readonly:
            if base_ids:
                self.state.add_bases(cid, base_ids)
            for t in meta_vals:
                if isinstance(t, ClassObj):
                    self.state.add_metaclass(cid, t.cls)
        self.class_stack.append((cid, {}))
        self.block(s.body)
        _, ns = self.class_stack.pop()
        ns_val = EMPTY
        for v in ns.values():
            ns_val |= v

        cls_obj = ClassObj(cid)
        cls_site = Site(self.info.id, s.lineno, s.col_offset, f"class {s.name}", "class")
        init_sub = self.state.lookup_class_attr(cid, "__init_subclass__", after=cid)
        if init_sub:
            bound_sub = frozenset(bind_attr(t, cid, cls_obj) for t in init_sub)
            self.apply(bound_sub, [], None, extra_kws, extra_kwstar, cls_site)
        for meta_cid in sorted(self.state.get_metaclasses(cid), key=lambda c: c.uid):
            meta_new = self.state.lookup_class_attr(meta_cid, "__new__")
            if meta_new:
                bound_new = frozenset(bind_attr(t, meta_cid, ClassObj(meta_cid)) for t in meta_new)
                self.apply(bound_new, [frozenset([ClassObj(meta_cid)]), EMPTY, bases_val, ns_val],
                           None, extra_kws, extra_kwstar, cls_site)
            meta_init = self.state.lookup_class_attr(meta_cid, "__init__")
            if meta_init:
                self.apply(meta_init, [frozenset([cls_obj]), EMPTY, bases_val, ns_val],
                           None, extra_kws, extra_kwstar, cls_site)

        vals: Vals = frozenset([cls_obj])
        for dec, callee in reversed(list(zip(s.decorator_list, decs))):
            vals = self._apply_decorator(dec, callee, vals)
        self.bind(s.name, vals)
