"""Save a solver state (or a whole Facts store) to JSON and load it back.

Loading needs the Program it was produced for: function ids are stored as
FuncId.uid strings and resolved against `prog.funcs`, and the program
fingerprint must match, so a state cannot be resumed on edited source.

Nodes and closures reference each other (a closure holds its defining node, a
context holds closures), so both live in one table `values`, written in
dependency order; everything else refers to table indices.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from .extract import Program
from .model import Closure, Ctx, Edge, Node, Site
from .pipeline import Facts
from .transfer import State

FORMAT, VERSION = "callme-state", 1


# ---------------------------------------------------------------- State
class _Encoder:
    def __init__(self) -> None:
        self.values: List[list] = []
        self.ids: Dict[Any, int] = {}

    def node(self, n: Node) -> int:
        if n not in self.ids:
            params = self.params(n.ctx.params)
            parent = self.opt_node(n.ctx.parent)
            self.ids[n] = len(self.values)
            self.values.append(["node", n.func.uid, params, parent, n.ctx.merged])
        return self.ids[n]

    def opt_node(self, n: Optional[Node]) -> Optional[int]:
        return None if n is None else self.node(n)

    def closure(self, c: Closure) -> int:
        key = ("clo", c)
        if key not in self.ids:
            parent = self.opt_node(c.parent)
            self.ids[key] = len(self.values)
            self.values.append(["clo", c.func.uid, parent, c.depth])
        return self.ids[key]

    def toks(self, ts) -> List[int]:
        return sorted(self.closure(t) for t in ts)

    def params(self, ps) -> list:
        return [[name, self.toks(ts)] for name, ts in ps]

    def env(self, env) -> dict:
        return {k: self.toks(v) for k, v in sorted(env.items())}


def _site(s: Site) -> list:
    return [s.caller.uid, s.line, s.col, s.text, s.kind]


def state_to_dict(state: State) -> dict:
    enc = _Encoder()
    order = [enc.node(n) for n in state.order]
    envs = [[enc.node(n), enc.env(e)] for n, e in state.envs.items()]
    ret = [[enc.node(n), enc.toks(v)] for n, v in state.ret.items()]
    entry = [[enc.node(n), enc.env(e)] for n, e in state.entry.items()]
    edges = sorted([enc.node(e.caller), _site(e.site), enc.node(e.callee)] for e in state.edges)
    return {"format": FORMAT, "version": VERSION, "program": state.program,
            "max_ctx": state.max_ctx, "values": enc.values, "order": order,
            "envs": envs, "ret": ret, "entry": entry, "edges": edges}


def state_from_dict(prog: Program, d: dict) -> State:
    if d.get("format") != FORMAT or d.get("version") != VERSION:
        raise ValueError(f"not a {FORMAT} v{VERSION} document")
    if d["program"] != prog.fingerprint:
        raise ValueError("state was produced for a different program (or another version of it)")
    funcs = {f.uid: f for f in prog.funcs}

    def func(uid):
        if uid not in funcs:
            raise ValueError(f"unknown function {uid}")
        return funcs[uid]

    vals: List[Any] = []

    def toks(ids) -> frozenset:
        return frozenset(vals[i] for i in ids)

    for v in d["values"]:
        if v[0] == "node":
            _, uid, params, parent, merged = v
            ctx = Ctx(tuple((name, toks(ids)) for name, ids in params),
                      None if parent is None else vals[parent], merged)
            vals.append(Node(func(uid), ctx))
        else:
            _, uid, parent, depth = v
            vals.append(Closure(func(uid), None if parent is None else vals[parent], depth))

    def env(e) -> dict:
        return {k: toks(ids) for k, ids in e.items()}

    state = State(d["max_ctx"], d["program"])
    for i in d["order"]:
        state.register(vals[i])
    state.envs = {vals[i]: env(e) for i, e in d["envs"]}
    state.ret = {vals[i]: toks(ids) for i, ids in d["ret"]}
    state.entry = {vals[i]: env(e) for i, e in d["entry"]}
    state.edges = {Edge(vals[c], Site(func(s[0]), *s[1:]), vals[t]) for c, s, t in d["edges"]}
    state.changed = False
    return state


def save_state(state: State, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state_to_dict(state), f)


def load_state(prog: Program, path) -> State:
    with open(path, encoding="utf-8") as f:
        return state_from_dict(prog, json.load(f))


# ---------------------------------------------------------------- Facts
Codec = Tuple[Callable[[Any], dict], Callable[[Program, dict], Any]]
CODECS: Dict[str, Codec] = {"callgraph": (state_to_dict, state_from_dict)}  # pass name -> codec


def facts_to_dict(facts: Facts, codecs: Dict[str, Codec] = CODECS) -> dict:
    missing = set(facts.states) - set(codecs)
    if missing:
        raise ValueError(f"no codec for passes {sorted(missing)}")
    return {"format": "callme-facts", "version": VERSION, "program": facts.program,
            "states": {k: codecs[k][0](v) for k, v in facts.states.items()}}


def facts_from_dict(prog: Program, d: dict, codecs: Dict[str, Codec] = CODECS) -> Facts:
    if d.get("format") != "callme-facts" or d["program"] != prog.fingerprint:
        raise ValueError("not a callme-facts document for this program")
    return Facts(d["program"], {k: codecs[k][1](prog, v) for k, v in d["states"].items()})
