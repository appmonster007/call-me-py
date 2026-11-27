"""Multi-pass driver: run several analyses over one program to a joint fixpoint.

A *pass* owns one state in a shared `Facts` store and may read the states of
other passes (e.g. a flow-sensitive pass reads the call graph). `run_passes`
steps every pass once per round and stops when no pass changed anything, so
passes that feed each other (call graph <-> data flow) are alternated until
both are stable.

Consistency rules every pass must follow:
  * `init` is idempotent: given an existing state it only validates and adds roots;
  * `step` only joins into states (results grow monotonically, never shrink);
  * states implement `copy()` and `same_result(other)` (the `PassState` protocol).
Under these rules `run_passes(prog, ps, run_passes(prog, ps))` returns the same
facts, and any pass's output can be fed back as its input.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Protocol, Self

from .extract import Program


class PassState(Protocol):
    """What the driver needs from the state of a pass: a copy, and a comparison of results."""

    def copy(self) -> Self:
        """Return an independent copy, which later steps can change without affecting this one."""
        ...

    def same_result(self, other: Self, /) -> bool:
        """Return True if `other` holds the same results as this state."""
        ...


class Pass(Protocol):
    """An analysis that `run_passes` steps to a joint fixpoint with other passes.

    name  key of the pass's state in `Facts`
    """

    name: str  # key of this pass's state in Facts

    def init(self, prog: Program, facts: Facts, state: PassState | None, /) -> PassState:
        """Return the pass's state for `prog`: a fresh one, or `state` validated and extended.

        `state` is None on a first run. Given an existing state, `init` only validates it and
        adds roots, so calling it again changes nothing. Raise if the state does not fit.
        """
        ...

    def step(self, prog: Program, facts: Facts, /) -> bool:
        """Run one round, joining new results into `facts[self.name]`; return True if it changed.

        A step may read the states of other passes in `facts`, and it only joins, so results
        never shrink.
        """
        ...


class Facts:
    """The states of all passes for one program version, keyed by pass name."""

    program: str  # Program.fingerprint
    states: dict[str, PassState]

    def __init__(self, program: str, states: Mapping[str, PassState] | None = None) -> None:
        """Create a store for the program with fingerprint `program`.

        states  initial states by pass name; the mapping is copied, the states are not
        """
        self.program = program
        self.states = dict(states or {})

    def __getitem__(self, name: str) -> PassState:
        return self.states[name]

    def __contains__(self, name: str) -> bool:
        return name in self.states

    def get[S: PassState](self, name: str, kind: type[S]) -> S:
        """Return the state of pass `name`, raising `TypeError` if it is not a `kind`."""
        state = self.states[name]
        if not isinstance(state, kind):
            raise TypeError(f"state of pass {name!r} is a {type(state).__name__}, "
                            + f"not a {kind.__name__}")
        return state

    def copy(self) -> Facts:
        """Return a copy holding a copy of every pass state."""
        return Facts(self.program, {k: v.copy() for k, v in self.states.items()})

    def same_result(self, other: Facts) -> bool:
        """Return True if both stores are for the same program and hold the same results."""
        return (self.program == other.program and self.states.keys() == other.states.keys()
                and all(v.same_result(other.states[k]) for k, v in self.states.items()))


def run_passes(prog: Program, passes: Iterable[Pass], facts: Facts | None = None, *,
               max_rounds: int = 10_000, inplace: bool = False,
               on_round: Callable[[int, Facts], None] | None = None) -> Facts:
    """Step all `passes` in rounds until none of them changes, and return the facts.

    Each pass is initialised first (`Pass.init`), then every round steps each pass once, in
    order. States of passes that are not listed are kept as they are.

    facts       earlier results for the same program to resume from; raises `ValueError` if
                they were produced for another program or version of it
    max_rounds  maximum number of rounds before raising `RuntimeError`
    inplace     update `facts` itself instead of a copy
    on_round    called as `on_round(round, facts)` after each round, the last one included
    """
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
    for rnd in range(max_rounds):
        changed = False
        for p in passes:
            changed = p.step(prog, facts) or changed
        if on_round is not None:
            on_round(rnd, facts)
        if not changed:
            return facts
    raise RuntimeError(f"passes did not converge: {[p.name for p in passes]}")
