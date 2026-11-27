"""Layer 0 of the interpreter: its fields and `__init__`, the abstract `eval` and `apply`, and
`bind_attr` and `LoopFrame`, which several layers use."""
from __future__ import annotations

import ast
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import final

from ...frontend import FuncInfo, Program
from ...model import (
    EMPTY,
    BoundMethod,
    Builtin,
    ClassId,
    ClassMethod,
    ClassObj,
    Closure,
    FuncId,
    Instance,
    Node,
    Site,
    StaticMethod,
    Token,
    Vals,
)
from ..state import Env, State


Calls = list[tuple[Site, Node]]
End = tuple[Env, bool]  # flow mode: env where a path ends, and whether a live path gets there


@final
class LoopFrame:
    """Environments that leave one loop early: one per `break` and one per `continue`."""

    def __init__(self) -> None:
        """Start a frame with no early exits yet."""
        self.breaks: list[Env] = []
        self.continues: list[Env] = []


def bind_attr(tok: Token, owner_cls: ClassId, receiver: ClassObj | Instance) -> Token:
    """Return `tok` bound as Python binds an attribute read through `receiver`.

    A classmethod binds the owner class `owner_cls`, a staticmethod gives its function, and a
    function read through an instance becomes a bound method. `partialmethod` and
    `singledispatchmethod` tokens bind `receiver` as their first argument. Other tokens are
    unchanged.
    """
    if isinstance(tok, ClassMethod):
        return BoundMethod(tok.func, ClassObj(owner_cls))
    if isinstance(tok, StaticMethod):
        return tok.func
    if isinstance(receiver, Instance):
        if isinstance(tok, Closure) or (isinstance(tok, Builtin) and tok.name == "<dataclass_init>"):
            return BoundMethod(tok, receiver)
        if isinstance(tok, Builtin) and tok.name == "<partialmethod>":
            return Builtin("<partial>", (tok.bound[0], frozenset([receiver]), *tok.bound[1:]), tok.bound_kw)
        if isinstance(tok, Builtin) and tok.name == "<partialmethod*>":
            return Builtin("<partial*>", (tok.bound[0], frozenset([receiver]) | tok.bound[1]), tok.bound_kw)
        if isinstance(tok, Builtin) and tok.name == "<singledispatchmethod>":
            return BoundMethod(Builtin("<singledispatch>", tok.bound), receiver)
    return tok


class BaseLayer(ABC):
    """The interpreter's fields, and the two methods that lower layers call before they are defined.

    The interpreter is a chain of layers, one class per module, each subclassing the one below:
    names, modules, calls, attributes, expressions, operators, statements, definitions and flow.
    A layer may call any method of the layers below it. Evaluating an expression and calling a
    value are needed everywhere, so `eval` (`ExprLayer`) and `apply` (`CallLayer`) are declared
    here as abstract methods; the layers below `ExprLayer` are therefore abstract too. `Interp`
    is the top of the chain.
    """

    prog: Program
    state: State
    node: Node | None
    readonly: bool
    info: FuncInfo
    env: Env  # flow mode: the locals at the current program point
    ret: Vals
    calls: Calls
    class_stack: list[tuple[ClassId, Env]]
    flow: bool
    summary: Env  # flow mode: every value each local takes anywhere (what State.envs stores)
    live: bool  # flow mode: False after return / raise / break / continue (until a live path joins)
    loops: list[LoopFrame]  # enclosing loops, innermost last
    tries: list[Env]  # enclosing try / with bodies: union of every state seen inside (may raise)
    empty_decos: set[Site]  # decorator sites that evaluated to EMPTY in this run
    _weak_depth: int  # > 0 inside conditional expressions, stubs, or implicit calls: no strong kills

    def __init__(self, prog: Program, state: State, node: Node | None, *,
                 readonly: bool = False, func: FuncId | None = None) -> None:
        """Prepare to interpret `node`, or function `func` with no context.

        readonly  evaluate without writing to `state`; used for default values, which run in
                  the defining scope and are read by every context
        func      the function to use when `node` is None; locals then read the union over all
                  of its contexts
        """
        self.prog, self.state, self.node, self.readonly = prog, state, node, readonly
        fid = node.func if node is not None else func
        assert fid is not None, "Interp needs a node or a function"
        self.info = prog.funcs[fid]
        self.env = {}
        self.ret = EMPTY
        self.calls = []
        self.class_stack = []
        self.flow = state.flow
        self.summary = {}
        self.live = True
        self.loops = []
        self.tries = []
        self.empty_decos = set()
        self._weak_depth = 0

    @abstractmethod
    def eval(self, e: ast.expr | None) -> Vals:
        """Evaluate expression `e` to the values it may have (defined by `ExprLayer.eval`)."""

    @abstractmethod
    def apply(self, callee: Vals, pos: Sequence[Vals], star: Vals | None,
              kws: Mapping[str, Vals], kwstar: Vals | None, site: Site) -> Vals:
        """Call every callable in `callee` at `site`; return the results (`CallLayer.apply`)."""
