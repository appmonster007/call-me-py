"""Layer 3 of the interpreter, `CallLayer`: calls."""
from __future__ import annotations

import ast
from abc import ABC
from collections.abc import Mapping, Sequence
from typing import override

from ...model import (
    EMPTY,
    Alloc,
    BoundMethod,
    Box,
    Builtin,
    ClassMethod,
    ClassObj,
    Closure,
    Ctx,
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
)
from ...stubs import STUBS, Args
from ..state import Env, Writes
from .base import bind_attr
from .modules import ModuleLayer


class CallLayer(ModuleLayer, ABC):
    """Layer 3: calls. Binding arguments to parameters (with default values),
    creating instances, and `apply`, which calls every callable a value may be.
    """

    # fields of BaseLayer that this layer assigns
    _weak_depth: int

    def default_value(self, tok: Closure, d: ast.expr) -> Vals:
        """Evaluate default value `d` of closure `tok` in read-only mode, where it was defined.

        With a known parent node, the default is evaluated in that node's summary environment;
        otherwise in the enclosing function, joined over all its contexts.
        """
        if tok.parent is not None:
            it = type(self)(self.prog, self.state, tok.parent, readonly=True)
            it.env = dict(self.state.envs.get(tok.parent, {}))
        else:
            it = type(self)(self.prog, self.state, None, readonly=True,
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
                bound_new = frozenset(bind_attr(t, tok.cls, tok) for t in new_fn)
                created = self.apply(bound_new, [frozenset([tok]), *pos], star, kws, kwstar, site)
            else:
                created = frozenset([Instance(tok.cls, Alloc.of(site))])
            for obj in created:
                if isinstance(obj, Instance) and tok.cls in self.state.mro(obj.cls):
                    init = self.state.lookup_class_attr(obj.cls, "__init__")
                    if init:
                        bound_init = frozenset(bind_attr(t, obj.cls, obj) for t in init)
                        self.apply(bound_init, pos, star, kws, kwstar, site)
            return created
        finally:
            self._weak_depth -= 1

    @override
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
                                meta_calls.add(bind_attr(fn, m, tok))
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
                        bound_call = frozenset(bind_attr(t, tok.cls, tok) for t in call_fn if t != tok)
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
