"""Step 2: abstract interpretation of every (function, context) node to a global fixpoint.

The modules: `state` (`State`, the global tables), `interp` (`Interp`, which runs one node,
and `analyze`), `pipeline` (the generic multi-pass driver) and `solver` (the call graph pass,
`solve`). Import the names from this package.
"""
from __future__ import annotations

from .interp import Interp, analyze
from .pipeline import Facts, Pass, PassState, run_passes
from .solver import DEFAULT_MAX_CONTEXTS, CallGraphPass, init_state, solve, step
from .state import MAX_DEPTH, Env, State, Writes, join_envs, mk_closure

__all__ = [
    "Interp", "analyze",
    "Facts", "Pass", "PassState", "run_passes",
    "DEFAULT_MAX_CONTEXTS", "CallGraphPass", "init_state", "solve", "step",
    "MAX_DEPTH", "Env", "State", "Writes", "join_envs", "mk_closure",
]
