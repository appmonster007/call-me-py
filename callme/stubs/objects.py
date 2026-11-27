"""Stubs for the builtins of classes and objects: classmethod, staticmethod, super, property,
object, type, `type.__call__`, `object.__new__` and `object.__setattr__`."""
from __future__ import annotations

from ..model import (
    EMPTY,
    Alloc,
    ClassMethod,
    ClassObj,
    Instance,
    Property,
    Site,
    StaticMethod,
    Super,
    Vals,
)
from .registry import MAX_PARTIAL_DEPTH, Args, Host, stub


@stub("classmethod")
def _classmethod(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `classmethod(f)`: wrap each function in a `ClassMethod`, dropping too deep tokens."""
    return frozenset(
        t if isinstance(t, ClassMethod) else ClassMethod(t)
        for t in a.get(0) if isinstance(t, ClassMethod) or t.depth <= MAX_PARTIAL_DEPTH
    )


@stub("staticmethod")
def _staticmethod(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `staticmethod(f)`: wrap each function in a `StaticMethod`, dropping too deep tokens."""
    return frozenset(
        t if isinstance(t, StaticMethod) else StaticMethod(t)
        for t in a.get(0) if isinstance(t, StaticMethod) or t.depth <= MAX_PARTIAL_DEPTH
    )


@stub("super")
def _super(it: Host, a: Args, _site: Site) -> Vals:
    """Model `super()` and `super(C, obj)`: return a `Super` token per start class and receiver.

    The zero-argument form uses the class whose body defines the current function, and the
    value of its first positional parameter (`self` or `cls`). It returns `EMPTY` outside a
    method.
    """
    if not a.pos:  # 0-arg super(): enclosing class + first positional param (self / cls)
        owner = it.info.owner
        if owner is None or not it.info.sig.pos:
            return EMPTY
        recv = it.lookup(it.info.sig.pos[0])
        return frozenset(Super(owner, r) for r in recv if isinstance(r, (ClassObj, Instance)))
    starts = [t.cls for t in a.get(0) if isinstance(t, ClassObj)]
    recvs = [r for r in a.get(1) if isinstance(r, (ClassObj, Instance))]
    return frozenset(Super(s, r) for s in starts for r in recvs)


def property_fn(v: Vals) -> Vals:
    """Return the accessor functions in `v`: properties and too deep tokens are dropped."""
    return frozenset(t for t in v if not isinstance(t, Property) and t.depth <= MAX_PARTIAL_DEPTH)


@stub("property")
def _property(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `property(fget, fset, fdel)`: return one `Property` holding the three accessors."""
    return frozenset([Property(
        property_fn(a.get(0, "fget")),
        property_fn(a.get(1, "fset")),
        property_fn(a.get(2, "fdel")),
    )])


@stub("<property_getter>")
def _property_getter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `prop.getter(f)`: return a copy of each property with `f` as its getter."""
    fn = property_fn(a.get(1))
    return frozenset(Property(fn, p.fset, p.fdel) for p in a.get(0) if isinstance(p, Property))


@stub("<property_setter>")
def _property_setter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `prop.setter(f)`: return a copy of each property with `f` as its setter."""
    fn = property_fn(a.get(1))
    return frozenset(Property(p.fget, fn, p.fdel) for p in a.get(0) if isinstance(p, Property))


@stub("<property_deleter>")
def _property_deleter(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `prop.deleter(f)`: return a copy of each property with `f` as its deleter."""
    fn = property_fn(a.get(1))
    return frozenset(Property(p.fget, p.fset, fn) for p in a.get(0) if isinstance(p, Property))


@stub("object")
def _object(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `object()`: return nothing callable."""
    return EMPTY


@stub("type")
def _type(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `type(...)` as returning nothing: neither `type(x)` nor new classes are tracked."""
    return EMPTY


@stub("type.__call__")
def _type_call(it: Host, a: Args, site: Site) -> Vals:
    """Model the default metaclass call `type.__call__(cls, ...)`: instantiate each class."""
    out = EMPTY
    for tok in a.get(0):
        if isinstance(tok, ClassObj):
            out |= it.instantiate(tok, a.pos[1:], a.star, a.kws, a.kwstar, site)
    return out


@stub("object.__new__")
def _object_new(_it: Host, a: Args, site: Site) -> Vals:
    """Model `object.__new__(cls)`: return a new instance of each class, allocated at this call."""
    return frozenset(Instance(t.cls, Alloc.of(site)) for t in a.get(0) if isinstance(t, ClassObj))


@stub("object.__setattr__")
def _object_setattr(it: Host, a: Args, site: Site) -> Vals:
    """Model `object.__setattr__(obj, name, value)`, which skips a custom `__setattr__`.

    The attribute name is not tracked, so `value` goes to the setter of every data descriptor
    (a property or an object with `__set__`) found in the classes of `obj`'s MRO.
    """
    val = a.get(2)
    for obj in a.get(0):
        if isinstance(obj, Instance):
            seen: set[str] = set()
            for c in it.state.mro(obj.cls):
                for attr, toks in list(it.state.class_attrs.get(c, {}).items()):
                    if attr in seen:
                        continue
                    seen.add(attr)
                    it.invoke_data_descriptor_set(obj, toks, val, site)
    return EMPTY
