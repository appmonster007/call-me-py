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

from collections.abc import Callable
from dataclasses import dataclass

from .extract import Program
from .model import Ctx, Edge, Node
from .pipeline import Facts, PassState, run_passes
from .transfer import EMPTY, State, analyze

DEFAULT_MAX_CONTEXTS = 16


def init_state(prog: Program, state: State | None = None, *,
               include_unreachable: bool = False, max_contexts: int | None = None,
               flow_sensitive: bool | None = None) -> State:
    """Return a state for `prog` with its root nodes registered, creating it if `state` is None.

    The module body is always a root. With `include_unreachable`, every other function is a
    root too, analysed with no known arguments, so code that is never called is also covered.
    Calling it again on its own result changes nothing.

    max_contexts    context budget of a new state (default 16)
    flow_sensitive  flow mode of a new state (default True)

    For an existing state both must be None or match its own value; any conflict, or a
    state made for another program, raises `ValueError`.
    """
    if state is None:
        state = State(DEFAULT_MAX_CONTEXTS if max_contexts is None else max_contexts,
                      prog.fingerprint, True if flow_sensitive is None else flow_sensitive)
    else:
        if state.program is not None and state.program != prog.fingerprint:
            raise ValueError("state was produced for a different program (or another version of it)")
        if max_contexts is not None and max_contexts != state.max_ctx:
            raise ValueError(f"max_contexts={max_contexts} conflicts with state.max_ctx={state.max_ctx}")
        if flow_sensitive is not None and flow_sensitive != state.flow:
            raise ValueError(f"flow_sensitive={flow_sensitive} conflicts with state.flow={state.flow}")
        state.program = prog.fingerprint
    mod = Node(prog.module, Ctx())
    state.register(mod)
    if include_unreachable:  # also analyse never-called functions (static-only edges)
        for fid, info in prog.funcs.items():
            if fid != prog.module:
                state.register(Node(fid, Ctx((), mod if info.parent == prog.module else None)))
    return state


def step(prog: Program, state: State) -> State:
    """Analyse every known node once and join the results into `state`, in place.

    Each node's environment, return value and call edges are joined into the state.
    `state.changed` is True afterwards if anything grew, including the contexts and tables
    that `analyze` writes directly. Return `state`.
    """
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
    """The call graph analysis as a pipeline pass, whose state is a `transfer.State`.

    The fields are passed to `init_state`; None for `max_contexts` or `flow_sensitive` means
    the default for a new state, or the value of a state being resumed.
    """

    include_unreachable: bool = False
    max_contexts: int | None = None
    flow_sensitive: bool | None = None  # None: True for a fresh state, else the state's own
    name: str = "callgraph"

    def init(self, prog: Program, _facts: Facts, state: PassState | None) -> State:
        """Return the pass state from `init_state`; raise `TypeError` for a non-`State` state."""
        if state is not None and not isinstance(state, State):
            raise TypeError(f"{self.name} state must be a State, not a {type(state).__name__}")
        return init_state(prog, state, include_unreachable=self.include_unreachable,
                          max_contexts=self.max_contexts, flow_sensitive=self.flow_sensitive)

    def step(self, prog: Program, facts: Facts) -> bool:
        """Run one solver round on the call graph state; return True if it changed."""
        return step(prog, facts.get(self.name, State)).changed


def solve(prog: Program, state: State | None = None, *, include_unreachable: bool = False,
          max_contexts: int | None = None, flow_sensitive: bool | None = None,
          max_rounds: int = 10_000, inplace: bool = False,
          on_round: Callable[[int, State], None] | None = None) -> State:
    """Run the call graph pass to a fixpoint, starting from `state` or from an empty state.

    The input state is copied unless `inplace` is set. `max_contexts` defaults to 16 and
    `flow_sensitive` to True for a new state; for a given state they default to its own
    values, and a different value raises `ValueError`.

    flow_sensitive  False treats each local as the union of all its bindings, which is
                    cheaper but less precise (see transfer.py)
    max_rounds      maximum number of rounds before raising `RuntimeError`
    on_round        called as `on_round(round, state)` after each round, e.g. to record a trace
    """
    p = CallGraphPass(include_unreachable, max_contexts, flow_sensitive)
    facts = Facts(prog.fingerprint, {p.name: state} if state is not None else None)
    hook: Callable[[int, Facts], None] | None = None if on_round is None else (
        lambda rnd, f: on_round(rnd, f.get(p.name, State)))
    return run_passes(prog, [p], facts, max_rounds=max_rounds, inplace=inplace,
                      on_round=hook).get(p.name, State)
