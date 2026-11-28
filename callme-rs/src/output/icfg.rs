//! The interprocedural CFG: each node's copy of its function's CFG, plus call and return edges.

use crate::error::{Error, Result};
use crate::frontend::Program;
use crate::model::{CFG, Edge, EdgeKind, IEdge, NodeId, SiteKind, Values};

/// The site kinds that are CFG call points.
pub const SYNTACTIC_KINDS: [SiteKind; 2] = [SiteKind::Call, SiteKind::Decorator];

/// Interprocedural CFG: each node's copy of its function's CFG, plus call and return edges.
///
/// A program point is a (node, CFG point index) pair. The edge lists are built on demand from
/// `calls`.
///
/// ```text
/// calls     context call edges at CFG call points, which get call and return edges
/// implicit  context call edges at implicit sites, which have no CFG point and get no edges
/// ```
#[derive(Debug)]
#[allow(missing_docs)]
pub struct ICFG {
    pub nodes: Vec<NodeId>,
    pub calls: Vec<Edge>,
    pub implicit: Vec<Edge>,
}

impl ICFG {
    /// Return the CFG of `node`'s function, which all its contexts share.
    pub fn cfg<'p>(&self, prog: &'p Program, values: &Values, node: NodeId) -> &'p CFG {
        prog.cfg(values.node_func(node))
    }

    /// Return every node's copy of its function's CFG edges.
    pub fn intra_edges(&self, prog: &Program, values: &Values) -> Vec<IEdge> {
        let mut out = Vec::new();
        for &n in &self.nodes {
            for e in &self.cfg(prog, values, n).edges {
                out.push(IEdge { src: n, src_pt: e.src, dst: n, dst_pt: e.dst, kind: e.kind, site: None });
            }
        }
        out
    }

    /// Return one edge per call: from the caller's CALL point to the callee's entry.
    pub fn call_edges(&self, prog: &Program, values: &Values) -> Vec<IEdge> {
        self.calls
            .iter()
            .map(|e| {
                let (call_pt, _) = self.cfg(prog, values, e.caller).calls[&e.site];
                let entry = self.cfg(prog, values, e.callee).entry;
                IEdge {
                    src: e.caller,
                    src_pt: call_pt,
                    dst: e.callee,
                    dst_pt: entry,
                    kind: EdgeKind::Call,
                    site: Some(e.site),
                }
            })
            .collect()
    }

    /// Return one edge per call: from the callee's exit to the caller's AFTER_CALL point.
    ///
    /// A return goes back only to the caller node that made the call, so only paths with
    /// matching calls and returns exist between contexts.
    pub fn return_edges(&self, prog: &Program, values: &Values) -> Vec<IEdge> {
        self.calls
            .iter()
            .map(|e| {
                let (_, ret_pt) = self.cfg(prog, values, e.caller).calls[&e.site];
                let exit = self.cfg(prog, values, e.callee).exit;
                IEdge {
                    src: e.callee,
                    src_pt: exit,
                    dst: e.caller,
                    dst_pt: ret_pt,
                    kind: EdgeKind::Return,
                    site: Some(e.site),
                }
            })
            .collect()
    }

    /// Return every edge: intraprocedural, then call, then return edges.
    pub fn edges(&self, prog: &Program, values: &Values) -> Vec<IEdge> {
        let mut out = self.intra_edges(prog, values);
        out.extend(self.call_edges(prog, values));
        out.extend(self.return_edges(prog, values));
        out
    }
}

/// Return the ICFG over `nodes` with the context call edges `calls`.
///
/// Edges at implicit sites (operator dunders, descriptors, `with`, iteration and class
/// creation) are kept apart in `ICFG.implicit`. Returns `Error::Value` if a syntactic call or
/// decorator site has no point in its CFG, which would mean the CFG builder and the
/// interpreter disagree.
pub fn build_icfg(prog: &Program, values: &Values, nodes: &[NodeId], calls: &[Edge]) -> Result<ICFG> {
    let mut wired = Vec::new();
    let mut implicit = Vec::new();
    for e in calls {
        if prog.cfg(values.node_func(e.caller)).calls.contains_key(&e.site) {
            wired.push(*e);
        } else if SYNTACTIC_KINDS.contains(&e.site.kind) {
            return Err(Error::Value(format!(
                "call site missing from the CFG: {} at {}:{}",
                e.site.text, e.site.line, e.site.col
            )));
        } else {
            implicit.push(*e);
        }
    }
    Ok(ICFG { nodes: nodes.to_vec(), calls: wired, implicit })
}
