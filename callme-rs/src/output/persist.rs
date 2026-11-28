//! Save a solver state (or a whole `Facts` store) to JSON and load it back.
//!
//! The document layout is the Python version's "callme-state" format, version 5, so a state
//! saved by either implementation can be resumed by the other. Loading needs the `Program` it
//! was produced for: function and class ids are stored as uid strings and resolved against the
//! program, and the program fingerprint must match, so a state cannot be resumed on edited
//! source.
//!
//! Nodes and tokens reference each other (a closure holds its defining node, a context holds
//! tokens, a bound method holds its function and receiver), so all of them live in one table
//! `values`, written in dependency order (children first); everything else refers to table
//! indices. A row is a list tagged by its kind:
//!
//!   ["node", func uid, [[param, ids]...], parent node | null, merged]
//!   ["clo", func uid, parent node | null, depth]
//!   ["builtin", name, [ids per bound arg], [[kw, ids]...]]
//! ```text
//!   ["class", class uid]        ["inst", class uid, alloc | null]   ["box", kind, alloc]
//!   ["bound", func, receiver]   ["classmethod", func]               ["staticmethod", func]
//!   ["super", class uid, receiver]   ["property", ids, ids, ids]    ["module", name]
//! ```
//!
//! An alloc is [func uid, line, col]; ids are sorted lists of table indices.

use std::collections::BTreeMap;

use indexmap::IndexMap;
use rustc_hash::FxHashMap;

use crate::analysis::{Facts, PassState, State};
use crate::error::{Error, Result};
use crate::frontend::Program;
use crate::model::{Alloc, ClassId, Edge, FuncId, IdTable, NodeId, Site, SiteKind, TokId, Token, Vals};
use crate::obj;
use crate::syntax::Name;

use super::json::{Json, parse};

/// The document format name.
pub const FORMAT: &str = "callme-state";
/// The document format version (v3: flow_sensitive; v4: module tokens; v5: writes, unknown_decos).
pub const VERSION: i64 = 5;

/// A node or token of the value table.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum Key {
    /// a node
    Node(NodeId),
    /// a token
    Tok(TokId),
}

/// Builder of the `values` table: gives each node and token one row and one index.
///
/// A value's parts are added before the value itself, so a decoder can rebuild the table in
/// one forward scan.
struct Encoder<'s> {
    /// the state being encoded
    state: &'s State,
    /// the program, for uids
    prog: &'s dyn IdTable,
    /// the rows so far
    rows: Vec<Json>,
    /// index of each node or token with a row
    ids: FxHashMap<Key, usize>,
}

impl Encoder<'_> {
    /// Append `row` for `key` and return its index.
    fn add(&mut self, key: Key, row: Json) -> usize {
        let i = self.rows.len();
        self.ids.insert(key, i);
        self.rows.push(row);
        i
    }

    /// Return the index of node `n`, adding its context's tokens and parent first.
    fn node(&mut self, n: NodeId) -> usize {
        if let Some(&i) = self.ids.get(&Key::Node(n)) {
            return i;
        }
        let d = self.state.values.node_data(n);
        let c = self.state.values.ctx_data(d.ctx).clone();
        let params = self.params(&c.params);
        let parent = c.parent.map(|p| self.node(p));
        let row = Json::List(vec![
            "node".into(),
            self.prog.func_sym(d.func).uid().into(),
            params,
            parent.into(),
            c.merged.into(),
        ]);
        self.add(Key::Node(n), row)
    }

    /// Return the index of token `t`, adding a row for it (and the values it holds) first.
    fn tok(&mut self, t: TokId) -> usize {
        if let Some(&i) = self.ids.get(&Key::Tok(t)) {
            return i;
        }
        let tok = self.state.values.get(t).clone();
        let row = match tok {
            Token::Closure { func, parent } => {
                let p = parent.map(|p| self.node(p));
                Json::List(vec![
                    "clo".into(),
                    self.prog.func_sym(func).uid().into(),
                    p.into(),
                    self.state.values.depth(t).into(),
                ])
            }
            Token::Builtin { name, bound, bound_kw } => {
                let b: Vec<Json> = bound.iter().map(|&v| self.toks(v)).collect();
                let k: Vec<Json> =
                    bound_kw.iter().map(|&(n, v)| Json::List(vec![n.as_str().into(), self.toks(v)])).collect();
                Json::List(vec!["builtin".into(), name.as_str().into(), Json::List(b), Json::List(k)])
            }
            Token::ClassObj(c) => Json::List(vec!["class".into(), self.prog.class_sym(c).uid().into()]),
            Token::Instance { cls, alloc } => {
                Json::List(vec!["inst".into(), self.prog.class_sym(cls).uid().into(), self.alloc(alloc)])
            }
            Token::Box { kind, alloc } => Json::List(vec!["box".into(), kind.as_str().into(), self.alloc(Some(alloc))]),
            Token::BoundMethod { func, receiver } => {
                let f = self.tok(func);
                let r = self.tok(receiver);
                Json::List(vec!["bound".into(), f.into(), r.into()])
            }
            Token::ClassMethod(f) => Json::List(vec!["classmethod".into(), self.tok(f).into()]),
            Token::StaticMethod(f) => Json::List(vec!["staticmethod".into(), self.tok(f).into()]),
            Token::Super { start, receiver } => {
                let r = self.tok(receiver);
                Json::List(vec!["super".into(), self.prog.class_sym(start).uid().into(), r.into()])
            }
            Token::Module(n) => Json::List(vec!["module".into(), n.as_str().into()]),
            Token::Property { fget, fset, fdel } => {
                let g = self.toks(fget);
                let s = self.toks(fset);
                let d = self.toks(fdel);
                Json::List(vec!["property".into(), g, s, d])
            }
        };
        self.add(Key::Tok(t), row)
    }

    /// Return the sorted indices of the tokens of `v`.
    fn toks(&mut self, v: Vals) -> Json {
        let toks = self.state.values.toks(v);
        let mut ids: Vec<usize> = toks.iter().map(|&t| self.tok(t)).collect();
        ids.sort_unstable();
        Json::List(ids.into_iter().map(Json::from).collect())
    }

    /// Return a context's parameters as [name, token indices] pairs.
    fn params(&mut self, ps: &[(Name, Vals)]) -> Json {
        Json::List(ps.iter().map(|&(n, v)| Json::List(vec![n.as_str().into(), self.toks(v)])).collect())
    }

    /// Return an environment as an object of token indices, sorted by name.
    fn env(&mut self, env: &crate::analysis::Env) -> Json {
        let mut items: Vec<(Name, Vals)> = env.iter().map(|(&k, &v)| (k, v)).collect();
        items.sort_by_key(|p| p.0);
        Json::Obj(items.into_iter().map(|(k, v)| (k.to_string(), self.toks(v))).collect())
    }

    /// Return the row form of an allocation site: [function uid, line, column], or null.
    fn alloc(&self, a: Option<Alloc>) -> Json {
        a.map_or(Json::Null, |a| Json::List(vec![self.prog.func_sym(a.func).uid().into(), a.line.into(), a.col.into()]))
    }

    /// Return the row form of a call site: [caller uid, line, column, text, kind].
    fn site(&self, s: &Site) -> Json {
        Json::List(vec![
            self.prog.func_sym(s.caller).uid().into(),
            s.line.into(),
            s.col.into(),
            s.text.as_str().into(),
            s.kind.as_str().into(),
        ])
    }
}

/// A site row as a sort key that orders like Python's tuple comparison.
type SiteKey = (String, i64, i64, String, String);

/// Return the sort key of a site row.
fn site_key(s: &Json) -> SiteKey {
    let it = s.items();
    let st = |i: usize| it.get(i).and_then(Json::as_str).unwrap_or_default().to_owned();
    let n = |i: usize| it.get(i).and_then(Json::as_int).unwrap_or_default();
    (st(0), n(1), n(2), st(3), st(4))
}

/// Encode `state` as a "callme-state" document.
///
/// prog  the program, which names the functions and classes by uid
pub fn state_to_json(state: &State, prog: &Program) -> Json {
    let mut enc = Encoder { state, prog, rows: Vec::new(), ids: FxHashMap::default() };
    let order: Vec<Json> = state.order.iter().map(|&n| enc.node(n).into()).collect();
    let envs: Vec<Json> = state.envs.iter().map(|(n, e)| Json::List(vec![enc.node(n).into(), enc.env(e)])).collect();
    let ret: Vec<Json> = state.ret.iter().map(|(n, &v)| Json::List(vec![enc.node(n).into(), enc.toks(v)])).collect();
    let entry: Vec<Json> = state.entry.iter().map(|(n, e)| Json::List(vec![enc.node(n).into(), enc.env(e)])).collect();
    let mut edges: Vec<(usize, SiteKey, usize, Json)> = state
        .edges
        .iter()
        .map(|e| {
            let c = enc.node(e.caller);
            let s = enc.site(&e.site);
            let t = enc.node(e.callee);
            (c, site_key(&s), t, s)
        })
        .collect();
    edges.sort_by(|a, b| (a.0, &a.1, a.2).cmp(&(b.0, &b.1, b.2)));
    let edges: Vec<Json> = edges.into_iter().map(|(c, _, t, s)| Json::List(vec![c.into(), s, t.into()])).collect();
    let class_attrs: Vec<Json> =
        state.class_attrs.iter().map(|(c, e)| Json::List(vec![prog.class_sym(c).uid().into(), enc.env(e)])).collect();
    let inst_attrs: Vec<Json> =
        state.inst_attrs.iter().map(|(i, e)| Json::List(vec![enc.tok(i).into(), enc.env(e)])).collect();
    let heap: Vec<Json> = state.heap.iter().map(|(b, &v)| Json::List(vec![enc.tok(b).into(), enc.toks(v)])).collect();
    let extern_: Vec<Json> =
        state.extern_.iter().map(|(f, e)| Json::List(vec![prog.func_sym(f).uid().into(), enc.env(e)])).collect();
    let writes: Vec<Json> = state
        .writes
        .iter()
        .map(|(n, ws)| {
            let mut owners: Vec<(&FuncId, &crate::analysis::Env)> = ws.iter().collect();
            owners.sort_by(|a, b| prog.func_sym(*a.0).uid().cmp(prog.func_sym(*b.0).uid()));
            let items: Vec<Json> =
                owners.into_iter().map(|(f, e)| Json::List(vec![prog.func_sym(*f).uid().into(), enc.env(e)])).collect();
            Json::List(vec![enc.node(n).into(), Json::List(items)])
        })
        .collect();
    let bases: Vec<Json> = state
        .bases
        .iter()
        .map(|(c, bs)| {
            Json::List(vec![
                prog.class_sym(c).uid().into(),
                Json::List(bs.iter().map(|&b| prog.class_sym(b).uid().into()).collect()),
            ])
        })
        .collect();
    let metaclasses: Vec<Json> = state
        .metaclasses
        .iter()
        .map(|(c, ms)| {
            let mut uids: Vec<&str> = ms.iter().map(|&m| prog.class_sym(m).uid()).collect();
            uids.sort_unstable();
            Json::List(vec![prog.class_sym(c).uid().into(), Json::List(uids.into_iter().map(Json::from).collect())])
        })
        .collect();
    let mut decos: Vec<(usize, SiteKey, Json)> = state
        .unknown_decos
        .iter()
        .map(|(n, s)| {
            let i = enc.node(*n);
            let row = enc.site(s);
            (i, site_key(&row), row)
        })
        .collect();
    decos.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
    let unknown_decos: Vec<Json> = decos.into_iter().map(|(i, _, s)| Json::List(vec![i.into(), s])).collect();
    obj! {
        "format" => FORMAT,
        "version" => Json::Int(VERSION),
        "program" => state.program.clone(),
        "max_ctx" => state.max_ctx,
        "flow_sensitive" => state.flow,
        "values" => Json::List(enc.rows),
        "order" => Json::List(order),
        "envs" => Json::List(envs),
        "ret" => Json::List(ret),
        "entry" => Json::List(entry),
        "edges" => Json::List(edges),
        "class_attrs" => Json::List(class_attrs),
        "inst_attrs" => Json::List(inst_attrs),
        "heap" => Json::List(heap),
        "extern" => Json::List(extern_),
        "writes" => Json::List(writes),
        "bases" => Json::List(bases),
        "metaclasses" => Json::List(metaclasses),
        "unknown_decos" => Json::List(unknown_decos),
    }
}

/// A decoded entry of the value table.
#[derive(Clone, Copy)]
enum Decoded {
    /// a node
    Node(NodeId),
    /// a token
    Tok(TokId),
}

/// Return a malformed-document error.
fn bad(msg: impl Into<String>) -> Error {
    Error::Value(msg.into())
}

/// The state of decoding one document.
struct Decoder<'p> {
    /// function id by uid
    funcs: FxHashMap<&'p str, FuncId>,
    /// class id by uid
    classes: FxHashMap<&'p str, ClassId>,
    /// the decoded value table
    vals: Vec<Decoded>,
}

impl Decoder<'_> {
    /// Return the function with uid `uid`.
    fn func(&self, uid: &Json) -> Result<FuncId> {
        let s = uid.as_str().ok_or_else(|| bad("a function uid must be a string"))?;
        self.funcs.get(s).copied().ok_or_else(|| bad(format!("unknown function {s}")))
    }

    /// Return the class with uid `uid`.
    fn cls(&self, uid: &Json) -> Result<ClassId> {
        let s = uid.as_str().ok_or_else(|| bad("a class uid must be a string"))?;
        self.classes.get(s).copied().ok_or_else(|| bad(format!("unknown class {s}")))
    }

    /// Return value index `i` of a document.
    fn index(&self, i: &Json) -> Result<usize> {
        let i = i.as_int().ok_or_else(|| bad("a value index must be an integer"))?;
        let i = usize::try_from(i).map_err(|_| bad("negative value index"))?;
        if i >= self.vals.len() {
            return Err(bad(format!("value {i} is not defined yet")));
        }
        Ok(i)
    }

    /// Return decoded node `i`.
    fn node(&self, i: &Json) -> Result<NodeId> {
        let k = self.index(i)?;
        match self.vals[k] {
            Decoded::Node(n) => Ok(n),
            Decoded::Tok(_) => Err(bad(format!("value {k} is a token, expected a Node"))),
        }
    }

    /// Return decoded node `i`, or None for null.
    fn opt_node(&self, i: &Json) -> Result<Option<NodeId>> {
        if *i == Json::Null { Ok(None) } else { self.node(i).map(Some) }
    }

    /// Return decoded token `i`.
    fn tok(&self, i: &Json) -> Result<TokId> {
        let k = self.index(i)?;
        match self.vals[k] {
            Decoded::Tok(t) => Ok(t),
            Decoded::Node(_) => Err(bad(format!("value {k} is a context node, expected a token"))),
        }
    }

    /// Return decoded token `i` if it has the kind `want` checks, else an error naming `what`.
    fn tok_of(&self, state: &State, i: &Json, what: &str, want: fn(&Token) -> bool) -> Result<TokId> {
        let t = self.tok(i)?;
        if want(state.values.get(t)) { Ok(t) } else { Err(bad(format!("value {i:?} is not a {what}"))) }
    }

    /// Return the value holding decoded tokens `ids`.
    fn toks(&self, state: &mut State, ids: &Json) -> Result<Vals> {
        let toks = ids.items().iter().map(|i| self.tok(i)).collect::<Result<Vec<TokId>>>()?;
        Ok(state.values.of(toks))
    }

    /// Decode an allocation site row.
    fn alloc(&self, a: &Json) -> Result<Option<Alloc>> {
        if *a == Json::Null {
            return Ok(None);
        }
        let it = a.items();
        let n = |i: usize| -> Result<u32> {
            it.get(i)
                .and_then(Json::as_int)
                .and_then(|x| u32::try_from(x).ok())
                .ok_or_else(|| bad("malformed allocation site"))
        };
        Ok(Some(Alloc { func: self.func(it.first().unwrap_or(&Json::Null))?, line: n(1)?, col: n(2)? }))
    }

    /// Decode a call site row.
    fn site(&self, s: &Json) -> Result<Site> {
        let it = s.items();
        let n = |i: usize| -> Result<u32> {
            it.get(i).and_then(Json::as_int).and_then(|x| u32::try_from(x).ok()).ok_or_else(|| bad("malformed site"))
        };
        let text = it.get(3).and_then(Json::as_str).ok_or_else(|| bad("malformed site"))?;
        let kind =
            it.get(4).and_then(Json::as_str).and_then(SiteKind::parse).ok_or_else(|| bad("malformed site kind"))?;
        Ok(Site {
            caller: self.func(it.first().unwrap_or(&Json::Null))?,
            line: n(1)?,
            col: n(2)?,
            text: Name::new(text),
            kind,
        })
    }

    /// Decode an environment object.
    fn env(&self, state: &mut State, e: &Json) -> Result<crate::analysis::Env> {
        let Json::Obj(items) = e else {
            return Err(bad("an environment must be an object"));
        };
        let mut out = crate::analysis::Env::default();
        for (k, ids) in items {
            let v = self.toks(state, ids)?;
            out.insert(Name::new(k), v);
        }
        Ok(out)
    }
}

/// Return the length a row of kind `kind` must have, or None for an unknown kind.
fn row_len(kind: &str) -> Option<usize> {
    match kind {
        "node" => Some(5),
        "clo" | "builtin" | "property" => Some(4),
        "inst" | "box" | "bound" | "super" => Some(3),
        "class" | "classmethod" | "staticmethod" | "module" => Some(2),
        _ => None,
    }
}

/// Decode a `state_to_json` document for `prog`.
///
/// Function and class uids are resolved against `prog`. Returns `Error::Value` if the header
/// is not "callme-state" version 5, if the document was produced for another program or
/// version of it (the fingerprints differ), or if a row is malformed or names an unknown id.
#[allow(clippy::too_many_lines)]
pub fn state_from_json(prog: &Program, doc: &Json) -> Result<State> {
    if doc.get("format").and_then(Json::as_str) != Some(FORMAT)
        || doc.get("version").and_then(Json::as_int) != Some(VERSION)
    {
        return Err(bad(format!("not a {FORMAT} v{VERSION} document")));
    }
    if doc.get("program").and_then(Json::as_str) != Some(prog.fingerprint.as_str()) {
        return Err(bad("state was produced for a different program (or another version of it)"));
    }
    let max_ctx = doc
        .get("max_ctx")
        .and_then(Json::as_int)
        .and_then(|m| usize::try_from(m).ok())
        .ok_or_else(|| bad("missing max_ctx"))?;
    let flow = doc.get("flow_sensitive").and_then(Json::as_bool).ok_or_else(|| bad("missing flow_sensitive"))?;
    let mut state = State::new(max_ctx, Some(prog.fingerprint.clone()), flow);
    let mut d = Decoder {
        funcs: prog.funcs.iter().map(|f| (f.sym.uid(), f.id)).collect(),
        classes: prog.classes.iter().map(|c| (c.sym.uid(), c.id)).collect(),
        vals: Vec::new(),
    };
    let field = |k: &str| doc.get(k).map(Json::items).unwrap_or_default();
    let is_receiver = |t: &Token| matches!(t, Token::ClassObj(_) | Token::Instance { .. });
    for row in field("values") {
        let r = row.items();
        let kind = r.first().and_then(Json::as_str).unwrap_or_default();
        if row_len(kind) != Some(r.len()) {
            return Err(bad(format!("unknown or malformed value row {}", row.compact())));
        }
        let decoded = match kind {
            "node" => {
                let mut params: Vec<(Name, Vals)> = Vec::new();
                for p in r[2].items() {
                    let pi = p.items();
                    let name = pi.first().and_then(Json::as_str).ok_or_else(|| bad("malformed parameter"))?;
                    params.push((Name::new(name), d.toks(&mut state, pi.get(1).unwrap_or(&Json::Null))?));
                }
                let parent = d.opt_node(&r[3])?;
                let merged = r[4].as_bool().ok_or_else(|| bad("malformed merged flag"))?;
                Decoded::Node(state.node_of(d.func(&r[1])?, &params, parent, merged))
            }
            "clo" => {
                let depth = r[3].as_int().and_then(|x| u32::try_from(x).ok()).ok_or_else(|| bad("malformed depth"))?;
                Decoded::Tok(state.values.closure(d.func(&r[1])?, d.opt_node(&r[2])?, depth))
            }
            "builtin" => {
                let name = r[1].as_str().ok_or_else(|| bad("malformed builtin"))?;
                let bound = r[2].items().iter().map(|b| d.toks(&mut state, b)).collect::<Result<Vec<Vals>>>()?;
                let mut bound_kw = Vec::new();
                for kv in r[3].items() {
                    let it = kv.items();
                    let k = it.first().and_then(Json::as_str).ok_or_else(|| bad("malformed keyword"))?;
                    bound_kw.push((Name::new(k), d.toks(&mut state, it.get(1).unwrap_or(&Json::Null))?));
                }
                Decoded::Tok(state.values.tok(Token::Builtin {
                    name: Name::new(name),
                    bound: bound.into(),
                    bound_kw: bound_kw.into(),
                }))
            }
            "class" => Decoded::Tok(state.values.tok(Token::ClassObj(d.cls(&r[1])?))),
            "inst" => Decoded::Tok(state.values.tok(Token::Instance { cls: d.cls(&r[1])?, alloc: d.alloc(&r[2])? })),
            "box" => {
                let alloc = d.alloc(&r[2])?.ok_or_else(|| bad("container token without an allocation site"))?;
                let kind = r[1].as_str().ok_or_else(|| bad("malformed box kind"))?;
                Decoded::Tok(state.values.tok(Token::Box { kind: Name::new(kind), alloc }))
            }
            "bound" => {
                let f = d.tok(&r[1])?;
                let recv = d.tok_of(&state, &r[2], "receiver", is_receiver)?;
                Decoded::Tok(state.values.tok(Token::BoundMethod { func: f, receiver: recv }))
            }
            "classmethod" => Decoded::Tok(state.values.tok(Token::ClassMethod(d.tok(&r[1])?))),
            "staticmethod" => Decoded::Tok(state.values.tok(Token::StaticMethod(d.tok(&r[1])?))),
            "super" => {
                let recv = d.tok_of(&state, &r[2], "receiver", is_receiver)?;
                Decoded::Tok(state.values.tok(Token::Super { start: d.cls(&r[1])?, receiver: recv }))
            }
            "property" => {
                let fget = d.toks(&mut state, &r[1])?;
                let fset = d.toks(&mut state, &r[2])?;
                let fdel = d.toks(&mut state, &r[3])?;
                Decoded::Tok(state.values.tok(Token::Property { fget, fset, fdel }))
            }
            _ => {
                // "module"
                let name = r[1].as_str().ok_or_else(|| bad("malformed module"))?;
                let name = Name::new(name);
                if prog.module(name).is_none() {
                    return Err(bad(format!("unknown module '{name}'")));
                }
                Decoded::Tok(state.values.tok(Token::Module(name)))
            }
        };
        d.vals.push(decoded);
    }
    let pair = |x: &Json| -> (Json, Json) {
        let it = x.items();
        (it.first().cloned().unwrap_or(Json::Null), it.get(1).cloned().unwrap_or(Json::Null))
    };
    for i in field("order") {
        let n = d.node(i)?;
        state.register(n);
    }
    for x in field("envs") {
        let (i, e) = pair(x);
        let env = d.env(&mut state, &e)?;
        state.envs.insert(d.node(&i)?, env);
    }
    for x in field("ret") {
        let (i, t) = pair(x);
        let v = d.toks(&mut state, &t)?;
        state.ret.insert(d.node(&i)?, v);
    }
    for x in field("entry") {
        let (i, e) = pair(x);
        let env = d.env(&mut state, &e)?;
        state.entry.insert(d.node(&i)?, env);
    }
    for x in field("edges") {
        let it = x.items();
        if it.len() != 3 {
            return Err(bad("malformed edge"));
        }
        state.edges.insert(Edge { caller: d.node(&it[0])?, site: d.site(&it[1])?, callee: d.node(&it[2])? });
    }
    for x in field("class_attrs") {
        let (c, e) = pair(x);
        let env = d.env(&mut state, &e)?;
        state.class_attrs.insert(d.cls(&c)?, env);
    }
    let is_inst = |t: &Token| matches!(t, Token::Instance { .. });
    let is_box = |t: &Token| matches!(t, Token::Box { .. });
    for x in field("inst_attrs") {
        let (i, e) = pair(x);
        let env = d.env(&mut state, &e)?;
        let t = d.tok_of(&state, &i, "Instance", is_inst)?;
        state.inst_attrs.insert(t, env);
    }
    for x in field("heap") {
        let (i, t) = pair(x);
        let v = d.toks(&mut state, &t)?;
        let b = d.tok_of(&state, &i, "Box", is_box)?;
        state.heap.insert(b, v);
    }
    for x in field("extern") {
        let (f, e) = pair(x);
        let env = d.env(&mut state, &e)?;
        state.extern_.insert(d.func(&f)?, env);
    }
    for x in field("writes") {
        let (i, ws) = pair(x);
        let mut w = crate::analysis::Writes::default();
        for fe in ws.items() {
            let (f, e) = pair(fe);
            let env = d.env(&mut state, &e)?;
            w.insert(d.func(&f)?, env);
        }
        state.writes.insert(d.node(&i)?, w);
    }
    for x in field("bases") {
        let (c, bs) = pair(x);
        let bases = bs.items().iter().map(|b| d.cls(b)).collect::<Result<Vec<ClassId>>>()?;
        state.bases.insert(d.cls(&c)?, bases);
    }
    for x in field("metaclasses") {
        let (c, ms) = pair(x);
        let metas = ms.items().iter().map(|m| d.cls(m)).collect::<Result<Vec<ClassId>>>()?;
        state.metaclasses.insert(d.cls(&c)?, metas.into_iter().collect());
    }
    for x in field("unknown_decos") {
        let (n, s) = pair(x);
        state.unknown_decos.insert((d.node(&n)?, d.site(&s)?));
    }
    state.changed = false;
    Ok(state)
}

/// Write `state` to the file at `path` as a "callme-state" JSON document.
pub fn save_state(state: &State, prog: &Program, path: &str) -> Result<()> {
    std::fs::write(path, state_to_json(state, prog).compact()).map_err(|err| Error::Io { path: path.into(), err })
}

/// Read a state saved by `save_state` (or by the Python version) from `path`, for `prog`.
///
/// The state keeps its own `max_ctx` and flow mode. Returns `Error::Value` as
/// `state_from_json` does, e.g. when the source changed since the state was saved.
pub fn load_state(prog: &Program, path: &str) -> Result<State> {
    let text = std::fs::read_to_string(path).map_err(|err| Error::Io { path: path.into(), err })?;
    let doc = parse(&text).map_err(|e| bad(format!("{path}: {e}")))?;
    state_from_json(prog, &doc)
}

/// An (encode, decode) pair for one pass's state.
pub struct Codec {
    /// encode a pass state
    pub encode: fn(&dyn PassState, &Program) -> Result<Json>,
    /// decode a pass state for a program
    pub decode: fn(&Program, &Json) -> Result<Box<dyn PassState>>,
}

/// Encode the call graph pass state; an `Error::Type` if it is not a `State`.
fn encode_callgraph(state: &dyn PassState, prog: &Program) -> Result<Json> {
    let s = state
        .as_any()
        .downcast_ref::<State>()
        .ok_or_else(|| Error::Type(format!("callgraph state must be a State, not a {}", state.type_name())))?;
    Ok(state_to_json(s, prog))
}

/// Decode the call graph pass state.
fn decode_callgraph(prog: &Program, doc: &Json) -> Result<Box<dyn PassState>> {
    Ok(Box::new(state_from_json(prog, doc)?))
}

/// Return the codecs of the built-in passes, by pass name.
pub fn codecs() -> BTreeMap<String, Codec> {
    let mut m = BTreeMap::new();
    m.insert("callgraph".to_owned(), Codec { encode: encode_callgraph, decode: decode_callgraph });
    m
}

/// Encode a `Facts` store as a "callme-facts" document, each pass through its codec.
///
/// Returns `Error::Value` if a pass has no codec.
pub fn facts_to_json(facts: &Facts, prog: &Program, codecs: &BTreeMap<String, Codec>) -> Result<Json> {
    let mut missing: Vec<&String> = facts.states.keys().filter(|k| !codecs.contains_key(*k)).collect();
    if !missing.is_empty() {
        missing.sort();
        let names: Vec<String> = missing.iter().map(|m| format!("'{m}'")).collect();
        return Err(bad(format!("no codec for passes [{}]", names.join(", "))));
    }
    let mut states: IndexMap<String, Json> = IndexMap::new();
    for (k, v) in &facts.states {
        states.insert(k.clone(), (codecs[k].encode)(v.as_ref(), prog)?);
    }
    Ok(obj! {
        "format" => "callme-facts",
        "version" => Json::Int(VERSION),
        "program" => facts.program.as_str(),
        "states" => Json::Obj(states.into_iter().collect()),
    })
}

/// Decode a `facts_to_json` document for `prog`, each pass through its codec.
///
/// Returns `Error::Value` if it is not a "callme-facts" document for this program.
pub fn facts_from_json(prog: &Program, doc: &Json, codecs: &BTreeMap<String, Codec>) -> Result<Facts> {
    if doc.get("format").and_then(Json::as_str) != Some("callme-facts")
        || doc.get("program").and_then(Json::as_str) != Some(prog.fingerprint.as_str())
    {
        return Err(bad("not a callme-facts document for this program"));
    }
    let mut facts = Facts::new(prog.fingerprint.clone());
    if let Some(Json::Obj(states)) = doc.get("states") {
        for (k, v) in states {
            let codec = codecs.get(k).ok_or_else(|| bad(format!("no codec for pass '{k}'")))?;
            facts.states.insert(k.clone(), (codec.decode)(prog, v)?);
        }
    }
    Ok(facts)
}
