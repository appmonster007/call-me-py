"""Call graph pass: global fixpoint over (function, context) nodes.

`solve` maps a State to a State, so passes can be chained:

    s = solve(prog)                                   # reachable code
    s = solve(prog, s, include_unreachable=True)      # extend with dead code
    assert solve(prog, s).same_result(s)              # already a fixpoint

Every pass only joins (never overwrites), so results grow monotonically and
re-running a pass on its own output changes nothing. To combine this analysis
with others, use `CallGraphPass` with `pipeline.run_passes`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .extract import Program
from .model import Ctx, Edge, Node
from .pipeline import Facts, run_passes
from .transfer import EMPTY, State, analyze

DEFAULT_MAX_CONTEXTS = 16


def init_state(prog: Program, state: Optional[State] = None, *,
               include_unreachable: bool = False, max_contexts: Optional[int] = None) -> State:
    """Validate/create a state for `prog` and register its root nodes (idempotent)."""
    if state is None:
        state = State(DEFAULT_MAX_CONTEXTS if max_contexts is None else max_contexts,
                      prog.fingerprint)
    else:
        if state.program is not None and state.program != prog.fingerprint:
            raise ValueError("state was produced for a different program (or another version of it)")
        if max_contexts is not None and max_contexts != state.max_ctx:
            raise ValueError(f"max_contexts={max_contexts} conflicts with state.max_ctx={state.max_ctx}")
        state.program = prog.fingerprint
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


@dataclass
class CallGraphPass:
    """The call graph analysis as a pipeline pass (state: `State`)."""

    include_unreachable: bool = False
    max_contexts: Optional[int] = None
    name: str = "callgraph"

    def init(self, prog: Program, facts: Facts, state: Optional[State]) -> State:
        return init_state(prog, state, include_unreachable=self.include_unreachable,
                          max_contexts=self.max_contexts)

    def step(self, prog: Program, facts: Facts) -> bool:
        return step(prog, facts[self.name]).changed


def solve(prog: Program, state: Optional[State] = None, *, include_unreachable: bool = False,
          max_contexts: Optional[int] = None, max_rounds: int = 10_000,
          inplace: bool = False) -> State:
    """Run the call graph pass to a fixpoint, starting from `state` (or an empty one).

    The input state is copied unless `inplace=True`. max_contexts defaults to 16
    for a fresh state and to the state's own value when one is given.
    """
    p = CallGraphPass(include_unreachable, max_contexts)
    facts = Facts(prog.fingerprint, {p.name: state} if state is not None else None)
    return run_passes(prog, [p], facts, max_rounds=max_rounds, inplace=inplace)[p.name]
