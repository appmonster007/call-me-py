"""Layer 1 of the interpreter, `NameLayer`: names and stores."""
from __future__ import annotations

import ast
from abc import ABC
from collections.abc import Mapping, Sequence

from ...frontend import make_site
from ...model import (
    EMPTY,
    Alloc,
    Box,
    Builtin,
    ClassMethod,
    ClassObj,
    Closure,
    FuncId,
    Instance,
    Module,
    Property,
    Site,
    StaticMethod,
    Vals,
)
from ...stubs import BUILTIN_NAMES, STUBS
from ..state import Env
from .base import BaseLayer, bind_attr


_LITERAL_CONTAINERS = (
    ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)


class NameLayer(BaseLayer, ABC):
    """Layer 1: names and stores. Binding and looking up variables, applying callee
    writes to enclosing scopes, attribute writes and data descriptors, assignment targets and
    container stores, and the implicit dunder calls (`invoke_dunder`, `iterate`).
    """

    # fields of BaseLayer that this layer assigns
    _weak_depth: int

    def bind(self, name: str, vals: Vals, *, weak: bool = False) -> None:
        """Bind `name` to `vals` in the current scope.

        In a class body the name becomes a class attribute; `__new__` is wrapped as a
        staticmethod and `__init_subclass__` as a classmethod, as Python does. A local is replaced
        in flow mode (a strong update) unless `weak` is set, and joined otherwise; flow mode also
        joins it into the summary and the enclosing `try` bodies. Names declared `global` or
        `nonlocal` are joined into `State.extern` of the function that owns them.
        """
        if self.class_stack:
            if name == "__new__":
                vals = frozenset(StaticMethod(t) if isinstance(t, Closure) else t for t in vals)
            elif name == "__init_subclass__":
                vals = frozenset(ClassMethod(t) if isinstance(t, Closure) else t for t in vals)
            cid, ns = self.class_stack[-1]
            ns[name] = ns.get(name, EMPTY) | vals
            if not self.readonly:
                self.state.add_class_attr(cid, name, vals)
        elif name in self.info.locals or self.info.kind == "module":
            if self.flow and not weak:  # strong update: this binding replaces the old one
                if vals:
                    self.env[name] = vals
                else:
                    self.env.pop(name, None)
            elif vals:
                self.env[name] = self.env.get(name, EMPTY) | vals
            if self.flow and vals:
                for acc in (self.summary, *self.tries):
                    acc[name] = acc.get(name, EMPTY) | vals
        elif vals and not self.readonly and name in self.info.globals:
            owner = self.prog.module_body(self.info.id)
            self.state.add_extern(owner, name, vals)
            if self.flow and self.node is not None:
                self.state.add_write(self.node, owner, name, vals)
        elif vals and not self.readonly and name in self.info.nonlocals:
            owner = self.nonlocal_owner(name)
            if owner is not None:
                self.state.add_extern(owner, name, vals)
                if self.flow and self.node is not None:
                    self.state.add_write(self.node, owner, name, vals)

    def nonlocal_owner(self, name: str) -> FuncId | None:
        """Return the nearest enclosing function with local `name`, which `nonlocal name` writes."""
        f = self.info.parent
        while f is not None and self.prog.funcs[f].kind != "module":
            if name in self.prog.funcs[f].locals:
                return f
            f = self.prog.funcs[f].parent
        return None

    def lookup(self, name: str) -> Vals:
        """Return the value of variable `name` in the current scope.

        The search follows Python's scoping. The class body being run comes first, then the
        function's locals, including writes from inner scopes in flow mode. Then come the
        enclosing functions: the summary of the context the closure was created in, or every
        context when that is unknown. Then the module, and finally a builtin with that name. An
        unresolved name gives `EMPTY`, so calling it adds no edge.
        """
        if self.class_stack and name in self.class_stack[-1][1]:
            return self.class_stack[-1][1][name]
        if name in self.info.locals:
            if self.node is None:
                return self.state.union_env(self.info.id, name)
            return self.env.get(name, EMPTY)
        if self.info.kind == "module":
            val = (self.state.union_env(self.info.id, name) if self.node is None
                   else self.env.get(name, EMPTY))
            if val:
                return val
        parent_node = self.node.ctx.parent if self.node is not None else None
        f = self.info.parent
        while f is not None:  # lexical chain: closures, then module
            pinfo = self.prog.funcs[f]
            if name in pinfo.locals:
                if parent_node is not None:
                    return self.state.envs.get(parent_node, {}).get(name, EMPTY)
                return self.state.union_env(f, name)
            if pinfo.kind == "module":
                val = (self.state.envs.get(parent_node, {}).get(name, EMPTY)
                       if parent_node is not None else self.state.union_env(f, name))
                if val:
                    return val
            parent_node = parent_node.ctx.parent if parent_node is not None else None
            f = pinfo.parent
        if name in BUILTIN_NAMES:  # not shadowed anywhere: the builtin
            return frozenset([Builtin(name)])
        return EMPTY  # unresolved

    def invoke_data_descriptor_set(self, obj: Instance, cls_vals: Vals,
                                   vals: Vals, site: Site) -> bool:
        """Call the setters of the data descriptors in `cls_vals` with `obj` and `vals`.

        A property's `fset` and the `__set__` of a descriptor instance are called. Return True
        if any setter was found, which means the value is not stored on the instance.
        """
        fsets = EMPTY
        handled = False
        self._weak_depth += 1
        try:
            for tok in cls_vals:
                if isinstance(tok, Property) and tok.fset:
                    fsets |= tok.fset
                elif isinstance(tok, Instance):
                    set_fn = self.state.lookup_class_attr(tok.cls, "__set__")
                    if set_fn:
                        bound = frozenset(bind_attr(f, tok.cls, tok) for f in set_fn)
                        self.apply(bound, [frozenset([obj]), vals], None, {}, None, site)
                        handled = True
            if fsets:
                self.apply(fsets, [frozenset([obj]), vals], None, {}, None, site)
                handled = True
        finally:
            self._weak_depth -= 1
        return handled

    def set_attr(self, objs: Vals, attr: str, vals: Vals, site: Site) -> None:
        """Store `vals` into attribute `attr` of every object in `objs`, as `obj.attr = vals` does.

        On an instance, a custom `__setattr__` is called instead if the class has one, then a
        data descriptor's setter; only when neither exists is the value joined into the instance
        attribute. On a class object it is joined into the class attribute, and on a module
        into that module's global (`State.extern`). Read-only mode writes nothing.
        """
        for t in objs:
            if isinstance(t, Instance):
                setattr_fn = self.state.lookup_class_attr(t.cls, "__setattr__")
                if setattr_fn:
                    bound = frozenset(bind_attr(f, t.cls, t) for f in setattr_fn)
                    self._weak_depth += 1
                    try:
                        self.apply(bound, [EMPTY, vals], None, {}, None, site)
                    finally:
                        self._weak_depth -= 1
                    continue
                cls_vals = self.state.lookup_class_attr(t.cls, attr)
                if not self.invoke_data_descriptor_set(t, cls_vals, vals, site):
                    if not self.readonly:
                        self.state.add_inst_attr(t, attr, vals)
            elif isinstance(t, ClassObj) and not self.readonly:
                self.state.add_class_attr(t.cls, attr, vals)
            elif isinstance(t, Module) and not self.readonly:
                body = self.prog.modules[t.name].func
                if body is not None:  # a namespace package has no globals of its own
                    self.state.add_extern(body, attr, vals)
                    if self.flow and self.node is not None:
                        self.state.add_write(self.node, body, attr, vals)

    def assign(self, t: ast.expr, vals: Vals) -> None:
        """Assign `vals` to an assignment target, following Python's forms of target.

        A name is bound; an attribute goes through `set_attr`; a subscript calls `__setitem__`
        and otherwise stores into the container. Tuple and list targets each receive the
        elements of `vals`, since unpacking reads them; `*rest` targets receive them too.
        """
        if isinstance(t, ast.Name):
            self.bind(t.id, vals)
        elif isinstance(t, ast.Attribute):
            self.set_attr(self.eval(t.value), t.attr, vals, make_site(self.info.id, t, "attr"))
        elif isinstance(t, ast.Subscript):
            slc = self.eval(t.slice)
            obj = self.eval(t.value)
            found, _ = self.invoke_dunder(obj, "__setitem__", [slc, vals],
                                          make_site(self.info.id, t, "op"))
            self.store(t.value, obj, vals, fallback=not found)
        elif isinstance(t, (ast.Tuple, ast.List)):
            elems = self.state.deref(vals)  # unpacking reads the elements
            for x in t.elts:
                self.assign(x, elems)
        elif isinstance(t, ast.Starred):
            self.assign(t.value, vals)
        else:
            self.eval(t)

    def new_box(self, kind: str, e: ast.expr) -> Vals:
        """Return a new `Box` of `kind` for the container created by expression `e`."""
        return frozenset([Box(kind, Alloc(self.info.id, e.lineno, e.col_offset))])

    def store(self, target: ast.expr, recv: Vals, vals: Vals, *, fallback: bool = True) -> None:
        """Join `vals` into the containers in `recv`, the value of `target`, as a weak update.

        Each `Box` in `recv` receives the values in `State.heap`. If `recv` holds no box (the
        container's origin is unknown) and `fallback` is set, the target expression itself is
        widened instead, as if the container were the union of its elements.
        """
        if not vals:
            return
        boxes = [t for t in recv if isinstance(t, Box)]
        if boxes:
            if not self.readonly:
                for box in boxes:
                    self.state.add_heap(box, vals)
        elif fallback and isinstance(target, (ast.Name, ast.Attribute, ast.Subscript)):
            if isinstance(target, ast.Name):
                self.bind(target.id, vals, weak=True)
            else:
                self.assign(target, vals)

    def invoke_dunder(self, recv: Vals, name: str, pos: Sequence[Vals],
                      site: Site) -> tuple[bool, Vals]:
        """Call special method `name` on every instance or stub object in `recv` that defines it.

        Return whether any token defined it, and the union of the results. A `Builtin` token
        matches when `<tok.name>.<name>` is registered in `STUBS`.
        """
        found = False
        out = EMPTY
        self._weak_depth += 1
        try:
            for t in recv:
                if isinstance(t, Instance):
                    fn = self.state.lookup_class_attr(t.cls, name)
                    if fn:
                        found = True
                        bound = frozenset(bind_attr(f, t.cls, t) for f in fn)
                        out |= self.apply(bound, pos, None, {}, None, site)
                elif isinstance(t, Builtin) and f"{t.name}.{name}" in STUBS:
                    found = True
                    out |= self.apply(frozenset([Builtin(f"{t.name}.{name}", t.bound, t.bound_kw)]),
                                      pos, None, {}, None, site)
        finally:
            self._weak_depth -= 1
        return found, out

    def iterate(self, iterable: Vals, site: Site, *, is_async: bool = False) -> Vals:
        """Return the elements produced by iterating over `iterable`, as a `for` loop does.

        An instance whose class defines `__iter__` has it called, then `__next__` on the result;
        with `is_async`, `__aiter__` and `__anext__` are used instead. Any other value stands for
        its own elements and is returned with its containers' contents.
        """
        iter_name = "__aiter__" if is_async else "__iter__"
        next_name = "__anext__" if is_async else "__next__"
        out = EMPTY
        self._weak_depth += 1
        try:
            for t in iterable:
                if isinstance(t, Instance):
                    it_fn = self.state.lookup_class_attr(t.cls, iter_name)
                    if it_fn:
                        bound = frozenset(bind_attr(f, t.cls, t) for f in it_fn)
                        iters = self.apply(bound, [], None, {}, None, site)
                        _, elem = self.invoke_dunder(iters, next_name, [], site)
                        out |= elem
                        continue
                out |= self.state.deref(frozenset([t]))
        finally:
            self._weak_depth -= 1
        return out

    def _iter_expr(self, expr: ast.expr, *, is_async: bool = False) -> Vals:
        """Evaluate `expr` and return its elements; literal containers are read directly."""
        v = self.eval(expr)
        if isinstance(expr, _LITERAL_CONTAINERS):
            return self.state.deref(v)
        return self.iterate(v, make_site(self.info.id, expr, "iter"), is_async=is_async)

    def _closure_owns_local(self, tok: Closure, name: str) -> bool:
        """Return True if `nonlocal name` in closure `tok` refers to `self.node`'s own local."""
        f = self.prog.funcs[tok.func].parent
        pnode = tok.parent
        while f is not None and self.prog.funcs[f].kind != "module":
            if name in self.prog.funcs[f].locals:
                return f == self.info.id and pnode is not None and pnode == self.node
            f = self.prog.funcs[f].parent
            pnode = pnode.ctx.parent if pnode is not None else None
        return False

    def _must_kills_for_closure(self, tok: Closure) -> frozenset[str]:
        """Return locals of `self.info.id` that closure `tok` definitely overwrites on return."""
        finfo = self.prog.funcs[tok.func]
        if not finfo.must_writes:
            return frozenset()
        kills: set[str] = set()
        for name in finfo.must_writes:
            if name in finfo.globals:
                if self.prog.module_body(tok.func) == self.info.id:
                    kills.add(name)
            elif name in finfo.nonlocals and self._closure_owns_local(tok, name):
                kills.add(name)
        return frozenset(kills)

    def _apply_writes(self, writes: Mapping[FuncId, Env],
                      kills: frozenset[str] | None = None) -> None:
        """Apply callee side-effect `writes` to `self.env` (strong for `kills`) and propagate.

        Writes targeting `self.info.id` update `self.env`, `self.summary` and `self.tries`.
        Writes targeting other scopes—or targeting `self.info.id` without a guaranteed kill on a
        non-module function, which may belong to an outer recursive activation—are propagated to
        `State.writes[self.node]`.
        """
        if kills is None:
            kills = frozenset()
        own = writes.get(self.info.id)
        if self.flow:
            for name in kills:
                vals = own.get(name, EMPTY) if own else EMPTY
                if vals:
                    self.env[name] = vals
                    for acc in (self.summary, *self.tries):
                        acc[name] = acc.get(name, EMPTY) | vals
                else:
                    self.env.pop(name, None)
            if own:
                for name, vals in own.items():
                    if name not in kills and vals:
                        self.env[name] = self.env.get(name, EMPTY) | vals
                        for acc in (self.summary, *self.tries):
                            acc[name] = acc.get(name, EMPTY) | vals
        if self.flow and not self.readonly and self.node is not None:
            for owner, env in writes.items():
                if owner == self.info.id:
                    if self.info.kind != "module":
                        for name, vals in env.items():
                            if name not in kills:
                                self.state.add_write(self.node, owner, name, vals)
                else:
                    for name, vals in env.items():
                        self.state.add_write(self.node, owner, name, vals)
