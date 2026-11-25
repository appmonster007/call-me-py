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
        self._mro_cache = {}
        self.changed = False

    def add_class_attr(self, cls: ClassId, name: str, vals: Vals) -> None:
        if not vals:
            return
        attrs = self.class_attrs.setdefault(cls, {})
        old = attrs.get(name, EMPTY)
        if not vals <= old:
            attrs[name] = old | vals
            self.changed = True

    def add_inst_attr(self, cls: ClassId, name: str, vals: Vals) -> None:
        if not vals:
            return
        attrs = self.inst_attrs.setdefault(cls, {})
        old = attrs.get(name, EMPTY)
        if not vals <= old:
            attrs[name] = old | vals
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
            vals = self.class_attrs.get(c, {}).get(name, EMPTY)
            if vals:
                return vals
        return EMPTY

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

    def set_attr(self, objs: Vals, attr: str, vals: Vals) -> None:
        if not vals or self.readonly:
            return
        for t in objs:
            if isinstance(t, Instance):
                self.state.add_inst_attr(t.cls, attr, vals)
            elif isinstance(t, ClassObj):
                self.state.add_class_attr(t.cls, attr, vals)

    def assign(self, t: ast.expr, vals: Vals) -> None:
        if isinstance(t, ast.Name):
            self.bind(t.id, vals)
        elif isinstance(t, ast.Attribute):
            self.set_attr(self.eval(t.value), t.attr, vals)
        elif isinstance(t, ast.Subscript):
            self.eval(t.slice)
            if isinstance(t.value, (ast.Name, ast.Attribute, ast.Subscript)):
                self.assign(t.value, vals)
            else:
                self.eval(t.value)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for x in t.elts:
                self.assign(x, vals)
        elif isinstance(t, ast.Starred):
            self.assign(t.value, vals)
        else:
            self.eval(t)

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
                out |= created
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
            if isinstance(tok, (ClassMethod, Super)):
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
        self.eval(e.slice)
        return self.eval(e.value)

    def e_Starred(self, e: ast.Starred) -> Vals:
        return self.eval(e.value)

    def e_Await(self, e: ast.Await) -> Vals:
        return self.eval(e.value)

    def e_Attribute(self, e: ast.Attribute) -> Vals:
        v = self.eval(e.value)
        if not isinstance(e.ctx, ast.Load):
            return EMPTY
        out: set[Token] = set()
        for t in v:
            if isinstance(t, Builtin) and f"{t.name}.{e.attr}" in STUBS:
                out.add(Builtin(f"{t.name}.{e.attr}"))
            elif isinstance(t, ClassObj):
                for attr_tok in self.state.lookup_class_attr(t.cls, e.attr):
                    out.add(_bind_attr(attr_tok, t.cls, t))
            elif isinstance(t, Instance):
                out.update(self.state.inst_attrs.get(t.cls, {}).get(e.attr, EMPTY))
                for attr_tok in self.state.lookup_class_attr(t.cls, e.attr):
                    out.add(_bind_attr(attr_tok, t.cls, t))
            elif isinstance(t, Super):
                found = self.state.lookup_class_attr(t.receiver.cls, e.attr, after=t.start)
                if found:
                    for attr_tok in found:
                        out.add(_bind_attr(attr_tok, t.receiver.cls, t.receiver))
                elif e.attr == "__new__":
                    out.add(Builtin("object.__new__"))
        if e.attr in METHOD_STUBS and not out:  # xs.sort(key=...), d.get(k, default)
            out.add(Builtin(METHOD_STUBS[e.attr], (v,)))
        return frozenset(out)

    def e_Yield(self, e: ast.Yield | ast.YieldFrom) -> Vals:
        self.ret |= self.eval(e.value)
        return EMPTY

    e_YieldFrom = e_Yield

    def _comp(self, gens: list[ast.comprehension]) -> None:
        for g in gens:
            self.assign(g.target, self.eval(g.iter))
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
        for b in s.bases:
            for t in sorted(self.eval(b), key=token_label):
                if isinstance(t, ClassObj):
                    base_ids.append(t.cls)
        for k in s.keywords:
            self.eval(k.value)
        if not self.readonly and base_ids:
            self.state.add_bases(cid, base_ids)
        self.class_stack.append((cid, {}))
        self.block(s.body)
        self.class_stack.pop()
        vals: Vals = frozenset([ClassObj(cid)])
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
        self.assign(s.target, self.eval(s.value))

    def s_AnnAssign(self, s: ast.AnnAssign) -> None:
        if s.value is not None:
            self.assign(s.target, self.eval(s.value))

    def s_Return(self, s: ast.Return) -> None:
        self.ret |= self.eval(s.value)

    def s_For(self, s: ast.For | ast.AsyncFor) -> None:
        self.assign(s.target, self.eval(s.iter))
        self.block(s.body)
        self.block(s.orelse)

    s_AsyncFor = s_For

    def s_With(self, s: ast.With | ast.AsyncWith) -> None:
        for it in s.items:
            v = self.eval(it.context_expr)
            if it.optional_vars is not None:
                self.assign(it.optional_vars, v)
        self.block(s.body)

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
