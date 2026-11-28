//! Core data types shared by every stage of the analysis.
//!
//! Vocabulary:
//!
//! ```text
//!   FuncId       identity of one function (def, lambda or the module body)
//!   ClassId      identity of one class definition
//!   Site         a place where a call happens
//!   Token        an abstract object: Closure (a function created in some context), Builtin
//!                (a callable modelled by a stub), ClassObj, Instance (per allocation site),
//!                BoundMethod, ClassMethod, StaticMethod, Super, Property, Box (a list, dict
//!                or set per allocation site) and Module
//!   Vals         an abstract value: a set of tokens
//!   Ctx          a context: the abstract entry state of a function (parameter -> tokens)
//!                plus the node a closure was created in
//!   Node         (function, context): one analysed instance of a function
//!   Edge         call edge between two nodes at a call site
//!   CFG          per-function control-flow graph over program points (CFGNode)
//!   IEdge        ICFG edge between program points of two nodes
//! ```
//!
//! Tokens, values, contexts and nodes are interned in `Values` and handled as `u32`s.
//!
//! The modules: `ids` (FuncId, ClassId, Site), `values` (the tokens, `Vals`, contexts, nodes
//! and the `Values` table) and `graphs` (Edge, the CFG types, IEdge).

pub mod graphs;
pub mod ids;
pub mod values;

pub use graphs::{CFG, CFGEdge, CFGNode, Edge, EdgeKind, IEdge, PointKind};
pub use ids::{ClassId, FuncId, Site, SiteKind, Style, SymId, module_name};
pub use values::{Alloc, CtxData, CtxId, EMPTY, IdTable, NodeData, NodeId, TokId, Token, Vals, Values};
