"""Save a solver state (or a whole Facts store) to JSON and load it back.

Loading needs the Program it was produced for: function and class ids are stored
as uid strings and resolved against `prog.funcs` / `prog.classes`, and the program
fingerprint must match, so a state cannot be resumed on edited source.

Nodes and tokens reference each other (a closure holds its defining node, a
context holds tokens, a bound method holds its function and receiver), so all of
them live in one table `values`, written in dependency order; everything else
refers to table indices. The document layout is spelled out by `StateDoc` and `Row`.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from typing import Literal, TypedDict, cast

from ..analysis import Env, Facts, PassState, State
from ..frontend import Program
from ..model import (
    Alloc,
    BoundMethod,
    Box,
    Builtin,
    ClassId,
    ClassMethod,
    ClassObj,
    Closure,
    Ctx,
    Edge,
    FuncId,
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

FORMAT, VERSION = "callme-state", 5  # v3: flow_sensitive; v4: module tokens; v5: writes, unknown_decos

Path = str | os.PathLike[str]

# ---------------------------------------------------------------- document layout
Ids = list[int]  # indices into the values table
EnvDoc = dict[str, Ids]
AllocRow = tuple[str, int, int] | None  # func uid, line, col
SiteRow = tuple[str, int, int, str, str]  # caller uid, line, col, text, kind
Row = (  # one entry of the values table, tagged by kind
    tuple[Literal["node"], str, list[tuple[str, Ids]], int | None, bool]  # uid, params, parent, merged
    | tuple[Literal["clo"], str, int | None, int]  # uid, parent node, depth
    | tuple[Literal["builtin"], str, list[Ids], list[tuple[str, Ids]]]  # name, bound, bound_kw
    | tuple[Literal["class"], str]  # class uid
    | tuple[Literal["inst"], str, AllocRow]  # class uid, allocation site
    | tuple[Literal["box"], str, AllocRow]  # container kind, allocation site
    | tuple[Literal["bound"], int, int]  # function, receiver
    | tuple[Literal["classmethod"], int]
    | tuple[Literal["staticmethod"], int]
    | tuple[Literal["super"], str, int]  # start class uid, receiver
    | tuple[Literal["property"], Ids, Ids, Ids]  # fget, fset, fdel
    | tuple[Literal["module"], str]  # dotted module name
)
ROW_LEN = {"node": 5, "clo": 4, "builtin": 4, "class": 2, "inst": 3, "box": 3, "bound": 3,
           "classmethod": 2, "staticmethod": 2, "super": 3, "property": 4,
           "module": 2}  # per Row kind


class StateDoc(TypedDict):
    """JSON layout of a saved `State`, format "callme-state".

    The header (`format`, `version`, `program`, `max_ctx`, `flow_sensitive`) is checked on
    loading. `values` holds every node and token once, as `Row`s; every other field mirrors
    the `State` table of the same name and refers to values by index. Function and class
    ids are stored as uids.
    """

    format: str
    version: int
    program: str | None
    max_ctx: int
    flow_sensitive: bool
    values: list[Row]
    order: Ids
    envs: list[tuple[int, EnvDoc]]
    ret: list[tuple[int, Ids]]
    entry: list[tuple[int, EnvDoc]]
    edges: list[tuple[int, SiteRow, int]]  # caller, site, callee
    class_attrs: list[tuple[str, EnvDoc]]
    inst_attrs: list[tuple[int, EnvDoc]]
    heap: list[tuple[int, Ids]]
    extern: list[tuple[str, EnvDoc]]
    writes: list[tuple[int, list[tuple[str, EnvDoc]]]]
    bases: list[tuple[str, list[str]]]
    metaclasses: list[tuple[str, list[str]]]
    unknown_decos: list[tuple[int, SiteRow]]  # node, decorator site


class FactsDoc(TypedDict):
    """JSON layout of a saved `Facts` store, format "callme-facts": one document per pass name."""

    format: str
    version: int
    program: str
    states: dict[str, object]  # pass name -> that pass's own document


# ---------------------------------------------------------------- State
def _alloc(a: Alloc | None) -> AllocRow:
    """Return the row form of an allocation site: (function uid, line, column), or None."""
    return None if a is None else (a.func.uid, a.line, a.col)


class _Encoder:
    """Builder of the `values` table: gives each node and token one row and one index.

    A value's parts are added before the value itself, so a decoder can rebuild the table
    in one forward scan.
    """

    def __init__(self) -> None:
        """Start with an empty table."""
        self.values: list[Row] = []
        self.ids: dict[Node | Token, int] = {}

    def _add(self, key: Node | Token, row: Row) -> int:
        """Append `row` for `key` and return its index."""
        self.ids[key] = len(self.values)
        self.values.append(row)
        return self.ids[key]

    def node(self, n: Node) -> int:
        """Return the index of node `n`, adding its context's tokens and parent first."""
        if n not in self.ids:
            params = self.params(n.ctx.params)
            parent = self.opt_node(n.ctx.parent)
            self._add(n, ("node", n.func.uid, params, parent, n.ctx.merged))
        return self.ids[n]

    def opt_node(self, n: Node | None) -> int | None:
        """Return the index of node `n`, or None when `n` is None."""
        return None if n is None else self.node(n)

    def tok(self, t: Token) -> int:  # children first, so decoding is one forward scan
        """Return the index of token `t`, adding a row for it on first use.

        The row is tagged by the token's kind ("clo", "builtin", "class", ...; see `Row`), and
        the tokens or nodes it holds are added first.
        """
        if t in self.ids:
            return self.ids[t]
        row: Row
        if isinstance(t, Closure):
            row = ("clo", t.func.uid, self.opt_node(t.parent), t.depth)
        elif isinstance(t, Builtin):
            row = ("builtin", t.name, [self.toks(v) for v in t.bound],
                   [(k, self.toks(v)) for k, v in t.bound_kw])
        elif isinstance(t, ClassObj):
            row = ("class", t.cls.uid)
        elif isinstance(t, Instance):
            row = ("inst", t.cls.uid, _alloc(t.alloc))
        elif isinstance(t, Box):
            row = ("box", t.kind, _alloc(t.alloc))
        elif isinstance(t, BoundMethod):
            row = ("bound", self.tok(t.func), self.tok(t.receiver))
        elif isinstance(t, ClassMethod):
            row = ("classmethod", self.tok(t.func))
        elif isinstance(t, StaticMethod):
            row = ("staticmethod", self.tok(t.func))
        elif isinstance(t, Super):
            row = ("super", t.start.uid, self.tok(t.receiver))
        elif isinstance(t, Module):
            row = ("module", t.name)
        else:
            row = ("property", self.toks(t.fget), self.toks(t.fset), self.toks(t.fdel))
        return self._add(t, row)

    def toks(self, ts: Iterable[Token]) -> Ids:
        """Return the sorted indices of the tokens in `ts`."""
        return sorted(self.tok(t) for t in ts)

    def params(self, ps: ParamState) -> list[tuple[str, Ids]]:
        """Return a context's parameters as (name, token indices) pairs."""
        return [(name, self.toks(ts)) for name, ts in ps]

    def env(self, env: Mapping[str, Vals]) -> EnvDoc:
        """Return an environment as a dict of token indices, sorted by name."""
        return {k: self.toks(v) for k, v in sorted(env.items())}


def _site(s: Site) -> SiteRow:
    """Return the row form of a call site: (caller uid, line, column, text, kind)."""
    return (s.caller.uid, s.line, s.col, s.text, s.kind)


def state_to_dict(state: State) -> StateDoc:
    """Encode `state` as a JSON-ready `StateDoc`."""
    enc = _Encoder()
    order = [enc.node(n) for n in state.order]
    envs = [(enc.node(n), enc.env(e)) for n, e in state.envs.items()]
    ret = [(enc.node(n), enc.toks(v)) for n, v in state.ret.items()]
    entry = [(enc.node(n), enc.env(e)) for n, e in state.entry.items()]
    edges = sorted((enc.node(e.caller), _site(e.site), enc.node(e.callee)) for e in state.edges)
    return {"format": FORMAT, "version": VERSION, "program": state.program,
            "max_ctx": state.max_ctx, "flow_sensitive": state.flow, "values": enc.values, "order": order,
            "envs": envs, "ret": ret, "entry": entry, "edges": edges,
            "class_attrs": [(c.uid, enc.env(e)) for c, e in state.class_attrs.items()],
            "inst_attrs": [(enc.tok(i), enc.env(e)) for i, e in state.inst_attrs.items()],
            "heap": [(enc.tok(b), enc.toks(v)) for b, v in state.heap.items()],
            "extern": [(f.uid, enc.env(e)) for f, e in state.extern.items()],
            "writes": [(enc.node(n), [(f.uid, enc.env(e))
                                      for f, e in sorted(ws.items(), key=lambda p: p[0].uid)])
                       for n, ws in state.writes.items()],
            "bases": [(c.uid, [b.uid for b in bs]) for c, bs in state.bases.items()],
            "metaclasses": [(c.uid, sorted(m.uid for m in ms))
                            for c, ms in state.metaclasses.items()],
            "unknown_decos": sorted((enc.node(n), _site(s)) for n, s in state.unknown_decos)}


def state_from_dict(prog: Program, d: object) -> State:
    """Decode a `state_to_dict` document, such as one read with `json.load`, for `prog`.

    Function and class uids are resolved against `prog`. Raises `ValueError` if the header
    is not "callme-state" version 5, if the document was produced for another program or
    version of it (the fingerprints differ), or if a row is malformed or names an unknown
    id.
    """
    doc = cast(StateDoc, d)  # the layout is trusted once the header matches
    if not isinstance(d, dict) or doc.get("format") != FORMAT or doc.get("version") != VERSION:
        raise ValueError(f"not a {FORMAT} v{VERSION} document")
    if doc["program"] != prog.fingerprint:
        raise ValueError("state was produced for a different program (or another version of it)")
    funcs = {f.uid: f for f in prog.funcs}
    classes = {c.uid: c for c in prog.classes}

    def func(uid: str) -> FuncId:
        """Return the function with uid `uid`; raise `ValueError` if `prog` has none."""
        if uid not in funcs:
            raise ValueError(f"unknown function {uid}")
        return funcs[uid]

    def cls(uid: str) -> ClassId:
        """Return the class with uid `uid`; raise `ValueError` if `prog` has none."""
        if uid not in classes:
            raise ValueError(f"unknown class {uid}")
        return classes[uid]

    def alloc(a: AllocRow) -> Alloc | None:
        """Decode an allocation site row."""
        return None if a is None else Alloc(func(a[0]), a[1], a[2])

    vals: list[Node | Token] = []

    def get[T](i: int, kind: type[T]) -> T:
        """Return decoded value `i`, raising `ValueError` if it is not a `kind`."""
        v = vals[i]
        if not isinstance(v, kind):
            raise ValueError(f"value {i} is a {type(v).__name__}, expected a {kind.__name__}")
        return v

    def opt_node(i: int | None) -> Node | None:
        """Return decoded node `i`, or None when `i` is None."""
        return None if i is None else get(i, Node)

    def tok(i: int) -> Token:
        """Return decoded token `i`, raising `ValueError` if it is a node."""
        v = vals[i]
        if isinstance(v, Node):
            raise ValueError(f"value {i} is a context node, expected a token")
        return v

    def recv(i: int) -> ClassObj | Instance:
        """Return decoded value `i`, raising `ValueError` unless it is a class or an instance."""
        v = vals[i]
        if not isinstance(v, (ClassObj, Instance)):
            raise ValueError(f"value {i} is a {type(v).__name__}, expected a receiver")
        return v

    def toks(ids: Iterable[int]) -> Vals:
        """Return the set of decoded tokens with indices `ids`."""
        return frozenset(tok(i) for i in ids)

    for v in doc["values"]:
        if ROW_LEN.get(v[0]) != len(v):
            raise ValueError(f"unknown or malformed value row {v!r}")
        match v:
            case ("node", uid, params, parent, merged):
                ctx = Ctx(tuple((name, toks(ids)) for name, ids in params), opt_node(parent), merged)
                vals.append(Node(func(uid), ctx))
            case ("clo", uid, parent, depth):
                vals.append(Closure(func(uid), opt_node(parent), depth))
            case ("builtin", name, bound, bound_kw):
                vals.append(Builtin(name, tuple(toks(b) for b in bound),
                                    tuple((k, toks(b)) for k, b in bound_kw)))
            case ("class", uid):
                vals.append(ClassObj(cls(uid)))
            case ("inst", uid, a):
                vals.append(Instance(cls(uid), alloc(a)))
            case ("box", kind, a):
                box_alloc = alloc(a)
                if box_alloc is None:
                    raise ValueError("container token without an allocation site")
                vals.append(Box(kind, box_alloc))
            case ("bound", f, r):
                vals.append(BoundMethod(tok(f), recv(r)))
            case ("classmethod", f):
                vals.append(ClassMethod(tok(f)))
            case ("staticmethod", f):
                vals.append(StaticMethod(tok(f)))
            case ("super", uid, r):
                vals.append(Super(cls(uid), recv(r)))
            case ("property", fget, fset, fdel):
                vals.append(Property(toks(fget), toks(fset), toks(fdel)))
            case ("module", name):
                if name not in prog.modules:
                    raise ValueError(f"unknown module {name!r}")
                vals.append(Module(name))

    def env(e: EnvDoc) -> Env:
        """Decode an environment."""
        return {k: toks(ids) for k, ids in e.items()}

    state = State(doc["max_ctx"], doc["program"], doc["flow_sensitive"])
    for i in doc["order"]:
        state.register(get(i, Node))
    state.envs = {get(i, Node): env(e) for i, e in doc["envs"]}
    state.ret = {get(i, Node): toks(ids) for i, ids in doc["ret"]}
    state.entry = {get(i, Node): env(e) for i, e in doc["entry"]}
    state.edges = {Edge(get(c, Node), Site(func(uid), line, col, text, kind), get(t, Node))
                   for c, (uid, line, col, text, kind), t in doc["edges"]}
    state.class_attrs = {cls(c): env(e) for c, e in doc["class_attrs"]}
    state.inst_attrs = {get(i, Instance): env(e) for i, e in doc["inst_attrs"]}
    state.heap = {get(i, Box): toks(ids) for i, ids in doc["heap"]}
    state.extern = {func(f): env(e) for f, e in doc["extern"]}
    state.writes = {get(i, Node): {func(f): env(e) for f, e in ws} for i, ws in doc["writes"]}
    state.bases = {cls(c): tuple(cls(b) for b in bs) for c, bs in doc["bases"]}
    state.metaclasses = {cls(c): {cls(m) for m in ms} for c, ms in doc["metaclasses"]}
    state.unknown_decos = {(get(n, Node), Site(func(uid), line, col, text, kind))
                           for n, (uid, line, col, text, kind) in doc["unknown_decos"]}
    state.changed = False
    return state


def save_state(state: State, path: Path) -> None:
    """Write `state` to the file at `path` as a `StateDoc` JSON document."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state_to_dict(state), f)


def load_state(prog: Program, path: Path) -> State:
    """Read a state saved by `save_state` from `path`, for the program `prog`.

    The state keeps its own `max_ctx` and flow mode. Raises `ValueError` as
    `state_from_dict` does, e.g. when the source changed since the state was saved.
    """
    with open(path, encoding="utf-8") as f:
        return state_from_dict(prog, json.load(f))


# ---------------------------------------------------------------- Facts
Encode = Callable[[PassState], object]
Decode = Callable[[Program, object], PassState]
Codec = tuple[Encode, Decode]


def _encode_callgraph(state: PassState) -> StateDoc:
    """Encode the call graph pass state; raise `TypeError` if it is not a `State`."""
    if not isinstance(state, State):
        raise TypeError(f"callgraph state must be a State, not a {type(state).__name__}")
    return state_to_dict(state)


CODECS: dict[str, Codec] = {"callgraph": (_encode_callgraph, state_from_dict)}  # pass name -> codec


def facts_to_dict(facts: Facts, codecs: Mapping[str, Codec] = CODECS) -> FactsDoc:
    """Encode a `Facts` store as a JSON-ready `FactsDoc`, each pass through its codec.

    codecs  (encode, decode) pair per pass name; raises `ValueError` if a pass has none
    """
    missing = set(facts.states) - set(codecs)
    if missing:
        raise ValueError(f"no codec for passes {sorted(missing)}")
    return {"format": "callme-facts", "version": VERSION, "program": facts.program,
            "states": {k: codecs[k][0](v) for k, v in facts.states.items()}}


def facts_from_dict(prog: Program, d: object, codecs: Mapping[str, Codec] = CODECS) -> Facts:
    """Decode a `facts_to_dict` document for `prog`, each pass through its codec.

    Raises `ValueError` if it is not a "callme-facts" document for this program.
    """
    doc = cast(FactsDoc, d)  # the layout is trusted once the header matches
    if (not isinstance(d, dict) or doc.get("format") != "callme-facts"
            or doc.get("program") != prog.fingerprint):
        raise ValueError("not a callme-facts document for this program")
    return Facts(doc["program"], {k: codecs[k][1](prog, v) for k, v in doc["states"].items()})
