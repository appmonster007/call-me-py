"""Global fixpoint over (function, context) nodes: return values + call edges.

`solve` maps a State to a State, so passes can be chained:

    s = solve(prog)                                   # reachable code
    s = solve(prog, s, include_unreachable=True)      # extend with dead code
    assert solve(prog, s).same_result(s)              # already a fixpoint

Every pass only joins (never overwrites), so results grow monotonically and
re-running a pass on its own output changes nothing.
"""
from __future__ import annotations

from typing import Optional

from .extract import Program
from .model import Ctx, Edge, Node
from .transfer import EMPTY, State, analyze


def init_state(prog: Program, state: Optional[State] = None, *,
               include_unreachable: bool = False, max_contexts: Optional[int] = None) -> State:
    """Validate/create a state for `prog` and register its root nodes (idempotent)."""
    if state is None:
        state = State(16 if max_contexts is None else max_contexts, prog.module)
    else:
        if state.module is not None and state.module != prog.module:
            raise ValueError("state was produced for a different program")
        if max_contexts is not None and max_contexts != state.max_ctx:
            raise ValueError(f"max_contexts={max_contexts} conflicts with state.max_ctx={state.max_ctx}")
        state.module = prog.module
    mod = Node(prog.module, Ctx())
    state.register(mod)
    if include_unreachable:  # also analyse never-called functions (static-only edges)
        for fid, info in prog.funcs.items():
            if fid != prog.module:
                state.register(Node(fid, Ctx((), mod if info.parent == prog.module else None)))
    return state


def step(prog: Program, state: State) -> State:
    """One round over all known nodes, joined into `state` (in place). Sets state.changed."""
    state.changed = False
    for node in list(state.order):
        env, ret, calls = analyze(node, state, prog)
        old_env = state.envs.get(node, {})
        new_env = dict(old_env)
        for k, v in env.items():
            new_env[k] = new_env.get(k, EMPTY) | v
        if new_env != old_env:
            state.envs[node] = new_env
            state.changed = True
        old_ret = state.ret.get(node, EMPTY)
        if not ret <= old_ret:
            state.ret[node] = old_ret | ret
            state.changed = True
        for site, callee in calls:
            edge = Edge(node, site, callee)
            if edge not in state.edges:
                state.edges.add(edge)
                state.changed = True
    return state


def solve(prog: Program, state: Optional[State] = None, *, include_unreachable: bool = False,
          max_contexts: Optional[int] = None, max_rounds: int = 10_000,
          inplace: bool = False) -> State:
    """Run `step` to a fixpoint, starting from `state` (or an empty one).

    The input state is copied unless `inplace=True`. max_contexts defaults to 16
    for a fresh state and to the state's own value when one is given.
    """
    if state is not None and not inplace:
        state = state.copy()
    state = init_state(prog, state, include_unreachable=include_unreachable,
                       max_contexts=max_contexts)
    for _ in range(max_rounds):
        step(prog, state)
        if not state.changed:
            return state
    raise RuntimeError("call graph analysis did not converge")
