"""Core data types shared by every stage of the analysis.

Vocabulary:
  FuncId       identity of one function (def, lambda or the module body)
  ClassId      identity of one class definition
  Closure      a *token*: the abstract value "this function, created in that context"
  Builtin      a *token* for a builtin / stdlib callable modelled by a stub (see `callme.stubs`)
  ClassObj     a *token* representing a class object
  Instance     a *token* representing an abstract instance of a class (per allocation site)
  Box          a *token* for a mutable container (list / dict / set), per allocation site
  Module       a *token* for a module object of the program, by dotted name
  Alloc        an allocation site: where an instance or container is created
  BoundMethod  a *token*: a callable with a receiver pre-bound as first argument
  ClassMethod  a *token*: `classmethod(func)` descriptor binding the owner class
  StaticMethod a *token*: `staticmethod(func)` descriptor returning `func` unbound
  Super        a *token*: `super(start, receiver)` proxy along `receiver`'s MRO
  Property     a *token*: `property(fget, fset, fdel)` descriptor
  Ctx          a *context*: the abstract entry state of a
               function (param -> set of tokens) plus the captured defining node
  Node         (function, context): one analysed instance of a function
  Edge         call edge between two nodes at a syntactic call site
  CFG          per-function control-flow graph over program points (CFGNode)
  IEdge        ICFG edge between program points of two (function, context) nodes

Tokens, `Ctx` and `Node` are frozen dataclasses that compute their hash once, in
`__post_init__`, and keep it in `_h`, because they are hashed in every set operation.
A token's `depth` says how deeply other tokens nest inside it; the analysis drops or
widens tokens that nest too deeply, which keeps every value finite.

The modules: `ids` (FuncId, ClassId, Site), `values` (the tokens, `Vals`, Ctx and Node) and
`graphs` (Edge, the CFG types and kind constants, IEdge). Import the names from this package.
"""
from __future__ import annotations

from .graphs import (
    AFTER_CALL,
    BRANCH,
    CALL,
    CALL_EDGE,
    CALL_TO_RETURN,
    CFG,
    ENTRY,
    EXC,
    EXIT,
    FALSE,
    FLOW,
    HANDLER,
    LOOP,
    RETURN_EDGE,
    STMT,
    TRUE,
    CFGEdge,
    CFGNode,
    Edge,
    IEdge,
)
from .ids import ClassId, FuncId, Site, module_name
from .values import (
    EMPTY,
    Alloc,
    BoundMethod,
    Box,
    Builtin,
    ClassMethod,
    ClassObj,
    Closure,
    Ctx,
    Instance,
    Module,
    Node,
    ParamState,
    Property,
    StaticMethod,
    Super,
    Token,
    Vals,
    token_label,
)

__all__ = [
    "AFTER_CALL", "BRANCH", "CALL", "CALL_EDGE", "CALL_TO_RETURN", "CFG", "ENTRY", "EXC", "EXIT",
    "FALSE", "FLOW", "HANDLER", "LOOP", "RETURN_EDGE", "STMT", "TRUE", "CFGEdge", "CFGNode",
    "IEdge",
    "ClassId", "FuncId", "module_name",
    "EMPTY", "Alloc", "BoundMethod", "Box", "Builtin", "ClassMethod", "ClassObj", "Closure",
    "Instance", "Module", "ParamState", "Property", "StaticMethod", "Super", "Token", "Vals",
    "token_label",
    "Ctx", "Edge", "Node", "Site",
]
