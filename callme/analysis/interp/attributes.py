"""Layer 4 of the interpreter, `AttributeLayer`: attribute reads."""
from __future__ import annotations

import ast
from abc import ABC

from ...frontend import make_site
from ...model import (
    EMPTY,
    BoundMethod,
    Builtin,
    ClassObj,
    Closure,
    Instance,
    Module,
    Property,
    Super,
    Token,
    Vals,
)
from ...stubs import BUILTIN_NAMES, METHOD_STUBS, STUBS, STUB_MODULES, STUB_PACKAGES
from .base import bind_attr
from .calls import CallLayer


class AttributeLayer(CallLayer, ABC):
    """Layer 4: attribute reads. Methods and binding, descriptors and
    properties, `__getattr__`, `super()`, module attributes and stub methods.
    """

    # fields of BaseLayer that this layer assigns
    _weak_depth: int

    def _has_instance_attr(self, inst: Instance, attr: str) -> bool:
        """Return True if `attr` may exist on `inst` without calling `__getattr__`.

        That is the case when it was stored on the instance, is defined on a class in the MRO,
        or is assigned or annotated in one of those classes' source.
        """
        if attr in self.state.inst_attrs.get(inst, {}) or self.state.has_class_attr(inst.cls, attr):
            return True
        return any(attr in self.prog.classes[c].known_attrs for c in self.state.mro(inst.cls))

    def e_Attribute(self, e: ast.Attribute) -> Vals:
        """Evaluate `obj.attr` when it is read; a store or delete target gives `EMPTY`."""
        v = self.eval(e.value)
        if not isinstance(e.ctx, ast.Load):
            return EMPTY
        return self.attribute(v, e)

    def attribute(self, v: Vals, e: ast.Attribute) -> Vals:
        """Return the value of `<receiver>.attr` for the already evaluated receiver values `v`.

        Follows Python's attribute rules on each token. On a class: descriptor `__get__`,
        classmethod and staticmethod binding, then metaclass attributes. On an instance: its
        own attribute, then class attributes bound as methods, property getters and descriptor
        `__get__`, then `__getattr__` when nothing else defines the name. On `super()`: the MRO
        after the start class, for classes and for metaclasses. On a module: its global or
        submodule of that name (`module_attr`). Builtin method names in
        `METHOD_STUBS` become their stub, bound to the receiver, when nothing else matched.
        """
        out: set[Token] = set()
        site = make_site(self.info.id, e, "attr")
        self._weak_depth += 1
        try:
            for t in v:
                if isinstance(t, Builtin):
                    full = f"{t.name}.{e.attr}"
                    if full in STUBS or full in STUB_MODULES or full in STUB_PACKAGES:
                        out.add(Builtin(full, t.bound, t.bound_kw))
                    elif t.name == "builtins" and e.attr in BUILTIN_NAMES:
                        out.add(Builtin(e.attr))
                elif isinstance(t, Module):
                    out.update(self.module_attr(t.name, e.attr))
                elif isinstance(t, Property):
                    if e.attr in ("getter", "setter", "deleter"):
                        out.add(Builtin(f"<property_{e.attr}>", (frozenset([t]),)))
                    elif e.attr == "fget":
                        out.update(t.fget)
                    elif e.attr == "fset":
                        out.update(t.fset)
                    elif e.attr == "fdel":
                        out.update(t.fdel)
                elif isinstance(t, ClassObj):
                    cls_vals = self.state.lookup_class_attr(t.cls, e.attr)
                    for attr_tok in cls_vals:
                        if isinstance(attr_tok, Instance):
                            get_fn = self.state.lookup_class_attr(attr_tok.cls, "__get__")
                            if get_fn:
                                bound = frozenset(bind_attr(f, attr_tok.cls, attr_tok) for f in get_fn)
                                out.update(self.apply(bound, [EMPTY, frozenset([t])], None, {}, None, site))
                                continue
                        out.add(bind_attr(attr_tok, t.cls, t))
                    if not self.state.has_class_attr(t.cls, e.attr):
                        for m in self.state.get_metaclasses(t.cls):
                            for attr_tok in self.state.lookup_class_attr(m, e.attr):
                                if isinstance(attr_tok, Closure):
                                    out.add(BoundMethod(attr_tok, t))
                                else:
                                    out.add(bind_attr(attr_tok, m, t))
                elif isinstance(t, Instance):
                    out.update(self.state.inst_attrs.get(t, {}).get(e.attr, EMPTY))
                    cls_vals = self.state.lookup_class_attr(t.cls, e.attr)
                    fgets = EMPTY
                    for attr_tok in cls_vals:
                        if isinstance(attr_tok, Property):
                            fgets |= attr_tok.fget
                        elif isinstance(attr_tok, Instance):
                            get_fn = self.state.lookup_class_attr(attr_tok.cls, "__get__")
                            if get_fn:
                                bound = frozenset(bind_attr(f, attr_tok.cls, attr_tok) for f in get_fn)
                                out.update(self.apply(
                                    bound, [frozenset([t]), frozenset([ClassObj(t.cls)])],
                                    None, {}, None, site))
                            else:
                                out.add(attr_tok)
                        else:
                            out.add(bind_attr(attr_tok, t.cls, t))
                    if fgets:
                        out.update(self.apply(fgets, [frozenset([t])], None, {}, None, site))
                    if not self._has_instance_attr(t, e.attr):
                        getattr_fn = self.state.lookup_class_attr(t.cls, "__getattr__")
                        if getattr_fn:
                            bound = frozenset(bind_attr(f, t.cls, t) for f in getattr_fn)
                            out.update(self.apply(bound, [EMPTY], None, {}, None, site))
                elif isinstance(t, Super):
                    if isinstance(t.receiver, ClassObj) and t.start not in self.state.mro(t.receiver.cls):
                        # Metaclass super(Meta, Cls): walk Cls's metaclass MRO after Meta
                        metas = self.state.get_metaclasses(t.receiver.cls)
                        found_any = False
                        for m in metas:
                            found = self.state.lookup_class_attr(m, e.attr, after=t.start)
                            if found:
                                found_any = True
                                for attr_tok in found:
                                    if isinstance(attr_tok, Closure):
                                        out.add(BoundMethod(attr_tok, t.receiver))
                                    else:
                                        out.add(bind_attr(attr_tok, m, t.receiver))
                        if not found_any:
                            if e.attr == "__call__":
                                out.add(Builtin("type.__call__", (frozenset([t.receiver]),)))
                            elif e.attr == "__new__":
                                out.add(Builtin("object.__new__"))
                            elif e.attr == "__setattr__":
                                out.add(Builtin("object.__setattr__", (frozenset([t.receiver]),)))
                    else:
                        found = self.state.lookup_class_attr(t.receiver.cls, e.attr, after=t.start)
                        if found:
                            for attr_tok in found:
                                out.add(bind_attr(attr_tok, t.receiver.cls, t.receiver))
                        elif e.attr == "__new__":
                            out.add(Builtin("object.__new__"))
                        elif e.attr == "__setattr__":
                            out.add(Builtin("object.__setattr__", (frozenset([t.receiver]),)))
        finally:
            self._weak_depth -= 1
        if e.attr in METHOD_STUBS and not out:  # xs.sort(key=...), d.get(k, default)
            out.add(Builtin(METHOD_STUBS[e.attr], (v,)))
        return frozenset(out)
