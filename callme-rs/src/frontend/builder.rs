//! Building a `Program` from parsed modules: ids, scopes, signatures, star imports and CFGs.
//!
//! `ProgramBuilder` takes one module at a time (`add_module`) and then links them (`finish`).
//! `build_program` uses it for a single source string; `loader` for files on disk.

use std::collections::BTreeMap;
use std::sync::Arc;

use indexmap::IndexMap;
use rustc_hash::{FxBuildHasher, FxHashMap};
use sha2::{Digest, Sha256};

use crate::error::{Error, Result};
use crate::model::{ClassId, FuncId, SymId, module_name};
use crate::syntax::Name;
use crate::syntax::ast::{Expr, Module, ScopeKey, Stmt};
use crate::syntax::parse_module;
use crate::syntax::walk::NodeRef;

use super::cfg::build_cfg;
use super::program::{ClassInfo, FuncInfo, FuncKind, MODULE_NAME, ModuleInfo, NameSet, Program, ScopeNode, Sig};
use super::scopes::{
    class_known_attrs, make_sig, outer_parts, scope_decls, scope_locals, scope_must_writes, walk_scope,
};

/// Return the names a module lists in `__all__`, or None when it has no literal `__all__`.
///
/// Module-level `__all__ = [...]`, `__all__ += [...]`, `__all__.extend([...])` and
/// `__all__.append("x")` count, with string constants only. Any other write to `__all__`
/// makes it unknown, and None is returned.
pub fn module_all(body: &[Stmt]) -> Option<NameSet> {
    /// Return the strings of a literal list or tuple (or one string), or None.
    fn strings(e: &Expr) -> Option<Vec<Name>> {
        let elts: Vec<&Expr> = match e {
            Expr::List(x) | Expr::Tuple(x) => x.elts.iter().collect(),
            other => vec![other],
        };
        let out: Vec<Name> = elts.iter().filter_map(|x| x.as_str_constant()).map(Name::new).collect();
        (out.len() == elts.len()).then_some(out)
    }
    let is_all = |e: &Expr| matches!(e, Expr::Name(n) if &*n.id == "__all__");
    let mut names = NameSet::default();
    let mut seen = false;
    for n in walk_scope(body.iter().map(NodeRef::Stmt)) {
        let value: Option<&Expr> = match n {
            NodeRef::Stmt(Stmt::Assign(a)) if a.targets.iter().any(is_all) => Some(&a.value),
            NodeRef::Stmt(Stmt::AugAssign(a)) if is_all(&a.target) => Some(&a.value),
            NodeRef::Stmt(Stmt::AnnAssign(a)) if is_all(&a.target) => a.value.as_ref(),
            NodeRef::Expr(Expr::Call(c)) => match &*c.func {
                Expr::Attribute(at)
                    if is_all(&at.value) && matches!(&*at.attr, "extend" | "append") && c.args.len() == 1 =>
                {
                    Some(&c.args[0])
                }
                _ => continue,
            },
            _ => continue,
        };
        let found = strings(value?)?;
        seen = true;
        names.extend(found);
    }
    seen.then_some(names)
}

/// Return the absolute name of the module that an import in `current` names.
///
/// ```text
/// level   number of leading dots; 0 is an absolute import, which returns `module` itself
/// module  the dotted name after the dots, or None for `from . import x`
/// ```
///
/// A relative import counts from `current`'s package (`current` itself for a package).
/// Return None when it climbs above the top-level package, where Python raises `ImportError`.
pub fn absolute_module(current: &ModuleInfo, level: u32, module: Option<Name>) -> Option<Name> {
    if level == 0 {
        return module;
    }
    let pkg: &str = if current.package { &current.name } else { current.name.rsplit_once('.').map_or("", |(p, _)| p) };
    let parts: Vec<&str> = if pkg.is_empty() { Vec::new() } else { pkg.split('.').collect() };
    let up = (level - 1) as usize;
    if up >= parts.len() {
        return None;
    }
    let base = parts[..parts.len() - up].join(".");
    Some(Name::new(&match module {
        Some(m) => format!("{base}.{m}"),
        None => base,
    }))
}

/// Return a hash that is the same for two programs exactly when file names and sources match.
///
/// files  source text by file name, hashed in sorted order as `name\0source`, joined by `\0`
pub fn fingerprint(files: &BTreeMap<String, String>) -> String {
    let mut h = Sha256::new();
    for (i, (name, src)) in files.iter().enumerate() {
        if i > 0 {
            h.update(b"\0");
        }
        h.update(name.as_bytes());
        h.update(b"\0");
        h.update(src.as_bytes());
    }
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Collects the functions, classes and modules of a program while its files are parsed.
#[derive(Default)]
pub struct ProgramBuilder {
    /// every function so far, indexed by id
    pub funcs: Vec<FuncInfo>,
    /// function id by scope key
    by_node: FxHashMap<ScopeKey, FuncId>,
    /// every class so far, indexed by id
    classes: Vec<ClassInfo>,
    /// class id by scope key
    class_by_node: FxHashMap<ScopeKey, ClassId>,
    /// module index by name; a file loaded under two names has two entries
    pub modules: IndexMap<Name, usize, FxBuildHasher>,
    /// the modules
    pub module_infos: Vec<ModuleInfo>,
    /// source by file name
    sources: BTreeMap<String, String>,
}

/// The state of registering one module's functions and classes.
struct ModuleWalk<'b> {
    /// the builder being filled
    b: &'b mut ProgramBuilder,
    /// the module's file name, recorded in every id
    filename: &'b str,
    /// the module's dotted name, recorded in every id
    name: &'b str,
    /// the functions of this module, module body first
    own: Vec<FuncId>,
}

impl ModuleWalk<'_> {
    /// Register a def or lambda under `qualname` and return its new id.
    fn add(&mut self, node: ScopeNode, qualname: String, parent: FuncId, owner: Option<ClassId>) -> FuncId {
        let fid = FuncId(u32::try_from(self.b.funcs.len()).expect("fewer than 2^32 functions"));
        let (pos, key, kind) = match &node {
            ScopeNode::Def(d) => (d.pos, d.key, FuncKind::Def),
            ScopeNode::Lambda(l) => (l.pos, l.key, FuncKind::Lambda),
            ScopeNode::Module(_) => unreachable!("module bodies are added by add_module"),
        };
        let info = {
            let (args, body, is_plain_def) = match &node {
                ScopeNode::Def(d) => (&d.args, d.body.iter().map(NodeRef::Stmt).collect::<Vec<_>>(), !d.is_async),
                ScopeNode::Lambda(l) => (&l.args, vec![NodeRef::Expr(&l.body)], false),
                ScopeNode::Module(_) => unreachable!(),
            };
            let sig = make_sig(args);
            let mut params: Vec<Name> = sig.pos.iter().chain(&sig.kwonly).copied().collect();
            params.extend(sig.vararg);
            params.extend(sig.kwarg);
            let (glob, nonloc) = scope_decls(body.iter().copied());
            let tracked: NameSet = glob.iter().chain(&nonloc).copied().collect();
            let must = match &node {
                ScopeNode::Def(d) => scope_must_writes(is_plain_def, &d.body, &tracked),
                _ => NameSet::default(),
            };
            let locals = scope_locals(body.iter().copied(), &params);
            FuncInfo {
                id: fid,
                sym: SymId::new(qualname, self.filename.to_owned(), pos.line, pos.col, self.name),
                kind,
                node,
                parent: Some(parent),
                sig,
                locals,
                owner,
                globals: glob,
                nonlocals: nonloc,
                must_writes: must,
            }
        };
        self.b.funcs.push(info);
        self.b.by_node.insert(key, fid);
        self.own.push(fid);
        fid
    }

    /// Register every function and class found under `node`.
    ///
    /// ```text
    /// prefix  qualname prefix of the scope being walked, e.g. "Outer.make."
    /// parent  function whose code runs `node`
    /// owner   class whose body `node` is directly in, if any
    /// ```
    fn walk(&mut self, node: NodeRef<'_>, prefix: &str, parent: FuncId, owner: Option<ClassId>) {
        match node {
            NodeRef::Stmt(Stmt::FunctionDef(d)) => {
                let fid = self.add(ScopeNode::Def(d.clone()), format!("{prefix}{}", d.name), parent, owner);
                let inner = format!("{}.", self.b.funcs[fid.index()].sym.qualname);
                for n in &d.decorator_list {
                    self.walk(NodeRef::Expr(n), prefix, parent, None);
                }
                for n in &outer_parts(node)[d.decorator_list.len()..] {
                    // lambdas in default values are named def.<lambda>
                    self.walk(*n, &inner, parent, None);
                }
                for s in &d.body {
                    self.walk(NodeRef::Stmt(s), &inner, fid, None);
                }
            }
            NodeRef::Expr(Expr::Lambda(l)) => {
                let fid =
                    self.add(ScopeNode::Lambda(l.clone()), format!("{prefix}<lambda>@{}", l.pos.line), parent, None);
                for n in outer_parts(node) {
                    self.walk(n, prefix, parent, None);
                }
                self.walk(NodeRef::Expr(&l.body), prefix, fid, None);
            }
            NodeRef::Stmt(Stmt::ClassDef(c)) => {
                let cid = ClassId(u32::try_from(self.b.classes.len()).expect("fewer than 2^32 classes"));
                let sym = SymId::new(
                    format!("{prefix}{}", c.name),
                    self.filename.to_owned(),
                    c.pos.line,
                    c.pos.col,
                    self.name,
                );
                self.b.classes.push(ClassInfo {
                    id: cid,
                    sym,
                    node: c.clone(),
                    parent,
                    known_attrs: class_known_attrs(&c.body),
                });
                self.b.class_by_node.insert(c.key, cid);
                for n in outer_parts(node) {
                    self.walk(n, prefix, parent, None);
                }
                let inner = format!("{prefix}{}.", c.name);
                for s in &c.body {
                    self.walk(NodeRef::Stmt(s), &inner, parent, Some(cid));
                }
            }
            other => other.for_each_child(&mut |c| self.walk(c, prefix, parent, owner)),
        }
    }
}

impl ProgramBuilder {
    /// Start with no modules.
    pub fn new() -> ProgramBuilder {
        ProgramBuilder::default()
    }

    /// Return the module record at index `i`.
    pub fn module_info(&self, i: usize) -> &ModuleInfo {
        &self.module_infos[i]
    }

    /// Return the syntax tree of module `info`, or None for a namespace package.
    pub fn tree(&self, info: &ModuleInfo) -> Option<Arc<Module>> {
        match &self.funcs[info.func?.index()].node {
            ScopeNode::Module(m) => Some(m.clone()),
            _ => None,
        }
    }

    /// Register namespace package `name`, which has no source of its own, and return its index.
    pub fn add_namespace(&mut self, name: Name) -> usize {
        let i = self.module_infos.len();
        self.module_infos.push(ModuleInfo { name, file: None, func: None, package: true, exports: NameSet::default() });
        self.modules.insert(name, i);
        i
    }

    /// Parse `source` as module `name` and register its functions and classes; return the
    /// module's index.
    ///
    /// Every def, async def and lambda gets a `FuncInfo`, and every class a `ClassInfo`. A
    /// qualname is the dotted path of enclosing defs and classes. A lambda is named
    /// `<lambda>@<line>`, and a lambda in a def's default values is put under that def.
    /// `filename` and `name` are recorded in every id. Returns `Error::Syntax` for invalid
    /// source.
    pub fn add_module(&mut self, source: &str, filename: &str, name: Name, package: bool) -> Result<usize> {
        let tree = Arc::new(parse_module(source).map_err(|err| Error::Syntax { file: filename.to_owned(), err })?);
        let mod_id = FuncId(u32::try_from(self.funcs.len()).expect("fewer than 2^32 functions"));
        let body: Vec<NodeRef<'_>> = tree.body.iter().map(NodeRef::Stmt).collect();
        let locals = scope_locals(body.iter().copied(), &[]);
        self.funcs.push(FuncInfo {
            id: mod_id,
            sym: SymId::new(MODULE_NAME.to_owned(), filename.to_owned(), 1, 0, &name),
            kind: FuncKind::Module,
            node: ScopeNode::Module(tree.clone()),
            parent: None,
            sig: Sig::default(),
            locals,
            owner: None,
            globals: NameSet::default(),
            nonlocals: NameSet::default(),
            must_writes: NameSet::default(),
        });
        self.by_node.insert(tree.key, mod_id);
        let mut w = ModuleWalk { b: self, filename, name: &name, own: vec![mod_id] };
        for s in &tree.body {
            w.walk(NodeRef::Stmt(s), "", mod_id, None);
        }
        let own = w.own;
        // `global x` makes x a module variable even if the module itself never assigns it
        let globals: Vec<Name> = own.iter().flat_map(|f| self.funcs[f.index()].globals.iter().copied()).collect();
        self.funcs[mod_id.index()].locals.extend(globals);
        let i = self.module_infos.len();
        self.module_infos.push(ModuleInfo {
            name,
            file: Some(filename.to_owned()),
            func: Some(mod_id),
            package,
            exports: NameSet::default(),
        });
        self.modules.insert(name, i);
        self.sources.insert(filename.to_owned(), source.to_owned());
        Ok(i)
    }

    /// Bind the names of every `from m import *` of a program module, and set `exports`.
    ///
    /// A star import makes the exported names module-level locals of the importer, so that
    /// `bind` and `lookup` see them. Star imports of star imports are followed until nothing
    /// is added. Names from modules outside the program stay unknown.
    pub fn link_star_imports(&mut self) {
        let mut bodies: IndexMap<Name, FuncId, FxBuildHasher> = IndexMap::default();
        let mut literal: FxHashMap<Name, Option<NameSet>> = FxHashMap::default();
        let mut stars: IndexMap<Name, Vec<Name>, FxBuildHasher> = IndexMap::default();
        for (&name, &i) in &self.modules {
            let info = &self.module_infos[i];
            let (Some(tree), Some(func)) = (self.tree(info), info.func) else {
                continue;
            };
            bodies.insert(name, func);
            literal.insert(name, module_all(&tree.body));
            let mut bases = Vec::new();
            for n in walk_scope(tree.body.iter().map(NodeRef::Stmt)) {
                if let NodeRef::Stmt(Stmt::ImportFrom(f)) = n
                    && f.names.iter().any(|a| &*a.name == "*")
                    && let Some(base) = absolute_module(info, f.level, f.module)
                    && self.modules.contains_key(&base)
                {
                    bases.push(base);
                }
            }
            stars.insert(name, bases);
        }
        let exports = |b: &ProgramBuilder, name: Name| -> NameSet {
            let Some(&func) = bodies.get(&name) else {
                return NameSet::default(); // a namespace package
            };
            if let Some(Some(lit)) = literal.get(&name) {
                return lit.clone();
            }
            b.funcs[func.index()].locals.iter().filter(|n| !n.starts_with('_')).copied().collect()
        };
        let mut changed = true;
        while changed {
            changed = false;
            for (name, bases) in &stars {
                let body = bodies[name];
                let mut new = NameSet::default();
                for &b in bases {
                    new.extend(exports(self, b));
                }
                let locals = &self.funcs[body.index()].locals;
                new.retain(|n| !locals.contains(n));
                if !new.is_empty() {
                    self.funcs[body.index()].locals.extend(new);
                    changed = true;
                }
            }
        }
        let names: Vec<(Name, usize)> = self.modules.iter().map(|(n, i)| (*n, *i)).collect();
        for (name, i) in names {
            let e = exports(self, name);
            self.module_infos[i].exports = e;
        }
    }

    /// Link star imports, build every CFG, and return the `Program` rooted at `entries`
    /// (module indices).
    pub fn finish(mut self, entries: &[usize]) -> Program {
        self.link_star_imports();
        let cfgs = self.funcs.iter().map(build_cfg).collect();
        let first = &self.module_infos[entries[0]];
        let file = first.file.clone().expect("entries are files");
        let module = first.func.expect("entries have bodies");
        let entry_funcs = entries.iter().filter_map(|&i| self.module_infos[i].func).collect();
        Program {
            file,
            module,
            funcs: self.funcs,
            by_node: self.by_node,
            classes: self.classes,
            class_by_node: self.class_by_node,
            cfgs,
            fingerprint: fingerprint(&self.sources),
            modules: self.modules,
            module_infos: self.module_infos,
            entries: entry_funcs,
            site_cache: std::sync::Mutex::default(),
        }
    }
}

/// Parse `source` and return its `Program`, with ids, scopes, signatures and CFGs.
///
/// The program has one module, named after `filename` (`model::module_name`); its imports are
/// not followed. `filename` is recorded in every id and in the fingerprint. Returns
/// `Error::Syntax` for invalid source.
pub fn build_program(source: &str, filename: &str) -> Result<Program> {
    let mut b = ProgramBuilder::new();
    let stem = filename.rsplit('/').next().unwrap_or(filename);
    let stem = stem.rsplit_once('.').map_or(stem, |(s, _)| if s.is_empty() { stem } else { s });
    let i = b.add_module(source, filename, Name::new(&module_name(filename)), stem == "__init__")?;
    Ok(b.finish(&[i]))
}
