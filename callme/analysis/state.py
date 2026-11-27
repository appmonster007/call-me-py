"""The global solver state: every table the analysis fills, and the joins that grow them.

`State` holds contexts, environments, return values, call edges, the class and instance
attribute tables, the heap and the writes to other scopes. `mk_closure` makes the closure
token for a `def` or `lambda`, widening its parent past `MAX_DEPTH` nested contexts.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..model import (
    EMPTY,
    Box,
    ClassId,
    Closure,
    Ctx,
    Edge,
    FuncId,
    Instance,
    Node,
    ParamState,
    Site,
    Vals,
)

Env = dict[str, Vals]
Writes = dict[FuncId, Env]  # owner function -> {name: values written by a node / its callees}
MAX_DEPTH = 3  # nesting of captured contexts before widening to "unknown parent"


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
    unknown_decos: set[tuple[Node, Site]]  # decorator sites confirmed unresolved -> identity
    pending_decos: set[tuple[Node, Site]]  # transient: empty decorator sites seen in current round
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
        self.unknown_decos = set()
        self.pending_decos = set()
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
        new.unknown_decos = set(self.unknown_decos)
        return new

    def same_result(self, other: State) -> bool:
        """Return True if both states hold the same results, ignoring node order and `changed`."""
        return (set(self.order) == set(other.order) and self.envs == other.envs
                and self.ret == other.ret and self.edges == other.edges
                and self.entry == other.entry and self.class_attrs == other.class_attrs
                and self.inst_attrs == other.inst_attrs and self.heap == other.heap
                and self.extern == other.extern and self.writes == other.writes
                and self.bases == other.bases and self.metaclasses == other.metaclasses
                and self.unknown_decos == other.unknown_decos)

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
