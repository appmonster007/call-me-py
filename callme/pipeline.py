"""Multi-pass driver: run several analyses over one program to a joint fixpoint.

A *pass* owns one state in a shared `Facts` store and may read the states of
other passes (e.g. a flow-sensitive pass reads the call graph). `run_passes`
steps every pass once per round and stops when no pass changed anything, so
passes that feed each other (call graph <-> data flow) are alternated until
both are stable.

Consistency rules every pass must follow:
  * `init` is idempotent: given an existing state it only validates and adds roots;
  * `step` only joins into states (results grow monotonically, never shrink);
  * states implement `copy()` and `same_result(other)`.
Under these rules `run_passes(prog, ps, run_passes(prog, ps))` returns the same
facts, and any pass's output can be fed back as its input.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Protocol

from .extract import Program


class Pass(Protocol):
    name: str  # key of this pass's state in Facts

    def init(self, prog: Program, facts: "Facts", state: Optional[Any]) -> Any:
        """Return a fresh state (state is None) or validate and extend `state`."""

    def step(self, prog: Program, facts: "Facts") -> bool:
        """One round on facts[self.name]; may read other passes' states. True if changed."""


class Facts:
    """States of all passes for one program version, keyed by pass name."""

    def __init__(self, program: str, states: Optional[Dict[str, Any]] = None) -> None:
        self.program = program  # Program.fingerprint
        self.states: Dict[str, Any] = dict(states or {})

    def __getitem__(self, name: str) -> Any:
        return self.states[name]

    def __contains__(self, name: str) -> bool:
        return name in self.states

    def copy(self) -> "Facts":
        return Facts(self.program, {k: v.copy() for k, v in self.states.items()})

    def same_result(self, other: "Facts") -> bool:
        return (self.program == other.program and self.states.keys() == other.states.keys()
                and all(v.same_result(other.states[k]) for k, v in self.states.items()))


def run_passes(prog: Program, passes: Iterable[Pass], facts: Optional[Facts] = None, *,
               max_rounds: int = 10_000, inplace: bool = False) -> Facts:
    """Step all `passes` round-robin until none changes. The input facts are
    copied unless `inplace=True`; states of passes not listed are kept as they are."""
    passes = list(passes)
    if facts is None:
        facts = Facts(prog.fingerprint)
    else:
        if facts.program != prog.fingerprint:
            raise ValueError("facts were produced for a different program (or another version of it)")
        if not inplace:
            facts = facts.copy()
    for p in passes:
        facts.states[p.name] = p.init(prog, facts, facts.states.get(p.name))
    for _ in range(max_rounds):
        changed = False
        for p in passes:
            changed = p.step(prog, facts) or changed
        if not changed:
            return facts
    raise RuntimeError(f"passes did not converge: {[p.name for p in passes]}")
