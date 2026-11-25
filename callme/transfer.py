"""Abstract interpretation of one function body in one context.

An abstract value is a set of tokens ("which functions may this expression
be?"): `Closure`s for user code, `Builtin`s for callables modelled in stubs.py. Containers are collapsed to the union of their elements.
Calls are context sensitive, with the callee's abstract entry state as context:

  entering  {param_i -> eval(arg_i)} becomes the callee's context
  returning the call evaluates to the callee's return value for that context
"""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import final

from .extract import FuncInfo, Program, make_site
from .model import (
    EMPTY,
    BoundMethod,
    Builtin,
    ClassId,
    ClassMethod,
    ClassObj,
    Closure,
    Ctx,
    Edge,
    FuncId,
    Instance,
    Node,
    ParamState,
    Property,
    Site,
    StaticMethod,
    Super,
    Token,
    Vals,
    token_label,
)
from .stubs import BUILTIN_NAMES, METHOD_STUBS, STUB_MODULES, STUBS, Args

Env = dict[str, Vals]
Calls = list[tuple[Site, Node]]

__all__ = ["EMPTY", "Vals", "Env", "State", "Interp", "analyze"]
MAX_DEPTH = 3  # nesting of captured contexts before widening to "unknown parent"

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
_LITERAL_CONTAINERS = (
    ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)


class State:
    """Global fixpoint state: contexts, local envs, return values, call edges."""

    max_ctx: int
    order: list[Node]
    by_func: dict[FuncId, set[Node]]
    envs: dict[Node, Env]
    ret: dict[Node, Vals]  # return value per node
    edges: set[Edge]
    entry: dict[Node, Env]  # entry states of widened contexts
    class_attrs: dict[ClassId, Env]  # class-level attribute table per class
    inst_attrs: dict[ClassId, Env]  # instance-level attribute table per class
    bases: dict[ClassId, tuple[ClassId, ...]]  # direct user base classes per class
    metaclasses: dict[ClassId, set[ClassId]]  # direct user metaclass(es) per class
    _mro_cache: dict[ClassId, tuple[ClassId, ...]]
    changed: bool

    def __init__(self, max_ctx: int = 16) -> None:
        self.max_ctx = max_ctx
        self.order = []
        self.by_func = {}
        self.envs = {}
        self.ret = {}
        self.edges = set()
        self.entry = {}
        self.class_attrs = {}
        self.inst_attrs = {}
        self.bases = {}
        self.metaclasses = {}
        self._mro_cache = {}
        self.changed = False

    def add_class_attr(self, cls: ClassId, name: str, vals: Vals) -> None:
        attrs = self.class_attrs.setdefault(cls, {})
        if name not in attrs:
            attrs[name] = vals
            self.changed = True
        elif not vals <= attrs[name]:
            attrs[name] = attrs[name] | vals
            self.changed = True

    def add_inst_attr(self, cls: ClassId, name: str, vals: Vals) -> None:
        attrs = self.inst_attrs.setdefault(cls, {})
        if name not in attrs:
            attrs[name] = vals
            self.changed = True
        elif not vals <= attrs[name]:
            attrs[name] = attrs[name] | vals
            self.changed = True

    def add_bases(self, cls: ClassId, new_bases: Sequence[ClassId]) -> None:
        old = self.bases.get(cls, ())
        merged = list(old)
        for b in new_bases:
            if b != cls and b not in merged:
                merged.append(b)
        tup = tuple(merged)
        if tup != old:
            self.bases[cls] = tup
            self._mro_cache.clear()
            self.changed = True

    def add_metaclass(self, cls: ClassId, meta: ClassId) -> None:
        if meta == cls:
            return
        group = self.metaclasses.setdefault(cls, set())
        if meta not in group:
            group.add(meta)
            self.changed = True

    def get_metaclasses(self, cls: ClassId) -> frozenset[ClassId]:
        for c in self.mro(cls):
            ms = self.metaclasses.get(c)
            if ms:
                return frozenset(ms)
        return frozenset()

    def mro(self, cls: ClassId) -> tuple[ClassId, ...]:
        cached = self._mro_cache.get(cls)
        if cached is not None:
            return cached
        res = self._compute_mro(cls, set())
        self._mro_cache[cls] = res
        return res

    def _compute_mro(self, cls: ClassId, visiting: set[ClassId]) -> tuple[ClassId, ...]:
        if cls in visiting:
            return ()
        bases = [b for b in self.bases.get(cls, ()) if b not in visiting]
        if not bases:
            return (cls,)
        visiting.add(cls)
        seqs = [list(self._compute_mro(b, visiting)) for b in bases]
        visiting.remove(cls)
        seqs.append(list(bases))
        out: list[ClassId] = [cls]
        while True:
            seqs = [s for s in seqs if s]
            if not seqs:
                return tuple(out)
            cand: ClassId | None = None
            for s in seqs:
                h = s[0]
                if not any(h in other[1:] for other in seqs):
                    cand = h
                    break
            if cand is None:  # inconsistent hierarchy: safe DFS fallback
                cand = seqs[0][0]
            if cand not in out:
                out.append(cand)
            for s in seqs:
                if s and s[0] == cand:
                    s.pop(0)

    def lookup_class_attr(self, cls: ClassId, name: str, *, after: ClassId | None = None) -> Vals:
        order = self.mro(cls)
        if after is not None:
            try:
                order = order[order.index(after) + 1:]
            except ValueError:
                return EMPTY
        for c in order:
            attrs = self.class_attrs.get(c)
            if attrs is not None and name in attrs:
                return attrs[name]
        return EMPTY

    def has_class_attr(self, cls: ClassId, name: str) -> bool:
        return any(name in self.class_attrs.get(c, {}) for c in self.mro(cls))

    def register(self, node: Node) -> None:
        group = self.by_func.setdefault(node.func, set())
        if node not in group:
            group.add(node)
            self.order.append(node)
            self.changed = True

    def resolve(self, func: FuncId, params: ParamState, parent: Node | None) -> Node:
        node = Node(func, Ctx(params, parent))
        group = self.by_func.setdefault(func, set())
        if node in group:
            return node
        if len(group) < self.max_ctx:
            self.register(node)
            return node
        merged = Node(func, Ctx((), None, True))  # too many contexts: widen
        self.register(merged)
        old = self.entry.get(merged, {})
        new = dict(old)
        for name, toks in params:
            new[name] = new.get(name, EMPTY) | toks
        if new != old:
            self.entry[merged] = new
            self.changed = True
        return merged

    def union_env(self, func: FuncId, name: str) -> Vals:
        out = EMPTY
        for n in self.by_func.get(func, ()):
            out |= self.envs.get(n, {}).get(name, EMPTY)
        return out


def mk_closure(func: FuncId, node: Node | None) -> Closure:
    if node is None:
        return Closure(func, None)
    depth = 1 + max((t.depth for _, ts in node.ctx.params for t in ts), default=0)
    if depth > MAX_DEPTH:
        return Closure(func, None)
    return Closure(func, node, depth)


def _bind_attr(tok: Token, owner_cls: ClassId, receiver: ClassObj | Instance) -> Token:
    """Apply Python's descriptor binding when reading `tok` via `receiver`."""
    if isinstance(tok, ClassMethod):
        return BoundMethod(tok.func, ClassObj(owner_cls))
    if isinstance(tok, StaticMethod):
        return tok.func
    if isinstance(receiver, Instance) and (
        isinstance(tok, Closure) or (isinstance(tok, Builtin) and tok.name == "<dataclass_init>")
    ):
        return BoundMethod(tok, receiver)
    return tok


@final
class Interp:
    prog: Program
    state: State
    node: Node | None
    readonly: bool
    info: FuncInfo
    env: Env
    ret: Vals
    calls: Calls
    class_stack: list[tuple[ClassId, Env]]

    def __init__(self, prog: Program, state: State, node: Node | None, *,
                 readonly: bool = False, func: FuncId | None = None) -> None:
        self.prog, self.state, self.node, self.readonly = prog, state, node, readonly
        fid = node.func if node is not None else func
        assert fid is not None, "Interp needs a node or a function"
        self.info = prog.funcs[fid]
        self.env = {}
        self.ret = EMPTY
        self.calls = []
        self.class_stack = []

    # ------------------------------------------------------------ names
    def bind(self, name: str, vals: Vals) -> None:
        if self.class_stack:
            if name == "__new__":
                vals = frozenset(StaticMethod(t) if isinstance(t, Closure) else t for t in vals)
            elif name == "__init_subclass__":
                vals = frozenset(ClassMethod(t) if isinstance(t, Closure) else t for t in vals)
            cid, ns = self.class_stack[-1]
            ns[name] = ns.get(name, EMPTY) | vals
            if not self.readonly:
                self.state.add_class_attr(cid, name, vals)
        elif vals and name in self.info.locals:
            self.env[name] = self.env.get(name, EMPTY) | vals

    def lookup(self, name: str) -> Vals:
        if self.class_stack and name in self.class_stack[-1][1]:
            return self.class_stack[-1][1][name]
        if name in self.info.locals:
            if self.node is not None:
                return self.env.get(name, EMPTY)
            return self.state.union_env(self.info.id, name)
        parent_node = self.node.ctx.parent if self.node is not None else None
        f = self.info.parent
        while f is not None:  # lexical chain: closures, then module
            pinfo = self.prog.funcs[f]
            if name in pinfo.locals:
                if parent_node is not None:
                    return self.state.envs.get(parent_node, {}).get(name, EMPTY)
                return self.state.union_env(f, name)
            parent_node = parent_node.ctx.parent if parent_node is not None else None
            f = pinfo.parent
        if name in BUILTIN_NAMES:  # not shadowed anywhere: the builtin
            return frozenset([Builtin(name)])
        return EMPTY  # unresolved

    def invoke_data_descriptor_set(self, obj: Instance, cls_vals: Vals,
                                   vals: Vals, site: Site) -> bool:
        """Invoke `Property.fset` or descriptor `__set__` in `cls_vals`; return True if any fired."""
        fsets = EMPTY
        handled = False
        for tok in cls_vals:
            if isinstance(tok, Property) and tok.fset:
                fsets |= tok.fset
            elif isinstance(tok, Instance):
                set_fn = self.state.lookup_class_attr(tok.cls, "__set__")
                if set_fn:
                    bound = frozenset(_bind_attr(f, tok.cls, tok) for f in set_fn)
                    self.apply(bound, [frozenset([obj]), vals], None, {}, None, site)
                    handled = True
        if fsets:
            self.apply(fsets, [frozenset([obj]), vals], None, {}, None, site)
            handled = True
        return handled

    def set_attr(self, objs: Vals, attr: str, vals: Vals, site: Site) -> None:
        for t in objs:
            if isinstance(t, Instance):
                setattr_fn = self.state.lookup_class_attr(t.cls, "__setattr__")
                if setattr_fn:
                    bound = frozenset(_bind_attr(f, t.cls, t) for f in setattr_fn)
                    self.apply(bound, [EMPTY, vals], None, {}, None, site)
                    continue
                cls_vals = self.state.lookup_class_attr(t.cls, attr)
                if not self.invoke_data_descriptor_set(t, cls_vals, vals, site):
                    if not self.readonly:
                        self.state.add_inst_attr(t.cls, attr, vals)
            elif isinstance(t, ClassObj) and not self.readonly:
                self.state.add_class_attr(t.cls, attr, vals)

    def assign(self, t: ast.expr, vals: Vals) -> None:
        if isinstance(t, ast.Name):
            self.bind(t.id, vals)
        elif isinstance(t, ast.Attribute):
            self.set_attr(self.eval(t.value), t.attr, vals, make_site(self.info.id, t, "attr"))
        elif isinstance(t, ast.Subscript):
            slc = self.eval(t.slice)
            obj = self.eval(t.value)
            found, _ = self.invoke_dunder(obj, "__setitem__", [slc, vals],
                                          make_site(self.info.id, t, "op"))
            if not found and isinstance(t.value, (ast.Name, ast.Attribute, ast.Subscript)):
                self.assign(t.value, vals)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for x in t.elts:
                self.assign(x, vals)
        elif isinstance(t, ast.Starred):
            self.assign(t.value, vals)
        else:
            self.eval(t)

    def invoke_dunder(self, recv: Vals, name: str, pos: Sequence[Vals],
                      site: Site) -> tuple[bool, Vals]:
        """Invoke special method `name` on any `Instance` in `recv` whose MRO defines it."""
        found = False
        out = EMPTY
        for t in recv:
            if isinstance(t, Instance):
                fn = self.state.lookup_class_attr(t.cls, name)
                if fn:
                    found = True
                    bound = frozenset(_bind_attr(f, t.cls, t) for f in fn)
                    out |= self.apply(bound, pos, None, {}, None, site)
        return found, out

    def iterate(self, iterable: Vals, site: Site, *, is_async: bool = False) -> Vals:
        """Drive `__iter__`/`__next__` (or async variants) on instances; pass through others."""
        iter_name = "__aiter__" if is_async else "__iter__"
        next_name = "__anext__" if is_async else "__next__"
        out = EMPTY
        for t in iterable:
            if isinstance(t, Instance):
                it_fn = self.state.lookup_class_attr(t.cls, iter_name)
                if it_fn:
                    bound = frozenset(_bind_attr(f, t.cls, t) for f in it_fn)
                    iters = self.apply(bound, [], None, {}, None, site)
                    _, elem = self.invoke_dunder(iters, next_name, [], site)
                    out |= elem
                    continue
            out |= frozenset([t])
        return out

    def _iter_expr(self, expr: ast.expr, *, is_async: bool = False) -> Vals:
        v = self.eval(expr)
        if isinstance(expr, _LITERAL_CONTAINERS):
            return v
        return self.iterate(v, make_site(self.info.id, expr, "iter"), is_async=is_async)

    # ------------------------------------------------------------ calls
    def default_value(self, tok: Closure, d: ast.expr) -> Vals:
        if tok.parent is not None:
            it = Interp(self.prog, self.state, tok.parent, readonly=True)
            it.env = dict(self.state.envs.get(tok.parent, {}))
        else:
            it = Interp(self.prog, self.state, None, readonly=True,
                        func=self.prog.funcs[tok.func].parent)
        return it.eval(d)

    def bind_args(self, tok: Closure, pos: Sequence[Vals], star: Vals | None,
                  kws: Mapping[str, Vals], kwstar: Vals | None) -> ParamState | None:
        sig = self.prog.funcs[tok.func].sig
        vals: Env = {}
        assigned: set[str] = set()

        def put(n: str, v: Vals) -> None:
            vals[n] = vals.get(n, EMPTY) | v

        for i, v in enumerate(pos):
            if i < len(sig.pos):
                put(sig.pos[i], v)
                assigned.add(sig.pos[i])
            elif sig.vararg:
                put(sig.vararg, v)
            else:
                return None  # too many positional arguments
        if star is not None:
            for n in sig.pos[len(pos):]:
                put(n, star)
            if sig.vararg:
                put(sig.vararg, star)
        names = set(sig.pos) | set(sig.kwonly)
        for k, v in kws.items():
            if k in names:
                if k in assigned:
                    return None
                put(k, v)
                assigned.add(k)
            elif sig.kwarg:
                put(sig.kwarg, v)
            else:
                return None
        if kwstar is not None:
            for n in names - assigned:
                put(n, kwstar)
            if sig.kwarg:
                put(sig.kwarg, kwstar)
        if any(n not in assigned for n in sig.required) and not (star is not None or kwstar is not None):
            return None  # missing required argument
        for n, d in sig.defaults:
            if n not in assigned and not isinstance(d, ast.Constant):
                put(n, self.default_value(tok, d))
        return tuple(sorted(((n, frozenset(v)) for n, v in vals.items() if v),
                            key=lambda p: p[0]))

    def instantiate(self, tok: ClassObj, pos: Sequence[Vals], star: Vals | None,
                    kws: Mapping[str, Vals], kwstar: Vals | None, site: Site) -> Vals:
        """Default `type.__call__`: run `__new__` then `__init__` on `tok`."""
        new_fn = self.state.lookup_class_attr(tok.cls, "__new__")
        if new_fn:
            bound_new = frozenset(_bind_attr(t, tok.cls, tok) for t in new_fn)
            created = self.apply(bound_new, [frozenset([tok]), *pos], star, kws, kwstar, site)
        else:
            created = frozenset([Instance(tok.cls)])
        for obj in created:
            if isinstance(obj, Instance) and tok.cls in self.state.mro(obj.cls):
                init = self.state.lookup_class_attr(obj.cls, "__init__")
                if init:
                    bound_init = frozenset(_bind_attr(t, obj.cls, obj) for t in init)
                    self.apply(bound_init, pos, star, kws, kwstar, site)
        return created

    def apply(self, callee: Vals, pos: Sequence[Vals], star: Vals | None,
              kws: Mapping[str, Vals], kwstar: Vals | None, site: Site) -> Vals:
        out = EMPTY
        for tok in callee:
            if isinstance(tok, Builtin):
                fn = STUBS.get(tok.name)
                if fn is not None:
                    args = Args([*tok.bound, *pos], star, {**dict(tok.bound_kw), **kws}, kwstar)
                    out |= fn(self, args, site)
                continue
            if isinstance(tok, BoundMethod):
                out |= self.apply(frozenset([tok.func]), [frozenset([tok.receiver]), *pos],
                                  star, kws, kwstar, site)
                continue
            if isinstance(tok, ClassObj):
                metas = self.state.get_metaclasses(tok.cls)
                meta_calls: set[Token] = set()
                for m in metas:
                    for fn in self.state.lookup_class_attr(m, "__call__"):
                        if isinstance(fn, Closure):
                            meta_calls.add(BoundMethod(fn, tok))
                        else:
                            meta_calls.add(_bind_attr(fn, m, tok))
                if meta_calls:
                    out |= self.apply(frozenset(meta_calls), pos, star, kws, kwstar, site)
                else:
                    out |= self.instantiate(tok, pos, star, kws, kwstar, site)
                continue
            if isinstance(tok, StaticMethod):
                out |= self.apply(frozenset([tok.func]), pos, star, kws, kwstar, site)
                continue
            if isinstance(tok, Instance):
                call_fn = self.state.lookup_class_attr(tok.cls, "__call__")
                if call_fn:
                    bound_call = frozenset(_bind_attr(t, tok.cls, tok) for t in call_fn if t != tok)
                    out |= self.apply(bound_call, pos, star, kws, kwstar, site)
                continue
            if isinstance(tok, (ClassMethod, Super, Property)):
                continue
            params = self.bind_args(tok, pos, star, kws, kwstar)
            if params is None:
                continue
            if self.readonly:
                target = Node(tok.func, Ctx(params, tok.parent))
            else:
                target = self.state.resolve(tok.func, params, tok.parent)
                self.calls.append((site, target))
            out |= self.state.ret.get(target, EMPTY)
        return out

    # ------------------------------------------------------------ expressions
    def eval(self, e: ast.expr | None) -> Vals:
        if e is None:
            return EMPTY
        m: Callable[[ast.expr], Vals] | None = getattr(self, "e_" + type(e).__name__, None)
        return m(e) if m else self.generic(e)

    def generic(self, e: ast.expr) -> Vals:
        for c in ast.iter_child_nodes(e):
            if isinstance(c, ast.expr):
                self.eval(c)
        return EMPTY

    def e_Name(self, e: ast.Name) -> Vals:
        return self.lookup(e.id) if isinstance(e.ctx, ast.Load) else EMPTY

    def e_Lambda(self, e: ast.Lambda) -> Vals:
        fid = self.prog.by_node[id(e)]
        for d in [*e.args.defaults, *[k for k in e.args.kw_defaults if k is not None]]:
            self.eval(d)
        return frozenset([mk_closure(fid, self.node)])

    def e_Call(self, e: ast.Call) -> Vals:
        callee = self.eval(e.func)
        pos: list[Vals] = []
        star: Vals | None = None  # None = no */** at the call
        kws: dict[str, Vals] = {}
        kwstar: Vals | None = None
        for a in e.args:
            if isinstance(a, ast.Starred):
                star = (star or EMPTY) | self.eval(a.value)
            else:
                pos.append(self.eval(a))
        for k in e.keywords:
            v = self.eval(k.value)
            if k.arg is None:
                kwstar = (kwstar or EMPTY) | v
            else:
                kws[k.arg] = kws.get(k.arg, EMPTY) | v
        out = self.apply(callee, pos, star, kws, kwstar, make_site(self.info.id, e))
        if not callee and isinstance(e.func, ast.Attribute) and isinstance(
            e.func.value, (ast.Name, ast.Attribute, ast.Subscript)
        ):
            out |= self._mutate_container(e.func.value, e.func.attr, pos, star, kws, kwstar)
        return out

    def _mutate_container(self, target: ast.expr, method: str, pos: Sequence[Vals],
                          star: Vals | None, kws: Mapping[str, Vals],
                          kwstar: Vals | None) -> Vals:
        """Propagate values inserted into list/set/dict containers (e.g. self.hooks.append(h))."""
        added = EMPTY
        if method in ("append", "add", "extend"):
            added = (pos[0] if pos else EMPTY) | (star or EMPTY)
        elif method == "insert":
            added = (pos[1] if len(pos) > 1 else EMPTY) | (star or EMPTY)
        elif method == "setdefault":
            added = ((pos[1] if len(pos) > 1 else EMPTY)
                     | kws.get("default", EMPTY) | (star or EMPTY) | (kwstar or EMPTY))
            self.assign(target, added)
            return self.eval(target) | added
        elif method == "update":
            added = (pos[0] if pos else EMPTY) | (star or EMPTY) | (kwstar or EMPTY)
            for v in kws.values():
                added |= v
        else:
            return EMPTY
        if added:
            self.assign(target, added)
        return EMPTY

    def e_IfExp(self, e: ast.IfExp) -> Vals:
        self.eval(e.test)
        return self.eval(e.body) | self.eval(e.orelse)

    def e_BoolOp(self, e: ast.BoolOp) -> Vals:
        out = EMPTY
        for v in e.values:
            out |= self.eval(v)
        return out

    def e_NamedExpr(self, e: ast.NamedExpr) -> Vals:
        v = self.eval(e.value)
        self.assign(e.target, v)
        return v

    def _union(self, elts: Iterable[ast.expr]) -> Vals:
        out = EMPTY
        for x in elts:
            out |= self.eval(x)
        return out

    def e_List(self, e: ast.List | ast.Tuple | ast.Set) -> Vals:
        return self._union(e.elts)

    e_Tuple = e_Set = e_List

    def e_Dict(self, e: ast.Dict) -> Vals:
        for k in e.keys:
            if k is not None:
                self.eval(k)
        return self._union(e.values)

    def e_Subscript(self, e: ast.Subscript) -> Vals:
        slc = self.eval(e.slice)
        val = self.eval(e.value)
        if not isinstance(e.ctx, ast.Load):
            return EMPTY
        site = make_site(self.info.id, e, "op")
        out = EMPTY
        for t in val:
            if isinstance(t, Instance):
                fn = self.state.lookup_class_attr(t.cls, "__getitem__")
                if fn:
                    bound = frozenset(_bind_attr(f, t.cls, t) for f in fn)
                    out |= self.apply(bound, [slc], None, {}, None, site)
                    continue
            out |= frozenset([t])
        return out

    def e_BinOp(self, e: ast.BinOp) -> Vals:
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
        left = self.eval(e.left)
        site = make_site(self.info.id, e, "op")
        for op, comp in zip(e.ops, e.comparators):
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
        return EMPTY

    def e_FormattedValue(self, e: ast.FormattedValue) -> Vals:
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

    def e_Starred(self, e: ast.Starred) -> Vals:
        return self.eval(e.value)

    def e_Await(self, e: ast.Await) -> Vals:
        return self.eval(e.value)

    def _has_instance_attr(self, cls: ClassId, attr: str) -> bool:
        if attr in self.state.inst_attrs.get(cls, {}) or self.state.has_class_attr(cls, attr):
            return True
        return any(attr in self.prog.classes[c].known_attrs for c in self.state.mro(cls))

    def e_Attribute(self, e: ast.Attribute) -> Vals:
        v = self.eval(e.value)
        if not isinstance(e.ctx, ast.Load):
            return EMPTY
        out: set[Token] = set()
        site = make_site(self.info.id, e, "attr")
        for t in v:
            if isinstance(t, Builtin) and f"{t.name}.{e.attr}" in STUBS:
                out.add(Builtin(f"{t.name}.{e.attr}"))
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
                            bound = frozenset(_bind_attr(f, attr_tok.cls, attr_tok) for f in get_fn)
                            out.update(self.apply(bound, [EMPTY, frozenset([t])], None, {}, None, site))
                            continue
                    out.add(_bind_attr(attr_tok, t.cls, t))
                if not self.state.has_class_attr(t.cls, e.attr):
                    for m in self.state.get_metaclasses(t.cls):
                        for attr_tok in self.state.lookup_class_attr(m, e.attr):
                            if isinstance(attr_tok, Closure):
                                out.add(BoundMethod(attr_tok, t))
                            else:
                                out.add(_bind_attr(attr_tok, m, t))
            elif isinstance(t, Instance):
                out.update(self.state.inst_attrs.get(t.cls, {}).get(e.attr, EMPTY))
                cls_vals = self.state.lookup_class_attr(t.cls, e.attr)
                fgets = EMPTY
                for attr_tok in cls_vals:
                    if isinstance(attr_tok, Property):
                        fgets |= attr_tok.fget
                    elif isinstance(attr_tok, Instance):
                        get_fn = self.state.lookup_class_attr(attr_tok.cls, "__get__")
                        if get_fn:
                            bound = frozenset(_bind_attr(f, attr_tok.cls, attr_tok) for f in get_fn)
                            out.update(self.apply(
                                bound, [frozenset([t]), frozenset([ClassObj(t.cls)])],
                                None, {}, None, site))
                        else:
                            out.add(attr_tok)
                    else:
                        out.add(_bind_attr(attr_tok, t.cls, t))
                if fgets:
                    out.update(self.apply(fgets, [frozenset([t])], None, {}, None, site))
                if not self._has_instance_attr(t.cls, e.attr):
                    getattr_fn = self.state.lookup_class_attr(t.cls, "__getattr__")
                    if getattr_fn:
                        bound = frozenset(_bind_attr(f, t.cls, t) for f in getattr_fn)
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
                                    out.add(_bind_attr(attr_tok, m, t.receiver))
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
                            out.add(_bind_attr(attr_tok, t.receiver.cls, t.receiver))
                    elif e.attr == "__new__":
                        out.add(Builtin("object.__new__"))
                    elif e.attr == "__setattr__":
                        out.add(Builtin("object.__setattr__", (frozenset([t.receiver]),)))
        if e.attr in METHOD_STUBS and not out:  # xs.sort(key=...), d.get(k, default)
            out.add(Builtin(METHOD_STUBS[e.attr], (v,)))
        return frozenset(out)

    def e_Yield(self, e: ast.Yield | ast.YieldFrom) -> Vals:
        self.ret |= self.eval(e.value)
        return EMPTY

    e_YieldFrom = e_Yield

    def _comp(self, gens: list[ast.comprehension]) -> None:
        for g in gens:
            self.assign(g.target, self._iter_expr(g.iter, is_async=bool(g.is_async)))
            for c in g.ifs:
                self.eval(c)

    def e_ListComp(self, e: ast.ListComp | ast.SetComp | ast.GeneratorExp) -> Vals:
        self._comp(e.generators)
        return self.eval(e.elt)

    e_SetComp = e_GeneratorExp = e_ListComp

    def e_DictComp(self, e: ast.DictComp) -> Vals:
        self._comp(e.generators)
        self.eval(e.key)
        return self.eval(e.value)

    # ------------------------------------------------------------ statements
    def block(self, stmts: Iterable[ast.AST]) -> None:
        for s in stmts:
            m: Callable[[ast.AST], None] | None = getattr(self, "s_" + type(s).__name__, None)
            if m:
                m(s)
            else:
                self.generic_stmt(s)

    def generic_stmt(self, s: ast.AST) -> None:
        for c in ast.iter_child_nodes(s):
            if isinstance(c, ast.expr):
                self.eval(c)
            elif isinstance(c, ast.stmt):
                self.block([c])
            elif isinstance(c, ast.ExceptHandler):
                exc = self.eval(c.type)
                if c.name:
                    self.bind(c.name, frozenset(Instance(t.cls) for t in exc if isinstance(t, ClassObj)))
                self.block(c.body)

    def s_FunctionDef(self, s: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        fid = self.prog.by_node[id(s)]
        for d in [*s.args.defaults, *[k for k in s.args.kw_defaults if k is not None]]:
            self.eval(d)
        vals = frozenset([mk_closure(fid, self.node)])
        for dec in reversed(s.decorator_list):
            callee = self.eval(dec)
            res = self.apply(callee, [vals], None, {}, None,
                             make_site(self.info.id, dec, "decorator"))
            vals = res if callee else vals  # unresolved decorator: assume identity
        self.bind(s.name, vals)

    s_AsyncFunctionDef = s_FunctionDef

    def s_ClassDef(self, s: ast.ClassDef) -> None:
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
            bound_sub = frozenset(_bind_attr(t, cid, cls_obj) for t in init_sub)
            self.apply(bound_sub, [], None, extra_kws, extra_kwstar, cls_site)
        for meta_cid in sorted(self.state.get_metaclasses(cid), key=lambda c: c.uid):
            meta_new = self.state.lookup_class_attr(meta_cid, "__new__")
            if meta_new:
                bound_new = frozenset(_bind_attr(t, meta_cid, ClassObj(meta_cid)) for t in meta_new)
                self.apply(bound_new, [frozenset([ClassObj(meta_cid)]), EMPTY, bases_val, ns_val],
                           None, extra_kws, extra_kwstar, cls_site)
            meta_init = self.state.lookup_class_attr(meta_cid, "__init__")
            if meta_init:
                self.apply(meta_init, [frozenset([cls_obj]), EMPTY, bases_val, ns_val],
                           None, extra_kws, extra_kwstar, cls_site)

        vals: Vals = frozenset([cls_obj])
        for dec, callee in reversed(list(zip(s.decorator_list, decs))):
            res = self.apply(callee, [vals], None, {}, None,
                             make_site(self.info.id, dec, "decorator"))
            vals = res if callee else vals  # unresolved decorator: assume identity
        self.bind(s.name, vals)

    def s_Assign(self, s: ast.Assign) -> None:
        v = self.eval(s.value)
        for t in s.targets:
            self.assign(t, v)

    def s_AugAssign(self, s: ast.AugAssign) -> None:
        rhs = self.eval(s.value)
        lhs = self.lookup(s.target.id) if isinstance(s.target, ast.Name) else EMPTY
        spec = BINOP_DUNDERS.get(type(s.op))
        res = EMPTY
        if spec is not None and lhs:
            site = make_site(self.info.id, s.value, "op")
            found, res = self.invoke_dunder(lhs, spec[2], [rhs], site)
            if not found:
                _, res = self.invoke_dunder(lhs, spec[0], [rhs], site)
        self.assign(s.target, res | rhs)

    def s_AnnAssign(self, s: ast.AnnAssign) -> None:
        if s.value is not None:
            self.assign(s.target, self.eval(s.value))

    def s_Delete(self, s: ast.Delete) -> None:
        for t in s.targets:
            if isinstance(t, ast.Attribute):
                site = make_site(self.info.id, t, "attr")
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
                                    bound = frozenset(_bind_attr(f, tok.cls, tok) for f in del_fn)
                                    self.apply(bound, [frozenset([obj])], None, {}, None, site)
                        if fdels:
                            self.apply(fdels, [frozenset([obj])], None, {}, None, site)
            elif isinstance(t, ast.Subscript):
                slc = self.eval(t.slice)
                obj = self.eval(t.value)
                self.invoke_dunder(obj, "__delitem__", [slc], make_site(self.info.id, t, "op"))
            else:
                self.eval(t)

    def s_Return(self, s: ast.Return) -> None:
        self.ret |= self.eval(s.value)

    def s_For(self, s: ast.For | ast.AsyncFor) -> None:
        self.assign(s.target, self._iter_expr(s.iter, is_async=isinstance(s, ast.AsyncFor)))
        self.block(s.body)
        self.block(s.orelse)

    s_AsyncFor = s_For

    def s_With(self, s: ast.With | ast.AsyncWith) -> None:
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
        self.block(s.body)
        for v, site in reversed(exits):
            self.invoke_dunder(v, exit_name, [EMPTY, EMPTY, EMPTY], site)

    s_AsyncWith = s_With

    def s_Import(self, s: ast.Import) -> None:
        for a in s.names:  # only stub modules; project modules are not resolved here
            if a.asname and a.name in STUB_MODULES:
                self.bind(a.asname, frozenset([Builtin(a.name)]))
            elif not a.asname and a.name.split(".")[0] in STUB_MODULES:
                top = a.name.split(".")[0]
                self.bind(top, frozenset([Builtin(top)]))

    def s_ImportFrom(self, s: ast.ImportFrom) -> None:
        if s.level or s.module not in STUB_MODULES:
            return
        for a in s.names:
            full = f"{s.module}.{a.name}"
            if full in STUBS:
                self.bind(a.asname or a.name, frozenset([Builtin(full)]))

    def run(self) -> None:
        node = self.info.node
        if self.info.kind == "lambda" and isinstance(node, ast.Lambda):
            self.ret = self.eval(node.body)
        else:
            self.block(self.info.body)


def analyze(node: Node, state: State, prog: Program) -> tuple[Env, Vals, Calls]:
    """Analyse one node to its local fixpoint -> (env, return value, [(site, callee)])."""
    it = Interp(prog, state, node)
    it.env = dict(state.entry.get(node, {})) if node.ctx.merged else dict(node.ctx.params)
    while True:
        snapshot = dict(it.env)
        it.ret, it.calls = EMPTY, []
        it.run()
        if it.env == snapshot:
            return it.env, it.ret, it.calls
