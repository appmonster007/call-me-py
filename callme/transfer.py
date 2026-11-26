"""Abstract interpretation of one function body in one context.

An abstract value is a set of `Closure` tokens ("which functions may this
expression be?"). Containers are collapsed to the union of their elements.
Calls are context sensitive, with the callee's abstract entry state as context:

  entering  {param_i -> eval(arg_i)} becomes the callee's context
  returning the call evaluates to the callee's return value for that context
"""
from __future__ import annotations

import ast
from typing import Dict, FrozenSet, List, Optional, Tuple

from .extract import Program, make_site, pattern_names
from .model import Closure, Ctx, Node, ParamState, Site

EMPTY: FrozenSet[Closure] = frozenset()
MAX_DEPTH = 3  # nesting of captured contexts before widening to "unknown parent"

Env = Dict[str, FrozenSet[Closure]]


class State:
    """Global fixpoint state: contexts, local envs, return values, call edges."""

    def __init__(self, max_ctx: int = 16, module=None) -> None:
        self.max_ctx = max_ctx
        self.module = module  # FuncId of the program this state belongs to
        self.order: List[Node] = []
        self.by_func: Dict = {}
        self.envs: Dict[Node, Env] = {}
        self.ret: Dict[Node, FrozenSet[Closure]] = {}  # return value per node
        self.edges: set = set()
        self.entry: Dict[Node, Env] = {}  # entry states of widened contexts
        self.changed = False

    def copy(self) -> "State":
        """Independent copy (values are immutable, containers are not)."""
        new = State(self.max_ctx, self.module)
        new.order = list(self.order)
        new.by_func = {f: set(g) for f, g in self.by_func.items()}
        new.envs = {n: dict(e) for n, e in self.envs.items()}
        new.ret = dict(self.ret)
        new.edges = set(self.edges)
        new.entry = {n: dict(e) for n, e in self.entry.items()}
        return new

    def same_result(self, other: "State") -> bool:
        """Equal analysis results (ignores node registration order and `changed`)."""
        return (set(self.order) == set(other.order) and self.envs == other.envs
                and self.ret == other.ret and self.edges == other.edges
                and self.entry == other.entry)

    def register(self, node: Node) -> None:
        group = self.by_func.setdefault(node.func, set())
        if node not in group:
            group.add(node)
            self.order.append(node)
            self.changed = True

    def resolve(self, func, params: ParamState, parent: Optional[Node]) -> Node:
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

    def union_env(self, func, name: str) -> FrozenSet[Closure]:
        out = EMPTY
        for n in self.by_func.get(func, ()):
            out |= self.envs.get(n, {}).get(name, EMPTY)
        return out


def mk_closure(func, node: Optional[Node]) -> Closure:
    if node is None:
        return Closure(func, None)
    depth = 1 + max((t.depth for _, ts in node.ctx.params for t in ts), default=0)
    if depth > MAX_DEPTH:
        return Closure(func, None)
    return Closure(func, node, depth)


class Interp:
    def __init__(self, prog: Program, state: State, node: Optional[Node], *,
                 readonly: bool = False, func=None) -> None:
        self.prog, self.state, self.node, self.readonly = prog, state, node, readonly
        self.info = prog.funcs[node.func if node is not None else func]
        self.env: Env = {}
        self.ret = EMPTY
        self.calls: List[Tuple[Site, Node]] = []
        self.in_class = 0

    # ------------------------------------------------------------ names
    def bind(self, name: str, vals) -> None:
        if vals and self.in_class == 0 and name in self.info.locals:
            self.env[name] = self.env.get(name, EMPTY) | vals

    def lookup(self, name: str):
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
        return EMPTY  # builtin / unresolved

    def assign(self, t: ast.AST, vals) -> None:
        if isinstance(t, ast.Name):
            self.bind(t.id, vals)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for x in t.elts:
                self.assign(x, vals)
        elif isinstance(t, ast.Starred):
            self.assign(t.value, vals)
        else:
            self.eval(t)

    # ------------------------------------------------------------ calls
    def default_value(self, tok: Closure, d: ast.expr):
        if tok.parent is not None:
            it = Interp(self.prog, self.state, tok.parent, readonly=True)
            it.env = dict(self.state.envs.get(tok.parent, {}))
        else:
            it = Interp(self.prog, self.state, None, readonly=True,
                        func=self.prog.funcs[tok.func].parent)
        return it.eval(d)

    def bind_args(self, tok: Closure, pos, star, kws, kwstar) -> Optional[ParamState]:
        sig = self.prog.funcs[tok.func].sig
        vals: Env = {}
        assigned = set()

        def put(n, v):
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

    def apply(self, callee, pos, star, kws, kwstar, site: Site):
        out = EMPTY
        for tok in callee:
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
    def eval(self, e):
        if e is None:
            return EMPTY
        m = getattr(self, "e_" + type(e).__name__, None)
        return m(e) if m else self.generic(e)

    def generic(self, e):
        for c in ast.iter_child_nodes(e):
            if isinstance(c, ast.expr):
                self.eval(c)
        return EMPTY

    def e_Name(self, e):
        return self.lookup(e.id) if isinstance(e.ctx, ast.Load) else EMPTY

    def e_Lambda(self, e):
        fid = self.prog.by_node[id(e)]
        for d in [*e.args.defaults, *[k for k in e.args.kw_defaults if k is not None]]:
            self.eval(d)
        return frozenset([mk_closure(fid, self.node)])

    def e_Call(self, e):
        callee = self.eval(e.func)
        pos, star, kws, kwstar = [], None, {}, None  # None = no */** at the call
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
        return self.apply(callee, pos, star, kws, kwstar, make_site(self.info.id, e))

    def e_IfExp(self, e):
        self.eval(e.test)
        return self.eval(e.body) | self.eval(e.orelse)

    def e_BoolOp(self, e):
        out = EMPTY
        for v in e.values:
            out |= self.eval(v)
        return out

    def e_NamedExpr(self, e):
        v = self.eval(e.value)
        self.assign(e.target, v)
        return v

    def _union(self, elts):
        out = EMPTY
        for x in elts:
            out |= self.eval(x)
        return out

    def e_List(self, e):
        return self._union(e.elts)

    e_Tuple = e_Set = e_List

    def e_Dict(self, e):
        for k in e.keys:
            if k is not None:
                self.eval(k)
        return self._union(e.values)

    def e_Subscript(self, e):
        self.eval(e.slice)
        return self.eval(e.value)

    def e_Starred(self, e):
        return self.eval(e.value)

    def e_Await(self, e):
        return self.eval(e.value)

    def e_Attribute(self, e):
        self.eval(e.value)
        return EMPTY

    def e_Yield(self, e):
        self.ret |= self.eval(e.value)
        return EMPTY

    e_YieldFrom = e_Yield

    def _comp(self, gens):
        for g in gens:
            self.assign(g.target, self.eval(g.iter))
            for c in g.ifs:
                self.eval(c)

    def e_ListComp(self, e):
        self._comp(e.generators)
        return self.eval(e.elt)

    e_SetComp = e_GeneratorExp = e_ListComp

    def e_DictComp(self, e):
        self._comp(e.generators)
        self.eval(e.key)
        return self.eval(e.value)

    # ------------------------------------------------------------ statements
    def block(self, stmts) -> None:
        for s in stmts:
            m = getattr(self, "s_" + type(s).__name__, None)
            m(s) if m else self.generic_stmt(s)

    def generic_stmt(self, s) -> None:
        for c in ast.iter_child_nodes(s):
            if isinstance(c, ast.expr):
                self.eval(c)
            elif isinstance(c, ast.stmt):
                self.block([c])
            elif isinstance(c, ast.ExceptHandler):
                self.eval(c.type)
                self.block(c.body)

    def s_FunctionDef(self, s):
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

    def s_ClassDef(self, s):
        decos = [self.eval(d) for d in s.decorator_list]
        for x in [*s.bases, *[k.value for k in s.keywords]]:
            self.eval(x)
        self.in_class += 1
        self.block(s.body)
        self.in_class -= 1
        for dec, callee in reversed(list(zip(s.decorator_list, decos))):
            # classes are not tracked as values: the decorator receives EMPTY
            self.apply(callee, [EMPTY], None, {}, None,
                       make_site(self.info.id, dec, "decorator"))

    def s_Match(self, s):
        subject = self.eval(s.subject)
        for case in s.cases:
            for n in ast.walk(case.pattern):
                if isinstance(n, ast.expr):  # value patterns, class names
                    self.eval(n)
                for name in pattern_names(n):
                    self.bind(name, subject)  # captures: some part of the subject
            self.eval(case.guard)
            self.block(case.body)

    def s_Assign(self, s):
        v = self.eval(s.value)
        for t in s.targets:
            self.assign(t, v)

    def s_AugAssign(self, s):
        self.assign(s.target, self.eval(s.value))

    def s_AnnAssign(self, s):
        if s.value is not None:
            self.assign(s.target, self.eval(s.value))

    def s_Return(self, s):
        self.ret |= self.eval(s.value)

    def s_For(self, s):
        self.assign(s.target, self.eval(s.iter))
        self.block(s.body)
        self.block(s.orelse)

    s_AsyncFor = s_For

    def s_With(self, s):
        for it in s.items:
            v = self.eval(it.context_expr)
            if it.optional_vars is not None:
                self.assign(it.optional_vars, v)
        self.block(s.body)

    s_AsyncWith = s_With

    def run(self) -> None:
        if self.info.kind == "lambda":
            self.ret = self.eval(self.info.node.body)
        else:
            self.block(self.info.body)


def analyze(node: Node, state: State, prog: Program, env: Optional[Env] = None):
    """Analyse one node to its local fixpoint -> (env, return value, [(site, callee)]).

    The local fixpoint starts from the entry state joined with `env` (default: the
    env already stored for `node` in `state`), so the returned env can be passed
    straight back in: analyze(n, s, p, analyze(n, s, p)[0]) is stable.
    """
    it = Interp(prog, state, node)
    it.env = dict(state.entry.get(node, {})) if node.ctx.merged else dict(node.ctx.params)
    for k, v in (state.envs.get(node, {}) if env is None else env).items():
        it.env[k] = it.env.get(k, EMPTY) | v
    while True:
        snapshot = dict(it.env)
        it.ret, it.calls = EMPTY, []
        it.run()
        if it.env == snapshot:
            return it.env, it.ret, it.calls
