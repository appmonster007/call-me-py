"""Abstract interpretation of one function body in one context.

An abstract value is a set of tokens ("which functions may this expression
be?"): `Closure`s for user code, `Builtin`s for callables modelled in stubs.py,
classes and instances. Containers are collapsed to the union of their elements
plus a `Box` token (their allocation site); what is stored into a container
after it is created lives in `State.heap[box]` and is read back with `deref`.
Calls are context sensitive, with the callee's abstract entry state as context:

  entering  {param_i -> eval(arg_i)} becomes the callee's context
  returning the call evaluates to the callee's return value for that context

Flow sensitivity (`State.flow`, the default): a body is interpreted once, in
program order. Assigning a local replaces its value (strong update); branches
run on copies of the env and are joined where they meet again; a loop repeats
its body until the env at its head stops growing; `break` / `continue` /
`return` / `raise` end their path. Everything other scopes read from a node
(closures, default arguments, the docs) is its *summary*: the union of every
value each local takes anywhere in the body, stored in `State.envs`. Locals
written from inner scopes (`global` / `nonlocal`) stay flow-insensitive: any
call may write them, so a read sees the local value joined with those writes.

With `flow=False` a local is the union of all its bindings (weak updates) and
the body is re-run until that union stops changing.
"""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import final

from .extract import FuncInfo, Program, make_site, pattern_names
from .model import (
    EMPTY,
    Alloc,
    BoundMethod,
    Box,
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
End = tuple[Env, bool]  # flow mode: env where a path ends, and whether a live path gets there

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
    program: str | None  # Program.fingerprint of the source this state describes
    flow: bool  # flow-sensitive locals (strong updates, joins); False = one union per local
    order: list[Node]
    by_func: dict[FuncId, set[Node]]
    envs: dict[Node, Env]
    ret: dict[Node, Vals]  # return value per node
    edges: set[Edge]
    entry: dict[Node, Env]  # entry states of widened contexts
    class_attrs: dict[ClassId, Env]  # class-level attribute table per class
    inst_attrs: dict[Instance, Env]  # instance attributes per abstract object
    heap: dict[Box, Vals]  # what was stored into each container after its creation
    extern: dict[FuncId, Env]  # writes to a function's locals from inner scopes (global / nonlocal)
    bases: dict[ClassId, tuple[ClassId, ...]]  # direct user base classes per class
    metaclasses: dict[ClassId, set[ClassId]]  # direct user metaclass(es) per class
    _mro_cache: dict[ClassId, tuple[ClassId, ...]]
    changed: bool

    def __init__(self, max_ctx: int = 16, program: str | None = None, flow: bool = True) -> None:
        self.max_ctx = max_ctx
        self.program = program
        self.flow = flow
        self.order = []
        self.by_func = {}
        self.envs = {}
        self.ret = {}
        self.edges = set()
        self.entry = {}
        self.class_attrs = {}
        self.inst_attrs = {}
        self.heap = {}
        self.extern = {}
        self.bases = {}
        self.metaclasses = {}
        self._mro_cache = {}
        self.changed = False

    def copy(self) -> State:
        """Independent copy (values are immutable, containers are not)."""
        new = State(self.max_ctx, self.program, self.flow)
        new.order = list(self.order)
        new.by_func = {f: set(g) for f, g in self.by_func.items()}
        new.envs = {n: dict(e) for n, e in self.envs.items()}
        new.ret = dict(self.ret)
        new.edges = set(self.edges)
        new.entry = {n: dict(e) for n, e in self.entry.items()}
        new.class_attrs = {c: dict(e) for c, e in self.class_attrs.items()}
        new.inst_attrs = {i: dict(e) for i, e in self.inst_attrs.items()}
        new.heap = dict(self.heap)
        new.extern = {f: dict(e) for f, e in self.extern.items()}
        new.bases = dict(self.bases)
        new.metaclasses = {c: set(m) for c, m in self.metaclasses.items()}
        return new

    def same_result(self, other: State) -> bool:
        """Equal analysis results (ignores node registration order and `changed`)."""
        return (set(self.order) == set(other.order) and self.envs == other.envs
                and self.ret == other.ret and self.edges == other.edges
                and self.entry == other.entry and self.class_attrs == other.class_attrs
                and self.inst_attrs == other.inst_attrs and self.heap == other.heap
                and self.extern == other.extern and self.bases == other.bases
                and self.metaclasses == other.metaclasses)

    def add_class_attr(self, cls: ClassId, name: str, vals: Vals) -> None:
        attrs = self.class_attrs.setdefault(cls, {})
        if name not in attrs:
            attrs[name] = vals
            self.changed = True
        elif not vals <= attrs[name]:
            attrs[name] = attrs[name] | vals
            self.changed = True

    def add_inst_attr(self, inst: Instance, name: str, vals: Vals) -> None:
        attrs = self.inst_attrs.setdefault(inst, {})
        if name not in attrs:
            attrs[name] = vals
            self.changed = True
        elif not vals <= attrs[name]:
            attrs[name] = attrs[name] | vals
            self.changed = True

    def add_heap(self, box: Box, vals: Vals) -> None:
        old = self.heap.get(box, EMPTY)
        if not vals <= old:
            self.heap[box] = old | vals
            self.changed = True

    def add_extern(self, func: FuncId, name: str, vals: Vals) -> None:
        env = self.extern.setdefault(func, {})
        old = env.get(name, EMPTY)
        if not vals <= old:
            env[name] = old | vals
            self.changed = True

    def deref(self, vals: Vals) -> Vals:
        """`vals` plus everything stored into the containers it may hold (transitively)."""
        todo = [t for t in vals if isinstance(t, Box)]
        if not todo or not self.heap:
            return vals
        out = set(vals)
        seen: set[Box] = set()
        while todo:
            box = todo.pop()
            if box in seen:
                continue
            seen.add(box)
            for t in self.heap.get(box, EMPTY):
                if t not in out:
                    out.add(t)
                    if isinstance(t, Box):
                        todo.append(t)
        return frozenset(out)

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


def join_envs(envs: Iterable[Env]) -> Env:
    """Pointwise union of environments (the join where control-flow paths meet)."""
    out: Env = {}
    for env in envs:
        for k, v in env.items():
            out[k] = out.get(k, EMPTY) | v
    return out


@final
class _LoopFrame:
    """Environments leaving one loop early: at each `break` and each `continue`."""

    def __init__(self) -> None:
        self.breaks: list[Env] = []
        self.continues: list[Env] = []


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
    env: Env  # flow mode: the locals at the current program point
    ret: Vals
    calls: Calls
    class_stack: list[tuple[ClassId, Env]]
    flow: bool
    summary: Env  # flow mode: every value each local takes anywhere (what State.envs stores)
    live: bool  # flow mode: False after return / raise / break / continue (until a live path joins)
    loops: list[_LoopFrame]  # enclosing loops, innermost last
    tries: list[Env]  # enclosing try / with bodies: union of every state seen inside (may raise)

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
        self.flow = state.flow
        self.summary = {}
        self.live = True
        self.loops = []
        self.tries = []

    # ------------------------------------------------------------ names
    def bind(self, name: str, vals: Vals, *, weak: bool = False) -> None:
        """Bind `name` in the current scope. Flow mode replaces a local's value unless `weak`."""
        if self.class_stack:
            if name == "__new__":
                vals = frozenset(StaticMethod(t) if isinstance(t, Closure) else t for t in vals)
            elif name == "__init_subclass__":
                vals = frozenset(ClassMethod(t) if isinstance(t, Closure) else t for t in vals)
            cid, ns = self.class_stack[-1]
            ns[name] = ns.get(name, EMPTY) | vals
            if not self.readonly:
                self.state.add_class_attr(cid, name, vals)
        elif name in self.info.locals:
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
            self.state.add_extern(self.prog.module, name, vals)
        elif vals and not self.readonly and name in self.info.nonlocals:
            owner = self.nonlocal_owner(name)
            if owner is not None:
                self.state.add_extern(owner, name, vals)

    def nonlocal_owner(self, name: str) -> FuncId | None:
        """Nearest enclosing function that owns local `name` (target of `nonlocal name`)."""
        f = self.info.parent
        while f is not None and f != self.prog.module:
            if name in self.prog.funcs[f].locals:
                return f
            f = self.prog.funcs[f].parent
        return None

    def lookup(self, name: str) -> Vals:
        if self.class_stack and name in self.class_stack[-1][1]:
            return self.class_stack[-1][1][name]
        if name in self.info.locals:
            if self.node is None:
                return self.state.union_env(self.info.id, name)
            if self.flow:  # inner scopes may have written it (global / nonlocal) during any call
                return self.env.get(name, EMPTY) | self.state.extern.get(self.info.id, {}).get(name, EMPTY)
            return self.env.get(name, EMPTY)
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
                        self.state.add_inst_attr(t, attr, vals)
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
        return frozenset([Box(kind, Alloc(self.info.id, e.lineno, e.col_offset))])

    def store(self, target: ast.expr, recv: Vals, vals: Vals, *, fallback: bool = True) -> None:
        """Weak update: `vals` stored into the container(s) `recv`, the value of `target`.

        Containers of unknown origin (no Box yet) widen the target expression
        itself instead, as if the container were the union of its elements.
        """
        if not vals:
            return
        boxes = [t for t in recv if isinstance(t, Box)]
        if boxes:
            if not self.readonly:
                for box in boxes:
                    self.state.add_heap(box, vals)
        elif fallback and isinstance(target, (ast.Name, ast.Attribute, ast.Subscript)):
            self.assign(target, vals)

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
            out |= self.state.deref(frozenset([t]))
        return out

    def _iter_expr(self, expr: ast.expr, *, is_async: bool = False) -> Vals:
        v = self.eval(expr)
        if isinstance(expr, _LITERAL_CONTAINERS):
            return self.state.deref(v)
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
            created = frozenset([Instance(tok.cls, Alloc.of(site))])
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
                if fn is not None:  # stubs read their arguments as elements
                    d = self.state.deref
                    args = Args([d(v) for v in (*tok.bound, *pos)],
                                None if star is None else d(star),
                                {k: d(v) for k, v in {**dict(tok.bound_kw), **kws}.items()},
                                None if kwstar is None else d(kwstar))
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
            if isinstance(tok, (ClassMethod, Super, Property, Box)):
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
        """Values inserted into list/set/dict containers (xs.append(f), self.hooks.append(h))."""
        d = self.state.deref
        added = EMPTY
        if method in ("append", "add"):
            added = (pos[0] if pos else EMPTY) | (star or EMPTY)
        elif method == "extend":  # takes an iterable: its elements
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
        self.eval(e.test)
        return self.eval(e.body) | self.eval(e.orelse)

    def e_BoolOp(self, e: ast.BoolOp) -> Vals:
        out = EMPTY
        for v in e.values:
            out |= self.eval(v)
        return out

    def e_NamedExpr(self, e: ast.NamedExpr) -> Vals:
        v = self.eval(e.value)
        if isinstance(e.target, ast.Name):  # weak: the walrus may sit in a branch that does not run
            self.bind(e.target.id, v, weak=True)
        else:
            self.assign(e.target, v)
        return v

    def _union(self, elts: Iterable[ast.expr]) -> Vals:
        out = EMPTY
        for x in elts:
            out |= self.eval(x)
        return out

    def e_List(self, e: ast.List) -> Vals:
        return self._union(e.elts) | self.new_box("list", e)

    def e_Set(self, e: ast.Set) -> Vals:
        return self._union(e.elts) | self.new_box("set", e)

    def e_Tuple(self, e: ast.Tuple) -> Vals:  # immutable: no Box needed
        return self._union(e.elts)

    def e_Dict(self, e: ast.Dict) -> Vals:
        out = self.new_box("dict", e)
        for k, v in zip(e.keys, e.values):
            if k is None:  # {**other}
                out |= self.state.deref(self.eval(v))
            else:
                self.eval(k)
                out |= self.eval(v)
        return out

    def e_Subscript(self, e: ast.Subscript) -> Vals:
        slc = self.eval(e.slice)
        val = self.eval(e.value)
        if not isinstance(e.ctx, ast.Load):
            return EMPTY
        site = make_site(self.info.id, e, "op")
        out = EMPTY
        rest: set[Token] = set()
        for t in val:
            if isinstance(t, Instance):
                fn = self.state.lookup_class_attr(t.cls, "__getitem__")
                if fn:
                    bound = frozenset(_bind_attr(f, t.cls, t) for f in fn)
                    out |= self.apply(bound, [slc], None, {}, None, site)
                    continue
            rest.add(t)
        return out | self.state.deref(frozenset(rest))

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

    def e_Starred(self, e: ast.Starred) -> Vals:  # [*xs]: the elements of xs
        return self.state.deref(self.eval(e.value))

    def e_Await(self, e: ast.Await) -> Vals:
        return self.eval(e.value)

    def _has_instance_attr(self, inst: Instance, attr: str) -> bool:
        if attr in self.state.inst_attrs.get(inst, {}) or self.state.has_class_attr(inst.cls, attr):
            return True
        return any(attr in self.prog.classes[c].known_attrs for c in self.state.mro(inst.cls))

    def e_Attribute(self, e: ast.Attribute) -> Vals:
        v = self.eval(e.value)
        if not isinstance(e.ctx, ast.Load):
            return EMPTY
        return self.attribute(v, e)

    def attribute(self, v: Vals, e: ast.Attribute) -> Vals:
        """Value of `<receiver>.attr` for the already evaluated receiver `v`."""
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
                out.update(self.state.inst_attrs.get(t, {}).get(e.attr, EMPTY))
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
                if not self._has_instance_attr(t, e.attr):
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
        v = self.eval(e.value)
        self.ret |= self.state.deref(v) if isinstance(e, ast.YieldFrom) else v
        return EMPTY

    e_YieldFrom = e_Yield

    def _comp(self, gens: list[ast.comprehension]) -> dict[str, Vals | None]:
        """Bind the comprehension's targets; returns their outer values for `_end_comp`."""
        saved: dict[str, Vals | None] = {}
        if self.flow:
            for g in gens:
                for n in ast.walk(g.target):
                    if isinstance(n, ast.Name):
                        saved.setdefault(n.id, self.env.get(n.id))
        for g in gens:
            self.assign(g.target, self._iter_expr(g.iter, is_async=bool(g.is_async)))
            for c in g.ifs:
                self.eval(c)
        return saved

    def _end_comp(self, saved: dict[str, Vals | None]) -> None:
        """Comprehension variables live in their own scope: restore the outer values."""
        for name, vals in saved.items():
            if vals is None:
                self.env.pop(name, None)
            else:
                self.env[name] = vals

    def e_GeneratorExp(self, e: ast.ListComp | ast.SetComp | ast.GeneratorExp) -> Vals:
        saved = self._comp(e.generators)
        out = self.eval(e.elt)
        self._end_comp(saved)
        return out

    def e_ListComp(self, e: ast.ListComp) -> Vals:
        return self.e_GeneratorExp(e) | self.new_box("list", e)

    def e_SetComp(self, e: ast.SetComp) -> Vals:
        return self.e_GeneratorExp(e) | self.new_box("set", e)

    def e_DictComp(self, e: ast.DictComp) -> Vals:
        saved = self._comp(e.generators)
        self.eval(e.key)
        out = self.eval(e.value)
        self._end_comp(saved)
        return out | self.new_box("dict", e)

    # ------------------------------------------------------------ statements
    def block(self, stmts: Iterable[ast.AST]) -> None:
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
        for c in ast.iter_child_nodes(s):
            if isinstance(c, ast.expr):
                self.eval(c)
            elif isinstance(c, ast.stmt):
                self.block([c])
            elif isinstance(c, ast.ExceptHandler):
                self._handler(c)

    def _handler(self, h: ast.ExceptHandler) -> None:
        exc = self.eval(h.type)
        if h.name:
            at = Alloc(self.info.id, h.lineno, h.col_offset)
            self.bind(h.name, frozenset(Instance(t.cls, at) for t in exc if isinstance(t, ClassObj)))
        self.block(h.body)

    # ------------------------------------------------------------ control flow (flow mode)
    def _path(self, start: Env, stmts: Iterable[ast.AST]) -> End:
        """Run `stmts` from a copy of `start`: (env at their end, whether a live path gets there)."""
        self.env, self.live = dict(start), True
        self.block(stmts)
        return self.env, self.live

    def _meet(self, ends: Sequence[End]) -> None:
        """Continue after paths meet: from the join of the live ends. If none is live,
        what follows is dead code, analysed from the join of all of them."""
        live = [e for e, ok in ends if ok]
        self.live = bool(live)
        pick = live or [e for e, _ in ends]
        self.env = dict(pick[0]) if len(pick) == 1 else join_envs(pick)

    def s_If(self, s: ast.If) -> None:
        if not self.flow:
            return self.generic_stmt(s)
        self.eval(s.test)
        start = self.env
        self._meet([self._path(start, s.body), self._path(start, s.orelse)])

    def _loop(self, s: ast.While | ast.For | ast.AsyncFor, enter: Callable[[], object]) -> None:
        """Run the body from the loop head until the head stops growing, then `else`.

        `enter` is what happens at the head before each iteration (the while test, or
        binding the for target). Leaving: the head after a last `enter` that ends the loop
        (while) or with the iterator exhausted (for), joined with every `break`.
        """
        frame = _LoopFrame()
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
        if not self.flow:
            return self.generic_stmt(s)
        self._loop(s, lambda: self.eval(s.test))

    def s_Break(self, s: ast.Break) -> None:
        if self.flow and self.loops:
            self.loops[-1].breaks.append(dict(self.env))
            self.live = False

    def s_Continue(self, s: ast.Continue) -> None:
        if self.flow and self.loops:
            self.loops[-1].continues.append(dict(self.env))
            self.live = False

    def s_Raise(self, s: ast.Raise) -> None:
        self.eval(s.exc)
        self.eval(s.cause)
        if self.flow:  # handlers start from their try's accumulator, which already holds this state
            self.live = False

    def s_Try(self, s: ast.Try | ast.TryStar) -> None:
        """Handlers start from every state the body passed through (any of them may raise);
        `finally` also from every state of the else / handler blocks and the abrupt exits."""
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

    s_TryStar = s_Try

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

    def s_Match(self, s: ast.Match) -> None:
        """Flow mode: cases are alternatives. A case that does not match falls through to
        the next one with whatever its pattern / guard already bound."""
        subject = self.eval(s.subject)
        tried = self.env  # state when the next case is tried
        ends: list[End] = []
        for case in s.cases:
            if self.flow:
                self.env, self.live = dict(tried), True
            for n in ast.walk(case.pattern):
                if isinstance(n, ast.expr):  # value patterns, class names
                    self.eval(n)
                for name in pattern_names(n):
                    self.bind(name, subject)  # captures: some part of the subject
            self.eval(case.guard)
            if self.flow:
                tried = join_envs([tried, self.env])
            self.block(case.body)
            ends.append((self.env, self.live))
        if self.flow:
            self._meet([*ends, (tried, True)])  # `tried`: no case matched

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
        if isinstance(s.op, (ast.Add, ast.BitOr)):  # xs += ys / s |= t / d |= e mutate in place
            self.store(s.target, lhs, self.state.deref(rhs), fallback=False)
        # flow mode keeps lhs: `xs += ys` rebinds xs to the same (mutated) container
        self.assign(s.target, (lhs if self.flow else EMPTY) | res | rhs)

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
            elif (self.flow and isinstance(t, ast.Name) and not self.class_stack
                  and t.id in self.info.locals):
                self.env.pop(t.id, None)  # `del x`: x holds nothing until rebound
            else:
                self.eval(t)

    def s_Return(self, s: ast.Return) -> None:
        self.ret |= self.eval(s.value)
        if self.flow:
            self.live = False

    def s_For(self, s: ast.For | ast.AsyncFor) -> None:
        elems = self._iter_expr(s.iter, is_async=isinstance(s, ast.AsyncFor))
        if self.flow:
            self._loop(s, lambda: self.assign(s.target, elems))
            return
        self.assign(s.target, elems)
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


def analyze(node: Node, state: State, prog: Program,
            env: Env | None = None) -> tuple[Env, Vals, Calls]:
    """Analyse one node to its local fixpoint -> (env, return value, [(site, callee)]).

    Flow mode (`state.flow`): one pass in program order from the entry state; the
    returned env is the node's summary (every value each local takes), joined with
    `env` (default: the env already stored for `node` in `state`). Otherwise the local
    fixpoint starts from the entry state joined with `env` and re-runs the body
    until the env stops growing. Either way the returned env can be passed back
    in: with the same `state`, analyze(n, s, p, analyze(n, s, p)[0]) returns the
    same result.

    Side effect: callee contexts reached for the first time are registered in
    `state` (State.resolve), and widened contexts may grow their entry state; so do
    the class / instance attribute tables, the heap and `extern` (global / nonlocal
    writes). Env, return values and edges are NOT written back; `solver.step` joins
    them. Pass `state.copy()` to keep a state untouched.
    """
    it = Interp(prog, state, node)
    it.env = dict(state.entry.get(node, {})) if node.ctx.merged else dict(node.ctx.params)
    known = state.envs.get(node, {}) if env is None else env
    if state.flow:  # extern writes are read at each lookup; `known` only joins the summary
        it.summary = join_envs([it.env, state.extern.get(node.func, {}), known])
        it.run()
        return it.summary, it.ret, it.calls
    for name, vals in state.extern.get(node.func, {}).items():  # global / nonlocal writes
        it.env[name] = it.env.get(name, EMPTY) | vals
    for name, vals in known.items():
        it.env[name] = it.env.get(name, EMPTY) | vals
    while True:
        snapshot = dict(it.env)
        it.ret, it.calls = EMPTY, []
        it.run()
        if it.env == snapshot:
            return it.env, it.ret, it.calls
