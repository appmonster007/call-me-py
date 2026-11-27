"""Stubs for `dataclasses`: the decorator, `field`, the generated `__init__`, `replace`,
`asdict`, `astuple` and `make_dataclass`."""
from __future__ import annotations

from ..model import (
    EMPTY,
    Alloc,
    BoundMethod,
    Box,
    Builtin,
    ClassMethod,
    ClassObj,
    Instance,
    Site,
    StaticMethod,
    Vals,
)
from .registry import Args, Host, call, shallow, stub, union


def _apply_dataclass(it: Host, cls_vals: Vals) -> Vals:
    """Give each class in `cls_vals` the generated dataclass `__init__`, and return the classes.

    A class that defines its own `__init__` keeps it. Nothing is written in read-only mode.
    """
    for t in cls_vals:
        if isinstance(t, ClassObj) and not it.readonly:
            if "__init__" not in it.state.class_attrs.get(t.cls, {}):
                it.state.add_class_attr(t.cls, "__init__", frozenset([Builtin("<dataclass_init>")]))
    return cls_vals


@stub("dataclasses.dataclass")
def _dataclass(it: Host, a: Args, _site: Site) -> Vals:
    """Model `dataclasses.dataclass` used bare or with options.

    `@dataclass` applied to a class turns it into a dataclass at once. `@dataclass(...)` with
    options returns the `<dataclass>` decorator, which does the same when it is applied.
    """
    cls = a.pos[0] if a.pos else EMPTY
    if any(isinstance(t, ClassObj) for t in cls):
        return _apply_dataclass(it, cls)
    return frozenset([Builtin("<dataclass>")])


@stub("<dataclass>")
def _dataclass_deco(it: Host, a: Args, _site: Site) -> Vals:
    """Apply the `<dataclass>` decorator returned by `dataclass(...)` to a class."""
    return _apply_dataclass(it, a.get(0))


@stub("dataclasses.field")
def _dataclass_field(_it: Host, a: Args, _site: Site) -> Vals:
    """Model `dataclasses.field(default=..., default_factory=...)`: record factory and default."""
    factory = shallow(a.kw("default_factory"))
    default = shallow(a.kw("default"))
    if not factory and not default:
        return EMPTY
    return frozenset([Builtin("<dataclass_field>", (factory, default))])


@stub("<dataclass_init>")
def _dataclass_init(it: Host, a: Args, site: Site) -> Vals:
    """Model a generated dataclass `__init__`: run field factories, then call `__post_init__`.

    Each `field(default_factory=...)` in the class's MRO has its factory invoked and the result
    joined into that instance attribute. `__post_init__` is then bound as Python would bind it:
    to the class for a classmethod, unbound for a staticmethod, and to the instance otherwise.
    """
    for obj in a.get(0):
        if isinstance(obj, Instance):
            seen: set[str] = set()
            for c in it.state.mro(obj.cls):
                for attr, toks in list(it.state.class_attrs.get(c, {}).items()):
                    if attr in seen:
                        continue
                    seen.add(attr)
                    for tok in toks:
                        if isinstance(tok, Builtin) and tok.name == "<dataclass_field>":
                            val = call(it, tok.bound[0], [], site) | tok.bound[1]
                            if val and not it.readonly:
                                it.state.add_inst_attr(obj, attr, val)
            post = it.state.lookup_class_attr(obj.cls, "__post_init__")
            if post:
                bound = frozenset(
                    BoundMethod(t.func, ClassObj(obj.cls)) if isinstance(t, ClassMethod)
                    else t.func if isinstance(t, StaticMethod)
                    else BoundMethod(t, obj)
                    for t in post
                )
                it.apply(bound, [], None, {}, None, site)
    return EMPTY


@stub("dataclasses.replace")
def _dataclass_replace(it: Host, a: Args, site: Site) -> Vals:
    """Model `dataclasses.replace(obj, **changes)`: re-instantiate `obj`'s class with updates."""
    objs = a.get(0)
    out = objs
    kwstar = union(a.kws.values()) | (a.kwstar or EMPTY)
    for obj in objs:
        if isinstance(obj, Instance):
            out |= it.instantiate(ClassObj(obj.cls), [], None, a.kws, kwstar, site)
    return out


@stub("dataclasses.asdict")
def _dataclass_asdict(it: Host, a: Args, site: Site) -> Vals:
    """Model `dataclasses.asdict(obj, dict_factory=dict)`: invoke `dict_factory` if supplied."""
    factory = a.get(1, "dict_factory")
    if factory:
        return call(it, factory, [EMPTY], site)
    return frozenset([Box("dict", Alloc.of(site))])


@stub("dataclasses.astuple")
def _dataclass_astuple(it: Host, a: Args, site: Site) -> Vals:
    """Model `dataclasses.astuple(obj, tuple_factory=tuple)`: invoke `tuple_factory` if supplied."""
    factory = a.get(1, "tuple_factory")
    if factory:
        return call(it, factory, [EMPTY], site)
    return EMPTY


@stub("dataclasses.make_dataclass")
def _make_dataclass(_it: Host, _a: Args, _site: Site) -> Vals:
    """Model `dataclasses.make_dataclass(...)`: dynamic class creation is not tracked."""
    return EMPTY
