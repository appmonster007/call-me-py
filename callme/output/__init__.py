"""Step 3: results. The call graph object, the ICFG, JSON / DOT export, and saved states.

The modules: `callgraph` (`CallGraph`, built from a solved `State`), `icfg` (the
interprocedural CFG), `export` (JSON and Graphviz DOT) and `persist` (a `State` or a whole
`Facts` store to JSON and back). Import the names from this package.
"""
from __future__ import annotations

from .callgraph import CallGraph, build_callgraph, node_label
from .export import icfg_dict, to_dict, to_dot, to_icfg_dot, to_json
from .icfg import ICFG, SYNTACTIC_KINDS, build_icfg, icfg_from_state
from .persist import (
    CODECS,
    FactsDoc,
    Row,
    StateDoc,
    facts_from_dict,
    facts_to_dict,
    load_state,
    save_state,
    state_from_dict,
    state_to_dict,
)

__all__ = [
    "CallGraph", "build_callgraph", "node_label",
    "icfg_dict", "to_dict", "to_dot", "to_icfg_dot", "to_json",
    "ICFG", "SYNTACTIC_KINDS", "build_icfg", "icfg_from_state",
    "CODECS", "FactsDoc", "Row", "StateDoc", "facts_from_dict", "facts_to_dict", "load_state",
    "save_state", "state_from_dict", "state_to_dict",
]
