//! JSON and Graphviz DOT output for a `CallGraph`.

use std::collections::{BTreeMap, BTreeSet};

use indexmap::IndexMap;
use rustc_hash::FxHashMap;

use crate::model::{EdgeKind, NodeId, PointKind, Site};
use crate::obj;

use super::callgraph::CallGraph;
use super::json::Json;

/// Return the JSON form of a call site: line, column, text and kind.
fn site_json(s: &Site) -> Vec<(String, Json)> {
    vec![
        ("line".into(), s.line.into()),
        ("col".into(), s.col.into()),
        ("text".into(), s.text.as_str().into()),
        ("kind".into(), s.kind.as_str().into()),
    ]
}

/// Return a node's context for JSON: token labels per parameter, or `{"*": []}` if widened.
fn ctx_json(cg: &CallGraph, node: NodeId) -> Json {
    let c = cg.state.values.node_ctx(node);
    if c.merged {
        return Json::Obj(vec![("*".into(), Json::List(Vec::new()))]);
    }
    let mut out: IndexMap<String, Json> = IndexMap::new();
    for &(n, toks) in c.params.iter() {
        let labels = cg.state.values.sorted_labels(&cg.prog, toks, cg.style);
        out.insert(n.to_string(), Json::List(labels.into_iter().map(Json::Str).collect()));
    }
    Json::Obj(out.into_iter().collect())
}

/// Return the call graph as a JSON value, laid out as documented in `docs/output.md`.
///
/// The keys are `file` (the first entry), `modules` (every loaded module and its file),
/// `functions` (every function, reachable or not), `sites` (each with its possible targets),
/// `edges` (function level, without duplicates), `contexts` and `context_edges`.
///
/// icfg  also add the `icfg` key from `icfg_dict`
pub fn to_dict(cg: &CallGraph, icfg: bool) -> Json {
    let prog = &cg.prog;
    let reach = cg.reachable();
    let modules =
        prog.modules_by_name().map(|(name, m)| obj! {"name" => name.as_str(), "file" => m.file.clone()}).collect();
    let functions = prog
        .funcs
        .iter()
        .map(|i| {
            obj! {
                "id" => cg.name(i.id),
                "kind" => i.kind.as_str(),
                "module" => i.sym.module.as_str(),
                "file" => i.sym.file.as_str(),
                "line" => i.sym.lineno,
                "parent" => i.parent.map(|p| cg.name(p).to_owned()),
                "name" => i.sym.qualname.as_str(),
                "col" => i.sym.col,
                "reachable" => reach.contains(&i.id),
            }
        })
        .collect();
    let sites = cg
        .sites
        .iter()
        .map(|s| {
            let mut items = vec![("caller".to_owned(), Json::from(cg.name(s.caller)))];
            items.extend(site_json(s));
            items.push(("targets".into(), Json::List(cg.targets(s).into_iter().map(Json::Str).collect())));
            Json::Obj(items)
        })
        .collect();
    let edges = cg.function_edges().into_iter().map(|(c, d)| obj! {"caller" => c, "callee" => d}).collect();
    let contexts = cg
        .nodes
        .iter()
        .map(|&n| obj! {"id" => cg.label(n), "function" => cg.name(cg.state.values.node_func(n)), "context" => ctx_json(cg, n)})
        .collect();
    let context_edges = cg
        .edges
        .iter()
        .map(|e| obj! {"caller" => cg.label(e.caller), "callee" => cg.label(e.callee), "site" => Json::Obj(site_json(&e.site))})
        .collect();
    let mut out = vec![
        ("file".to_owned(), Json::from(prog.file.as_str())),
        ("modules".into(), Json::List(modules)),
        ("functions".into(), Json::List(functions)),
        ("sites".into(), Json::List(sites)),
        ("edges".into(), Json::List(edges)),
        ("contexts".into(), Json::List(contexts)),
        ("context_edges".into(), Json::List(context_edges)),
    ];
    if icfg {
        out.push(("icfg".into(), icfg_dict(cg)));
    }
    Json::Obj(out)
}

/// Return the JSON form of an ICFG program point: the node's label and the point index.
fn point(cg: &CallGraph, node: NodeId, pt: u32) -> Json {
    obj! {"context" => cg.label(node), "point" => pt}
}

/// Return the ICFG as a JSON value with keys `cfgs` and `edges`.
///
/// `cfgs` holds the CFG of each reachable function once, since its contexts share it. `edges`
/// holds the call and return edges between context nodes: a call goes from a CALL point to the
/// callee's entry, a return from the callee's exit to the AFTER_CALL point.
pub fn icfg_dict(cg: &CallGraph) -> Json {
    let g = cg.icfg();
    let reach = cg.reachable();
    let mut cfgs: IndexMap<String, Json> = IndexMap::new();
    for (fid, c) in cg.prog.funcs.iter().map(|f| (f.id, cg.prog.cfg(f.id))) {
        if !reach.contains(&fid) {
            continue;
        }
        let nodes = c
            .nodes
            .iter()
            .map(|n| obj! {"id" => n.idx, "kind" => n.kind.as_str(), "line" => n.line, "col" => n.col, "text" => n.text.as_str()})
            .collect();
        let edges = c.edges.iter().map(|e| obj! {"src" => e.src, "dst" => e.dst, "kind" => e.kind.as_str()}).collect();
        cfgs.insert(
            cg.name(fid).to_owned(),
            obj! {"entry" => c.entry, "exit" => c.exit, "nodes" => Json::List(nodes), "edges" => Json::List(edges)},
        );
    }
    let v = &cg.state.values;
    let mut edges = g.call_edges(&cg.prog, v);
    edges.extend(g.return_edges(&cg.prog, v));
    let edges = edges
        .iter()
        .map(|e| {
            obj! {
                "kind" => e.kind.as_str(),
                "src" => point(cg, e.src, e.src_pt),
                "dst" => point(cg, e.dst, e.dst_pt),
                "site" => e.site.map_or(Json::Null, |s| Json::Obj(site_json(&s))),
            }
        })
        .collect();
    obj! {"cfgs" => Json::Obj(cfgs.into_iter().collect()), "edges" => Json::List(edges)}
}

/// Return `to_dict(cg, icfg)` as JSON text, indented by `indent` spaces.
pub fn to_json(cg: &CallGraph, indent: usize, icfg: bool) -> String {
    to_dict(cg, icfg).pretty(indent)
}

/// Return `s` as a quoted DOT string, with backslashes and quotes escaped.
fn q(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
}

/// Return the call graph as Graphviz DOT text.
///
/// By default there is one box per function: unreachable functions are dashed and gray, and
/// each edge is labelled with its call lines, e.g. "L12,L15".
///
/// ```text
/// contexts  draw one box per (function, context) node and one edge per context edge
/// returns   add a dotted return edge from callee to caller for each call edge
/// ```
pub fn to_dot(cg: &CallGraph, contexts: bool, returns: bool) -> String {
    let mut lines: Vec<String> = vec![
        "digraph callgraph {".into(),
        "  rankdir=LR;".into(),
        "  node [shape=box, fontname=\"Helvetica\"];".into(),
    ];
    if contexts {
        for &n in &cg.nodes {
            lines.push(format!("  {};", q(cg.label(n))));
        }
        for e in &cg.edges {
            let lbl = format!("L{}", e.site.line);
            lines.push(format!("  {} -> {} [label={}];", q(cg.label(e.caller)), q(cg.label(e.callee)), q(&lbl)));
            if returns {
                lines.push(format!(
                    "  {} -> {} [style=dotted, constraint=false];",
                    q(cg.label(e.callee)),
                    q(cg.label(e.caller))
                ));
            }
        }
    } else {
        let reach = cg.reachable();
        for info in &cg.prog.funcs {
            let style = if reach.contains(&info.id) { "" } else { " [style=dashed, color=gray, fontcolor=gray]" };
            lines.push(format!("  {}{style};", q(cg.name(info.id))));
        }
        let mut sites: BTreeMap<(String, String), BTreeSet<u32>> = BTreeMap::new();
        for e in &cg.edges {
            let c = cg.name(cg.state.values.node_func(e.caller)).to_owned();
            let d = cg.name(cg.state.values.node_func(e.callee)).to_owned();
            sites.entry((c, d)).or_default().insert(e.site.line);
        }
        for ((c, d), ls) in sites {
            let lbl: Vec<String> = ls.iter().map(|x| format!("L{x}")).collect();
            lines.push(format!("  {} -> {} [label={}];", q(&c), q(&d), q(&lbl.join(","))));
            if returns {
                lines.push(format!("  {} -> {} [style=dotted, constraint=false];", q(&d), q(&c)));
            }
        }
    }
    lines.push("}".into());
    lines.join("\n") + "\n"
}

/// Return the DOT attributes of an ICFG edge kind.
fn edge_style(kind: EdgeKind) -> &'static str {
    match kind {
        EdgeKind::Flow => "",
        EdgeKind::True => " [label=\"T\"]",
        EdgeKind::False => " [label=\"F\"]",
        EdgeKind::Exc => " [color=red, style=dashed]",
        EdgeKind::CallToReturn => " [style=dotted]",
        EdgeKind::Call => " [color=blue, penwidth=1.5]",
        EdgeKind::Return => " [color=darkgreen, style=dashed]",
    }
}

/// Return the DOT shape of an ICFG point kind.
fn node_shape(kind: PointKind) -> &'static str {
    match kind {
        PointKind::Entry | PointKind::Exit => "oval",
        PointKind::Branch => "diamond",
        PointKind::Loop => "hexagon",
        PointKind::Call => "box, style=filled, fillcolor=lightblue",
        PointKind::AfterCall => "box, style=filled, fillcolor=honeydew",
        PointKind::Stmt | PointKind::Handler => "box",
    }
}

/// Return the ICFG as Graphviz DOT text.
///
/// Each (function, context) node is a cluster holding its function's CFG, and call and return
/// edges link the clusters. Point kinds get their own shapes and edge kinds their own styles;
/// edges at implicit sites have no CFG point and are not drawn.
pub fn to_icfg_dot(cg: &CallGraph) -> String {
    let g = cg.icfg();
    let ids: FxHashMap<NodeId, usize> = cg.nodes.iter().enumerate().map(|(i, &n)| (n, i)).collect();
    let pid = |node: NodeId, pt: u32| format!("n{}_{pt}", ids[&node]);
    let mut lines: Vec<String> = vec![
        "digraph icfg {".into(),
        "  node [shape=box, fontname=\"Helvetica\", fontsize=10];".into(),
        "  compound=true;".into(),
    ];
    let v = &cg.state.values;
    for &n in &cg.nodes {
        let c = cg.prog.cfg(v.node_func(n));
        lines.push(format!("  subgraph cluster_{} {{", ids[&n]));
        lines.push(format!("    label={};", q(cg.label(n))));
        for p in &c.nodes {
            let text = if matches!(p.kind, PointKind::Entry | PointKind::Exit) {
                p.kind.as_str().to_owned()
            } else {
                format!("L{}: {}", p.line, p.text)
            };
            lines.push(format!("    {} [label={}, shape={}];", pid(n, p.idx), q(&text), node_shape(p.kind)));
        }
        for e in &c.edges {
            lines.push(format!("    {} -> {}{};", pid(n, e.src), pid(n, e.dst), edge_style(e.kind)));
        }
        lines.push("  }".into());
    }
    let mut edges = g.call_edges(&cg.prog, v);
    edges.extend(g.return_edges(&cg.prog, v));
    for e in edges {
        lines.push(format!("  {} -> {}{};", pid(e.src, e.src_pt), pid(e.dst, e.dst_pt), edge_style(e.kind)));
    }
    lines.push("}".into());
    lines.join("\n") + "\n"
}
