//! Call graph: context-sensitive nodes and edges, plus the collapsed function-level view.

use std::collections::BTreeSet;
use std::sync::OnceLock;

use rustc_hash::{FxHashMap, FxHashSet};

use crate::analysis::State;
use crate::frontend::{Program, call_sites};
use crate::model::{Edge, FuncId, IdTable, NodeId, Site, Style, Values};

use super::icfg::{ICFG, build_icfg};

/// Return the printable label of a node, with ids rendered in `style`.
///
/// The label is `f` for a context without function-valued parameters, `f[p={a,b}, q={c}]`
/// listing each parameter's sorted token labels, or `f[*]` for the widened context.
pub fn node_label(values: &Values, ids: &dyn IdTable, node: NodeId, style: Style) -> String {
    let d = values.node_data(node);
    let c = values.ctx_data(d.ctx);
    let f = ids.func_sym(d.func).render(style);
    if c.merged {
        return format!("{f}[*]");
    }
    if c.params.is_empty() {
        return f.to_owned();
    }
    let parts: Vec<String> = c
        .params
        .iter()
        .map(|&(n, toks)| format!("{n}={{{}}}", values.sorted_labels(ids, toks, style).join(",")))
        .collect();
    format!("{f}[{}]", parts.join(", "))
}

/// The result of an analysis: context nodes, call edges and call sites, with labels.
///
/// ```text
/// nodes   analysed (function, context) nodes, in discovery order
/// edges   context call edges, sorted by caller, position and callee
/// sites   every syntactic call site of the reachable functions, followed by the implicit
///         sites that got an edge
/// labels  unique printable label per node; labels that would collide get "#1", "#2", ...
/// state   the solver state the graph was built from, to resume or chain later passes
/// style   id style for names (see `SymId::render`)
/// ```
pub struct CallGraph {
    /// the program
    pub prog: Program,
    /// analysed nodes, in discovery order
    pub nodes: Vec<NodeId>,
    /// context call edges, sorted
    pub edges: Vec<Edge>,
    /// every syntactic call site of reachable functions, then the implicit ones with edges
    pub sites: Vec<Site>,
    /// unique printable label per node
    pub labels: FxHashMap<NodeId, String>,
    /// the solver state this graph was built from
    pub state: State,
    /// id style for names
    pub style: Style,
    /// the ICFG, built on first use
    icfg: OnceLock<ICFG>,
    /// the callee ids of each site, built on first use
    targets: OnceLock<FxHashMap<Site, BTreeSet<String>>>,
}

impl CallGraph {
    /// Return the id of function `f` in the graph's id style.
    pub fn name(&self, f: FuncId) -> &str {
        self.prog.func_sym(f).render(self.style)
    }

    /// Return the label of node `n`.
    pub fn label(&self, n: NodeId) -> &str {
        &self.labels[&n]
    }

    /// Return the functions that have at least one analysed node.
    pub fn reachable(&self) -> FxHashSet<FuncId> {
        self.nodes.iter().map(|&n| self.state.values.node_func(n)).collect()
    }

    /// Return the call edges with contexts merged, as (caller id, callee id) pairs, sorted.
    pub fn function_edges(&self) -> BTreeSet<(String, String)> {
        self.edges
            .iter()
            .map(|e| {
                let c = self.state.values.node_func(e.caller);
                let d = self.state.values.node_func(e.callee);
                (self.name(c).to_owned(), self.name(d).to_owned())
            })
            .collect()
    }

    /// Return the interprocedural CFG of the analysed nodes, built once and then cached.
    ///
    /// Panics if a syntactic call edge has no CFG point, which would mean the CFG builder and
    /// the interpreter disagree.
    pub fn icfg(&self) -> &ICFG {
        self.icfg.get_or_init(|| {
            build_icfg(&self.prog, &self.state.values, &self.nodes, &self.edges)
                .expect("CFG and interpreter agree on call sites")
        })
    }

    /// Return the ids of the functions that the call at `site` may call, sorted.
    pub fn targets(&self, site: &Site) -> BTreeSet<String> {
        let by_site = self.targets.get_or_init(|| {
            let mut m: FxHashMap<Site, BTreeSet<String>> = FxHashMap::default();
            for e in &self.edges {
                let callee = self.name(self.state.values.node_func(e.callee)).to_owned();
                m.entry(e.site).or_default().insert(callee);
            }
            m
        });
        by_site.get(site).cloned().unwrap_or_default()
    }
}

/// The sort key of an edge: caller uid, site line, column and kind, callee label, then
/// tie-breakers (caller label, site text, node handles) that make the order deterministic.
type EdgeKey = (String, u32, u32, &'static str, String, String, String, u32, u32);

/// Build the `CallGraph` of a solved `state`, naming functions in id style `style`.
///
/// Edges are sorted by caller, site position and callee. Syntactic sites come from the
/// reachable functions' CFGs, so a call with no known target is listed too; implicit sites are
/// listed only when they got an edge.
pub fn build_callgraph(prog: Program, state: State, style: Style) -> CallGraph {
    let v = &state.values;
    let nodes = state.order.clone();
    let mut keyed: Vec<(EdgeKey, Edge)> = state
        .edges
        .iter()
        .map(|e| {
            let key = (
                prog.func_sym(v.node_func(e.caller)).uid().to_owned(),
                e.site.line,
                e.site.col,
                e.site.kind.as_str(),
                node_label(v, &prog, e.callee, Style::Full),
                node_label(v, &prog, e.caller, Style::Full),
                e.site.text.to_string(),
                e.caller.0,
                e.callee.0,
            );
            (key, *e)
        })
        .collect();
    keyed.sort_by(|a, b| a.0.cmp(&b.0));
    let edges: Vec<Edge> = keyed.into_iter().map(|(_, e)| e).collect();
    let reach: FxHashSet<FuncId> = nodes.iter().map(|&n| v.node_func(n)).collect();
    let syntactic: Vec<Site> =
        prog.funcs.iter().filter(|f| reach.contains(&f.id)).flat_map(|f| call_sites(&prog, f.id)).collect();
    let syn_set: FxHashSet<Site> = syntactic.iter().copied().collect();
    let mut implicit: Vec<Site> =
        edges.iter().map(|e| e.site).filter(|s| !syn_set.contains(s)).collect::<FxHashSet<_>>().into_iter().collect();
    implicit.sort_by(|a, b| {
        let ka = (prog.func_sym(a.caller).uid(), a.line, a.col, a.kind.as_str(), a.text.as_str());
        let kb = (prog.func_sym(b.caller).uid(), b.line, b.col, b.kind.as_str(), b.text.as_str());
        ka.cmp(&kb)
    });
    let mut sites = syntactic;
    sites.extend(implicit);
    let mut by_label: indexmap::IndexMap<String, Vec<NodeId>> = indexmap::IndexMap::new();
    for &n in &nodes {
        by_label.entry(node_label(v, &prog, n, style)).or_default().push(n);
    }
    let mut labels = FxHashMap::default();
    for (label, group) in by_label {
        let unique = group.len() == 1;
        for (i, n) in group.into_iter().enumerate() {
            labels.insert(n, if unique { label.clone() } else { format!("{label}#{}", i + 1) });
        }
    }
    CallGraph { prog, nodes, edges, sites, labels, state, style, icfg: OnceLock::new(), targets: OnceLock::new() }
}
