"""Global fixpoint over (function, context) nodes: return values + call edges."""
from __future__ import annotations

from collections.abc import Callable

from .extract import Program
from .model import Ctx, Edge, Node
from .transfer import EMPTY, State, analyze


def solve(prog: Program, *, include_unreachable: bool = False,
          max_contexts: int = 16, max_rounds: int = 10_000,
          on_round: Callable[[int, State], None] | None = None) -> State:
    """Re-analyse every node until a whole round changes nothing.

    `on_round(round, state)` (optional) is called after each round, e.g. to record a trace.
    """
    # print(prog)
    state = State(max_contexts)
    mod = Node(prog.module, Ctx())
    state.register(mod)
    if include_unreachable:  # also analyse never-called functions (static-only edges)
        for fid, info in prog.funcs.items():
            if fid != prog.module:
                state.register(Node(fid, Ctx((), mod if info.parent == prog.module else None)))
    for rnd in range(max_rounds):
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
        if on_round is not None:
            on_round(rnd, state)
        if not state.changed:
            return state
    raise RuntimeError("call graph analysis did not converge")
