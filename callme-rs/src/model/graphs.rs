//! Graphs: call edges between nodes, the CFG of one function, and ICFG edges.

use std::sync::OnceLock;

use indexmap::IndexMap;
use rustc_hash::FxBuildHasher;

use super::ids::{FuncId, Site};
use super::values::NodeId;

/// A call edge: node `caller` may call node `callee` at `site`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Edge {
    /// the calling node
    pub caller: NodeId,
    /// where the call is
    pub site: Site,
    /// the called node
    pub callee: NodeId,
}

/// The kind of a CFG program point.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum PointKind {
    /// the function's entry
    Entry,
    /// the function's exit
    Exit,
    /// a statement's own effect, after the calls inside it
    Stmt,
    /// a test: if / while / assert / case / and-or / comprehension filter
    Branch,
    /// loop head: while join point, for / comprehension "next item"
    Loop,
    /// start of an except clause
    Handler,
    /// the call is made here ...
    Call,
    /// ... and its result arrives here (the return point)
    AfterCall,
}

impl PointKind {
    /// Return the kind's name as it appears in the output.
    pub fn as_str(self) -> &'static str {
        match self {
            PointKind::Entry => "entry",
            PointKind::Exit => "exit",
            PointKind::Stmt => "stmt",
            PointKind::Branch => "branch",
            PointKind::Loop => "loop",
            PointKind::Handler => "handler",
            PointKind::Call => "call",
            PointKind::AfterCall => "after_call",
        }
    }
}

/// The kind of a CFG or ICFG edge.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum EdgeKind {
    /// normal control flow
    Flow,
    /// the true side of a branch
    True,
    /// the false side of a branch
    False,
    /// an exception
    Exc,
    /// from a call point to its after-call point
    CallToReturn,
    /// ICFG only: from a call point to the callee's entry
    Call,
    /// ICFG only: from the callee's exit to the caller's after-call point
    Return,
}

impl EdgeKind {
    /// Return the kind's name as it appears in the output.
    pub fn as_str(self) -> &'static str {
        match self {
            EdgeKind::Flow => "flow",
            EdgeKind::True => "true",
            EdgeKind::False => "false",
            EdgeKind::Exc => "exc",
            EdgeKind::CallToReturn => "call_to_return",
            EdgeKind::Call => "call",
            EdgeKind::Return => "return",
        }
    }
}

/// One program point of a function body.
///
/// site  the call made at a CALL point or returning at its AFTER_CALL point, else None
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CFGNode {
    /// index of the point in `CFG.nodes`
    pub idx: u32,
    /// the point's kind
    pub kind: PointKind,
    /// source line, 0 for entry and exit
    pub line: u32,
    /// source column
    pub col: u32,
    /// a short source text
    pub text: String,
    /// the call site of a CALL or AFTER_CALL point
    pub site: Option<Site>,
}

/// An edge between two points of one CFG.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct CFGEdge {
    /// source point
    pub src: u32,
    /// target point
    pub dst: u32,
    /// Flow, True, False, Exc or CallToReturn
    pub kind: EdgeKind,
}

/// The outgoing and the incoming edges of every point of a CFG.
type Adjacency = (Vec<Vec<CFGEdge>>, Vec<Vec<CFGEdge>>);

/// The control-flow graph of one function's body.
///
/// It does not depend on contexts: every node of the function shares it. `calls` maps each
/// syntactic call site to its (CALL point, AFTER_CALL point) pair, in the order the points
/// were made.
#[derive(Debug)]
pub struct CFG {
    /// the function
    pub func: FuncId,
    /// the program points, indexed by `CFGNode.idx`
    pub nodes: Vec<CFGNode>,
    /// the edges, without duplicates, in the order they were added
    pub edges: Vec<CFGEdge>,
    /// call site -> (CALL point, AFTER_CALL point)
    pub calls: IndexMap<Site, (u32, u32), FxBuildHasher>,
    /// the entry point (0)
    pub entry: u32,
    /// the exit point (1)
    pub exit: u32,
    /// outgoing and incoming edges per point, built on first use
    adjacency: OnceLock<Adjacency>,
}

impl CFG {
    /// Create a CFG from its parts.
    pub fn new(
        func: FuncId,
        nodes: Vec<CFGNode>,
        edges: Vec<CFGEdge>,
        calls: IndexMap<Site, (u32, u32), FxBuildHasher>,
    ) -> CFG {
        CFG { func, nodes, edges, calls, entry: 0, exit: 1, adjacency: OnceLock::new() }
    }

    /// Return the outgoing and incoming edges of every point, built once and then cached.
    fn adj(&self) -> &Adjacency {
        self.adjacency.get_or_init(|| {
            let mut out = vec![Vec::new(); self.nodes.len()];
            let mut inc = vec![Vec::new(); self.nodes.len()];
            for e in &self.edges {
                out[e.src as usize].push(*e);
                inc[e.dst as usize].push(*e);
            }
            (out, inc)
        })
    }

    /// Return the edges leaving point `i`.
    pub fn succ(&self, i: u32) -> &[CFGEdge] {
        &self.adj().0[i as usize]
    }

    /// Return the edges entering point `i`.
    pub fn pred(&self, i: u32) -> &[CFGEdge] {
        &self.adj().1[i as usize]
    }
}

/// An ICFG edge from point `src_pt` of node `src` to point `dst_pt` of node `dst`.
///
/// ```text
/// kind  an intraprocedural kind when `src` is `dst`; `Call` from a CALL point to the callee's
///       entry; or `Return` from the callee's exit to the AFTER_CALL point of the same caller
///       node, so a return goes back only to the context that made the call
/// site  the call site of a `Call` or `Return` edge, else None
/// ```
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct IEdge {
    /// source node
    pub src: NodeId,
    /// source point
    pub src_pt: u32,
    /// target node
    pub dst: NodeId,
    /// target point
    pub dst_pt: u32,
    /// edge kind
    pub kind: EdgeKind,
    /// call site of a call or return edge
    pub site: Option<Site>,
}
