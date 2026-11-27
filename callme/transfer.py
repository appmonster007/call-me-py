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
value each local takes anywhere in the body, stored in `State.envs`. Writes to
enclosing scopes (`global` / `nonlocal`) are summarised per node in `State.writes`
and applied back at call sites, with a strong update when every callee statically
must-writes the variable.

With `flow=False` a local is the union of all its bindings (weak updates) and
the body is re-run until that union stops changing.
"""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import final

from .extract import FuncInfo, Program, absolute_module, make_site, pattern_names
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
    Module,
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
from .stubs import BUILTIN_NAMES, METHOD_STUBS, STUB_MODULES, STUB_PACKAGES, STUBS, Args

Env = dict[str, Vals]
Writes = dict[FuncId, Env]  # owner function -> {name: values written by a node / its callees}
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
    """Global analysis state: contexts, environments, return values, call edges and object tables.

    One `State` holds the result of the whole fixpoint. Every table only grows, because each
    write is a join, and `changed` records whether anything grew since it was last reset.
    `solver.step` resets it once per round.
    """

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
    extern: dict[FuncId, Env]  # writes from other scopes: global / nonlocal, and `module.attr = v`
    writes: dict[Node, Writes]  # per-node global / nonlocal / module-attr writes (incl. callees)
    bases: dict[ClassId, tuple[ClassId, ...]]  # direct user base classes per class
    metaclasses: dict[ClassId, set[ClassId]]  # direct user metaclass(es) per class
    _mro_cache: dict[ClassId, tuple[ClassId, ...]]
    changed: bool

    def __init__(self, max_ctx: int = 16, program: str | None = None, flow: bool = True) -> None:
        """Create an empty state.

        max_ctx  contexts per function before calls are widened to one shared context
        program  fingerprint of the `Program` the state describes, checked when resuming
        flow     True for flow-sensitive locals; False makes each local the union of its bindings
        """
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
        self.writes = {}
        self.bases = {}
        self.metaclasses = {}
        self._mro_cache = {}
        self.changed = False

    def copy(self) -> State:
        """Return an independent copy: tables are copied, the immutable values are shared."""
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
        new.writes = {n: {f: dict(e) for f, e in ws.items()} for n, ws in self.writes.items()}
        new.bases = dict(self.bases)
        new.metaclasses = {c: set(m) for c, m in self.metaclasses.items()}
        return new

    def same_result(self, other: State) -> bool:
        """Return True if both states hold the same results, ignoring node order and `changed`."""
        return (set(self.order) == set(other.order) and self.envs == other.envs
                and self.ret == other.ret and self.edges == other.edges
                and self.entry == other.entry and self.class_attrs == other.class_attrs
                and self.inst_attrs == other.inst_attrs and self.heap == other.heap
                and self.extern == other.extern and self.writes == other.writes
                and self.bases == other.bases and self.metaclasses == other.metaclasses)

    def add_class_attr(self, cls: ClassId, name: str, vals: Vals) -> None:
        """Join `vals` into attribute `name` of class `cls`, setting `changed` if it grew."""
        attrs = self.class_attrs.setdefault(cls, {})
        if name not in attrs:
            attrs[name] = vals
            self.changed = True
        elif not vals <= attrs[name]:
            attrs[name] = attrs[name] | vals
            self.changed = True

    def add_inst_attr(self, inst: Instance, name: str, vals: Vals) -> None:
        """Join `vals` into attribute `name` of instance `inst`, setting `changed` if it grew."""
        attrs = self.inst_attrs.setdefault(inst, {})
        if name not in attrs:
            attrs[name] = vals
            self.changed = True
        elif not vals <= attrs[name]:
            attrs[name] = attrs[name] | vals
            self.changed = True

    def add_heap(self, box: Box, vals: Vals) -> None:
        """Join `vals` into what is stored in container `box`, setting `changed` if it grew."""
        old = self.heap.get(box, EMPTY)
        if not vals <= old:
            self.heap[box] = old | vals
            self.changed = True

    def add_extern(self, func: FuncId, name: str, vals: Vals) -> None:
        """Join `vals` into the writes to variable `name` of `func` made from other scopes.

        These are `global` and `nonlocal` writes, and attribute writes to a module object,
        which `func`, a module body, then holds as a global.
        """
        env = self.extern.setdefault(func, {})
        old = env.get(name, EMPTY)
        if not vals <= old:
            env[name] = old | vals
            self.changed = True

    def add_write(self, node: Node, func: FuncId, name: str, vals: Vals) -> None:
        """Join `vals` into the side-effect writes of `node` to `(func, name)`.

        Records `global`, `nonlocal` and module-attribute writes performed directly by `node`
        or transitively by its callees, so callers in flow mode can apply them at call sites.
        """
        if not vals:
            return
        env = self.writes.setdefault(node, {}).setdefault(func, {})
        old = env.get(name, EMPTY)
        if not vals <= old:
            env[name] = old | vals
            self.changed = True

    def deref(self, vals: Vals) -> Vals:
        """Return `vals` plus everything stored into the containers it holds, transitively.

        Containers found among the stored values are read too, so a list of lists yields the
        elements of both.
        """
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
        """Append new direct base classes to those of `cls`, in order, and clear the MRO cache."""
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
        """Record `meta` as a metaclass of `cls`; a class is never its own metaclass."""
        if meta == cls:
            return
        group = self.metaclasses.setdefault(cls, set())
        if meta not in group:
            group.add(meta)
            self.changed = True

    def get_metaclasses(self, cls: ClassId) -> frozenset[ClassId]:
        """Return the metaclasses of `cls`: those of the first class in its MRO that has any."""
        for c in self.mro(cls):
            ms = self.metaclasses.get(c)
            if ms:
                return frozenset(ms)
        return frozenset()

    def mro(self, cls: ClassId) -> tuple[ClassId, ...]:
        """Return the C3 method resolution order of `cls`, starting with `cls` (cached)."""
        cached = self._mro_cache.get(cls)
        if cached is not None:
            return cached
        res = self._compute_mro(cls, set())
        self._mro_cache[cls] = res
        return res

    def _compute_mro(self, cls: ClassId, visiting: set[ClassId]) -> tuple[ClassId, ...]:
        """Compute the C3 linearisation of `cls` from the base classes known so far.

        `visiting` holds the classes on the current path, so a cyclic hierarchy ends instead of
        recursing forever. When C3 finds no consistent order, it takes the next class depth first
        instead of failing.
        """
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
        """Return attribute `name` from the first class in `cls`'s MRO that defines it, or `EMPTY`.

        after  start the search after this class, as `super()` does; returns `EMPTY` when it is
               not in the MRO
        """
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
        """Return True if any class in `cls`'s MRO defines attribute `name`."""
        return any(name in self.class_attrs.get(c, {}) for c in self.mro(cls))

    def register(self, node: Node) -> None:
        """Add `node` to the nodes to analyse, if it is new, and set `changed`."""
        group = self.by_func.setdefault(node.func, set())
        if node not in group:
            group.add(node)
            self.order.append(node)
            self.changed = True

    def resolve(self, func: FuncId, params: ParamState, parent: Node | None) -> Node:
        """Return the node entered by calling `func` with entry state `params` and closure `parent`.

        A new context is registered while `func` has fewer than `max_ctx` of them. After that,
        every new context is widened: the call goes to the single `func[*]` node, whose context is
        `Ctx(merged=True)`, and `params` are joined into its entry state in `entry`.
        """
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
        """Return the union of local `name`'s summary over every context of `func`."""
        out = EMPTY
        for n in self.by_func.get(func, ()):
            out |= self.envs.get(n, {}).get(name, EMPTY)
        return out


def join_envs(envs: Iterable[Env]) -> Env:
    """Return the pointwise union of environments, the join where control-flow paths meet."""
    out: Env = {}
    for env in envs:
        for k, v in env.items():
            out[k] = out.get(k, EMPTY) | v
    return out


@final
class _LoopFrame:
    """Environments that leave one loop early: one per `break` and one per `continue`."""

    def __init__(self) -> None:
        """Start a frame with no early exits yet."""
        self.breaks: list[Env] = []
        self.continues: list[Env] = []


def mk_closure(func: FuncId, node: Node | None) -> Closure:
    """Return the closure token for `func` defined while `node` was running.

    The parent is dropped (set to None) when `node` is None, or when the closure would nest
    contexts deeper than `MAX_DEPTH`; its free variables are then read from every context of
    the enclosing function.
    """
    if node is None:
        return Closure(func, None)
    depth = 1 + max((t.depth for _, ts in node.ctx.params for t in ts), default=0)
    if depth > MAX_DEPTH:
        return Closure(func, None)
    return Closure(func, node, depth)


def _bind_attr(tok: Token, owner_cls: ClassId, receiver: ClassObj | Instance) -> Token:
    """Return `tok` bound as Python binds an attribute read through `receiver`.

    A classmethod binds the owner class `owner_cls`, a staticmethod gives its function, and a
    function read through an instance becomes a bound method. `partialmethod` and
    `singledispatchmethod` tokens bind `receiver` as their first argument. Other tokens are
    unchanged.
    """
    if isinstance(tok, ClassMethod):
        return BoundMethod(tok.func, ClassObj(owner_cls))
    if isinstance(tok, StaticMethod):
        return tok.func
    if isinstance(receiver, Instance):
        if isinstance(tok, Closure) or (isinstance(tok, Builtin) and tok.name == "<dataclass_init>"):
            return BoundMethod(tok, receiver)
        if isinstance(tok, Builtin) and tok.name == "<partialmethod>":
            return Builtin("<partial>", (tok.bound[0], frozenset([receiver]), *tok.bound[1:]), tok.bound_kw)
        if isinstance(tok, Builtin) and tok.name == "<partialmethod*>":
            return Builtin("<partial*>", (tok.bound[0], frozenset([receiver]) | tok.bound[1]), tok.bound_kw)
        if isinstance(tok, Builtin) and tok.name == "<singledispatchmethod>":
            return BoundMethod(Builtin("<singledispatch>", tok.bound), receiver)
    return tok


@final
class Interp:
    """Abstract interpreter for one node: a function body run under one context.

    It evaluates expressions to `Vals`, collects the call edges it finds in `calls`, and
    writes shared facts (contexts, attributes, the heap) into `state`. In flow mode, `env` is
    the environment at the current point, `summary` collects every value each local takes,
    and `live` is False once the current path has ended.
    """

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
    _weak_depth: int  # > 0 inside conditional expressions, stubs, or implicit calls: no strong kills

    def __init__(self, prog: Program, state: State, node: Node | None, *,
                 readonly: bool = False, func: FuncId | None = None) -> None:
        """Prepare to interpret `node`, or function `func` with no context.

        readonly  evaluate without writing to `state`; used for default values, which run in
                  the defining scope and are read by every context
        func      the function to use when `node` is None; locals then read the union over all
                  of its contexts
        """
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
        self._weak_depth = 0

    # ------------------------------------------------------------ names
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
                        bound = frozenset(_bind_attr(f, tok.cls, tok) for f in set_fn)
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
                    bound = frozenset(_bind_attr(f, t.cls, t) for f in setattr_fn)
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
                        bound = frozenset(_bind_attr(f, t.cls, t) for f in fn)
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
                        bound = frozenset(_bind_attr(f, t.cls, t) for f in it_fn)
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

    # ------------------------------------------------------------ calls
    def default_value(self, tok: Closure, d: ast.expr) -> Vals:
        """Evaluate default value `d` of closure `tok` in read-only mode, where it was defined.

        With a known parent node, the default is evaluated in that node's summary environment;
        otherwise in the enclosing function, joined over all its contexts.
        """
        if tok.parent is not None:
            it = Interp(self.prog, self.state, tok.parent, readonly=True)
            it.env = dict(self.state.envs.get(tok.parent, {}))
        else:
            it = Interp(self.prog, self.state, None, readonly=True,
                        func=self.prog.funcs[tok.func].parent)
        return it.eval(d)

    def bind_args(self, tok: Closure, pos: Sequence[Vals], star: Vals | None,
                  kws: Mapping[str, Vals], kwstar: Vals | None) -> ParamState | None:
        """Bind call arguments to the parameters of closure `tok`; return the callee's entry state.

        The result is a sorted tuple of (parameter, values) pairs, without parameters that get
        no values. With `*args` or `**kwargs` at the call, their values may fill any parameter
        that is still open. Default values that are not constants are evaluated where the
        function was defined. Return None when the call cannot match the signature: too many
        positional arguments, an unknown or repeated keyword, or a missing required argument.
        """
        sig = self.prog.funcs[tok.func].sig
        vals: Env = {}
        assigned: set[str] = set()

        def put(n: str, v: Vals) -> None:
            """Join `v` into the value bound to parameter `n`."""
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
        """Create instances of class `tok` as `type.__call__` does, and return them.

        `__new__` is called if the class or a base defines it; otherwise a new instance is
        allocated at `site`. Each created instance of the class then has `__init__` called with
        the same arguments.
        """
        self._weak_depth += 1
        try:
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
        finally:
            self._weak_depth -= 1

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
                      kills: frozenset[str] = frozenset()) -> None:
        """Apply callee side-effect `writes` to `self.env` (strong for `kills`) and propagate.

        Writes targeting `self.info.id` update `self.env`, `self.summary` and `self.tries`.
        Writes targeting other scopes—or targeting `self.info.id` without a guaranteed kill on a
        non-module function, which may belong to an outer recursive activation—are propagated to
        `State.writes[self.node]`.
        """
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

    def apply(self, callee: Vals, pos: Sequence[Vals], star: Vals | None,
              kws: Mapping[str, Vals], kwstar: Vals | None, site: Site) -> Vals:
        """Call every callable in `callee` with these arguments at `site`, and return the results.

        What happens depends on the token:

        Closure      the arguments are bound (`bind_args`), the callee node is resolved (a new
                     context if needed), a (site, callee) edge is recorded, and its return value
                     so far is the result; a call that cannot match the signature is skipped
        Builtin      its stub runs, with bound arguments prepended and containers dereferenced
        BoundMethod  the receiver is passed as the first argument
        ClassObj     a metaclass `__call__` if there is one, else `instantiate`
        Instance     its class's `__call__`
        StaticMethod its function is called unbound

        Other tokens, modules among them, are not callable and are skipped. In read-only mode
        no context is registered and no edge is recorded. In flow mode, side-effect writes from
        `State.writes` are applied back to `self.env` (strongly when every matching closure
        statically must-writes the variable) and propagated to `State.writes[self.node]`.

        star    values passed through `*args`, or None when the call has no `*` argument
        kwstar  values passed through `**kwargs`, or None when the call has no `**` argument
        """
        out = EMPTY
        all_closures = bool(callee) and all(isinstance(t, Closure) for t in callee)
        can_kill = (self.flow and not self.readonly and self._weak_depth == 0
                    and self.node is not None and not self.node.ctx.merged)
        collected_writes: Writes = {}
        kills: set[str] | None = None
        guard_poly = not all_closures and len(callee) > 1
        if guard_poly:
            self._weak_depth += 1
        try:
            for tok in callee:
                if isinstance(tok, Builtin):
                    fn = STUBS.get(tok.name)
                    if fn is not None:  # stubs read their arguments as elements
                        d = self.state.deref
                        args = Args([d(v) for v in (*tok.bound, *pos)],
                                    None if star is None else d(star),
                                    {k: d(v) for k, v in {**dict(tok.bound_kw), **kws}.items()},
                                    None if kwstar is None else d(kwstar))
                        self._weak_depth += 1
                        try:
                            out |= fn(self, args, site)
                        finally:
                            self._weak_depth -= 1
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
                if isinstance(tok, (ClassMethod, Super, Property, Box, Module)):
                    continue
                params = self.bind_args(tok, pos, star, kws, kwstar)
                if params is None:
                    continue
                if self.readonly:
                    target = Node(tok.func, Ctx(params, tok.parent))
                else:
                    target = self.state.resolve(tok.func, params, tok.parent)
                    self.calls.append((site, target))
                    if self.flow:
                        tw = self.state.writes.get(target)
                        if tw:
                            for owner, env in tw.items():
                                dst = collected_writes.setdefault(owner, {})
                                for name, vals in env.items():
                                    dst[name] = dst.get(name, EMPTY) | vals
                        if can_kill and all_closures:
                            tk = self._must_kills_for_closure(tok)
                            kills = set(tk) if kills is None else (kills & tk)
                out |= self.state.ret.get(target, EMPTY)
        finally:
            if guard_poly:
                self._weak_depth -= 1
        if collected_writes or kills:
            self._apply_writes(collected_writes, frozenset(kills) if kills else frozenset())
        return out

    # ------------------------------------------------------------ expressions
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
                        bound = frozenset(_bind_attr(f, t.cls, t) for f in fn)
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

    def e_Starred(self, e: ast.Starred) -> Vals:  # [*xs]: the elements of xs
        """Evaluate `*xs` in a display: the elements of `xs`."""
        return self.state.deref(self.eval(e.value))

    def e_Await(self, e: ast.Await) -> Vals:
        """Evaluate `await x`: call `__await__` if defined, otherwise return `x` itself."""
        v = self.eval(e.value)
        found, out = self.invoke_dunder(v, "__await__", [], make_site(self.info.id, e, "op"))
        return out if found else v

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
        finally:
            self._weak_depth -= 1
        if e.attr in METHOD_STUBS and not out:  # xs.sort(key=...), d.get(k, default)
            out.add(Builtin(METHOD_STUBS[e.attr], (v,)))
        return frozenset(out)

    def e_Yield(self, e: ast.Yield | ast.YieldFrom) -> Vals:
        """Evaluate `yield x` or `yield from xs`: join the yielded values into the return value.

        The function's return value thus stands for what iterating the generator produces. The
        expression itself, the value sent in, is `EMPTY`.
        """
        v = self.eval(e.value)
        self.ret |= self.state.deref(v) if isinstance(e, ast.YieldFrom) else v
        return EMPTY

    e_YieldFrom = e_Yield

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

    # ------------------------------------------------------------ statements
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

    # ------------------------------------------------------------ control flow (flow mode)
    def _path(self, start: Env, stmts: Iterable[ast.AST]) -> End:
        """Run `stmts` from a copy of `start` and return how the path ends.

        The result is the environment at the end and whether a live path gets there.
        """
        self.env, self.live = dict(start), True
        self.block(stmts)
        return self.env, self.live

    def _meet(self, ends: Sequence[End]) -> None:
        """Continue after alternative paths meet, from the join of the ends that are live.

        If none is live, the code that follows is dead: it is analysed from the join of all the
        ends, and the current path stays dead.
        """
        live = [e for e, ok in ends if ok]
        self.live = bool(live)
        pick = live or [e for e, _ in ends]
        self.env = dict(pick[0]) if len(pick) == 1 else join_envs(pick)

    def s_If(self, s: ast.If) -> None:
        """Run `if`: in flow mode both branches start from the same state, then meet."""
        if not self.flow:
            return self.generic_stmt(s)
        self.eval(s.test)
        start = self.env
        self._meet([self._path(start, s.body), self._path(start, s.orelse)])

    def _loop(self, s: ast.While | ast.For | ast.AsyncFor, enter: Callable[[], object]) -> None:
        """Run a loop body from the loop head until the head stops growing, then the `else` block.

        `enter` runs at the head before each iteration: the `while` test, or binding the `for`
        target. The loop is left from the final head, after the `else` block, joined with the
        state at every `break`; `continue` states join the head.
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
        """Run `while` as a loop whose head evaluates the test."""
        if not self.flow:
            return self.generic_stmt(s)
        self._loop(s, lambda: self.eval(s.test))

    def s_Break(self, _s: ast.Break) -> None:
        """End the current path at `break`, saving its state for the end of the loop."""
        if self.flow and self.loops:
            self.loops[-1].breaks.append(dict(self.env))
            self.live = False

    def s_Continue(self, _s: ast.Continue) -> None:
        """End the current path at `continue`, saving its state for the loop head."""
        if self.flow and self.loops:
            self.loops[-1].continues.append(dict(self.env))
            self.live = False

    def s_Raise(self, s: ast.Raise) -> None:
        """Evaluate `raise` for its calls and end the current path."""
        self.eval(s.exc)
        self.eval(s.cause)
        if self.flow:  # handlers start from their try's accumulator, which already holds this state
            self.live = False

    def s_Try(self, s: ast.Try | ast.TryStar) -> None:
        """Run `try` or `try*` with its handlers, `else` block and finally block, in flow mode.

        Any point of the body may raise, so each handler starts from the join of every state
        the body passed through. The `else` block runs only after the body completes. The
        finally block starts from every normal end joined with every state seen in the body,
        the handlers and the `else` block; a `break` or `continue` inside the `try` passes
        through it before leaving.
        """
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
        """Run a def: evaluate its defaults, create its closure, apply its decorators, bind it.

        Decorators are applied from the bottom one up, each at its own decorator site. A
        decorator whose value is unknown is assumed to be the identity, so the function keeps
        its own value.
        """
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
        """Run `match`, binding each case's captures to the subject's value.

        In flow mode the cases are alternatives. A case that does not match falls through to
        the next one with whatever its pattern and guard already bound. The path where no case
        matches meets the ends of the cases.
        """
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
        """Run `a = b = value`: evaluate the value once and assign it to every target."""
        v = self.eval(s.value)
        for t in s.targets:
            self.assign(t, v)

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
                                        bound = frozenset(_bind_attr(f, tok.cls, tok) for f in del_fn)
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

    def s_For(self, s: ast.For | ast.AsyncFor) -> None:
        """Run `for` or `async for` over the iterable's elements, as a loop in flow mode."""
        elems = self._iter_expr(s.iter, is_async=isinstance(s, ast.AsyncFor))
        if self.flow:
            self._loop(s, lambda: self.assign(s.target, elems))
            return
        self.assign(s.target, elems)
        self.block(s.body)
        self.block(s.orelse)

    s_AsyncFor = s_For

    def s_With(self, s: ast.With | ast.AsyncWith) -> None:
        """Run `with` or `async with`: call `__enter__`, run the body, then call `__exit__`.

        The `as` target is bound to what `__enter__` returns, or to the context manager itself
        when no class defines `__enter__`. In flow mode `__exit__` may swallow an exception from
        any point of the body, so the state after the statement also joins every state of the
        body.
        """
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

    # ------------------------------------------------------------ modules
    def run_module(self, name: str) -> None:
        """Run the body of program module `name` and of its parent packages, as an import does.

        Each body's node is registered, so that it is analysed, but no call edge is recorded,
        because a traced run shows no call into a module body either. A namespace package has
        no body, and read-only mode registers nothing.
        """
        if self.readonly:
            return
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            info = self.prog.modules.get(".".join(parts[:i]))
            if info is not None and info.func is not None:
                mod_node = Node(info.func, Ctx())
                self.state.register(mod_node)
                if self.flow:
                    mw = self.state.writes.get(mod_node)
                    if mw:
                        self._apply_writes(mw)

    def module_attr(self, name: str, attr: str) -> Vals:
        """Return attribute `attr` of program module `name`: one of its globals, or a submodule.

        A global's value is its summary in the module body, plus what other scopes stored
        into it (`global` writes and `module.attr = ...`, in `State.extern`). A submodule
        `name.attr` of the program is an attribute too, as Python sets it on its package once
        it is imported.
        """
        out = EMPTY
        body = self.prog.modules[name].func if name in self.prog.modules else None
        if body is not None:
            out = (self.state.union_env(body, attr)
                   | self.state.extern.get(body, {}).get(attr, EMPTY))
        sub = f"{name}.{attr}"
        if sub in self.prog.modules:
            out |= frozenset([Module(sub)])
        return out

    def import_any(self) -> Vals:
        """Return every module of the program, and run each, as an import of an unknown name may.

        This models `__import__(name)` and `importlib.import_module(name)`: the analysis does
        not track strings, so any module may be the one imported.
        """
        for name in self.prog.modules:
            self.run_module(name)
        return frozenset(Module(n) for n in self.prog.modules)

    def s_Import(self, s: ast.Import) -> None:
        """Run `import`: bind program modules as `Module`s and stub modules as `Builtin`s.

        `import a.b.c` runs `a`, `a.b` and `a.b.c` and binds `a`; `import a.b.c as x` binds
        `x` to `a.b.c` itself. A program module wins over a stub module of the same name. Any
        other module binds nothing, so calls through it get no edge.
        """
        for a in s.names:
            top = a.name.split(".")[0]
            if a.name in self.prog.modules:
                self.run_module(a.name)
                self.bind(a.asname or top, frozenset([Module(a.name if a.asname else top)]))
            elif a.asname and (a.name in STUB_MODULES or a.name in STUB_PACKAGES):
                self.bind(a.asname, frozenset([Builtin(a.name)]))
            elif not a.asname and (a.name in STUB_MODULES or top in STUB_MODULES or top in STUB_PACKAGES):
                self.bind(top, frozenset([Builtin(top)]))

    def s_ImportFrom(self, s: ast.ImportFrom) -> None:
        """Run `from module import name`: bind attributes of a program module, or stubs.

        For a program module, the module and its parent packages run, and each name is bound
        to the module's attribute (`module_attr`); a name that is a submodule runs as well.
        `from module import *` binds every name in the module's `exports`. A relative import
        resolves against the current module's package. For other modules only the names
        that stubs provide are bound.
        """
        base = absolute_module(self.prog.module_of(self.info.id), s.level, s.module)
        if base is not None and base in self.prog.modules:
            self.run_module(base)
            for a in s.names:
                star = a.name == "*"
                for n in sorted(self.prog.modules[base].exports) if star else [a.name]:
                    if f"{base}.{n}" in self.prog.modules:
                        self.run_module(f"{base}.{n}")
                    self.bind(n if star else a.asname or n, self.module_attr(base, n))
            return
        if s.level or (s.module not in STUB_MODULES and s.module not in STUB_PACKAGES):
            return
        for a in s.names:
            if a.name == "*":
                if s.module == "builtins":
                    for n in BUILTIN_NAMES:
                        self.bind(n, frozenset([Builtin(n)]))
                else:
                    prefix = f"{s.module}."
                    for full in STUBS:
                        if full.startswith(prefix) and "." not in full[len(prefix):]:
                            self.bind(full[len(prefix):], frozenset([Builtin(full)]))
                continue
            full = f"{s.module}.{a.name}"
            if full in STUBS or full in STUB_MODULES or full in STUB_PACKAGES:
                self.bind(a.asname or a.name, frozenset([Builtin(full)]))
            elif s.module == "builtins" and a.name in BUILTIN_NAMES:
                self.bind(a.asname or a.name, frozenset([Builtin(a.name)]))

    def run(self) -> None:
        """Run the function's body: a lambda's expression becomes its return value."""
        node = self.info.node
        if self.info.kind == "lambda" and isinstance(node, ast.Lambda):
            self.ret = self.eval(node.body)
        else:
            self.block(self.info.body)


def analyze(node: Node, state: State, prog: Program,
            env: Env | None = None) -> tuple[Env, Vals, Calls]:
    """Analyse one node to its local fixpoint, and return (environment, return value, call edges).

    The call edges are (site, callee node) pairs. In flow mode (`state.flow`) the body runs
    once in program order from the entry state. The returned environment is the node's
    summary: every value each local takes, joined with `env`. Otherwise the body re-runs from
    the entry state joined with `env` until the environment stops growing. Either way, the
    returned environment can be passed back in: with the same `state`,
    `analyze(n, s, p, analyze(n, s, p)[0])` returns the same result.

    Side effects: new callee contexts are registered in `state` (`State.resolve`), and
    widened contexts may grow their entry state. The class and instance attribute tables,
    the heap and `extern` (writes through `global` and `nonlocal`) may grow too. The
    environment, return value and edges are not written back; `solver.step` joins them.
    Pass `state.copy()` to keep a state untouched.

    env  environment to start from and join; defaults to the one stored for `node` in `state`
    """
    it = Interp(prog, state, node)
    it.env = dict(state.entry.get(node, {})) if node.ctx.merged else dict(node.ctx.params)
    known = state.envs.get(node, {}) if env is None else env
    if state.flow:  # callee writes are applied at call sites; `known` only joins the summary
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
