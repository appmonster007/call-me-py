"""Abstract interpretation of one function body in one context.

An abstract value is a set of tokens ("which functions may this expression
be?"): `Closure`s for user code, `Builtin`s for callables modelled in `callme.stubs`,
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

The interpreter is split into layers, one class per module, each subclassing the one below
(`base.BaseLayer` documents the chain): `names`, `modules`, `calls`, `attributes`,
`expressions`, `operators`, `statements`, `definitions` and `flow`. `Interp` is the last.
"""
from __future__ import annotations

from typing import final

from ...frontend import Program
from ...model import EMPTY, Node, Vals
from ..state import Env, State, join_envs
from .base import Calls
from .flow import FlowLayer


@final
class Interp(FlowLayer):
    """Abstract interpreter for one node: a function body run under one context.

    It evaluates expressions to `Vals`, collects the call edges it finds in `calls`, and
    writes shared facts (contexts, attributes, the heap) into `state`. In flow mode, `env` is
    the environment at the current point, `summary` collects every value each local takes,
    and `live` is False once the current path has ended.
    """


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
        for site in it.empty_decos:
            if (node, site) not in state.unknown_decos:
                state.pending_decos.add((node, site))
        return it.summary, it.ret, it.calls
    for name, vals in state.extern.get(node.func, {}).items():  # global / nonlocal writes
        it.env[name] = it.env.get(name, EMPTY) | vals
    for name, vals in known.items():
        it.env[name] = it.env.get(name, EMPTY) | vals
    while True:
        snapshot = dict(it.env)
        it.ret, it.calls, it.empty_decos = EMPTY, [], set()
        it.run()
        if it.env == snapshot:
            for site in it.empty_decos:
                if (node, site) not in state.unknown_decos:
                    state.pending_decos.add((node, site))
            return it.env, it.ret, it.calls
