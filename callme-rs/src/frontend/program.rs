//! The `Program`: what step 1 builds, with the per-function, per-class and per-module records.
//!
//! `FuncInfo`, `ClassInfo` and `ModuleInfo` describe one definition each; `Sig` is a
//! function's signature. `builder::ProgramBuilder` fills them in.

use std::sync::{Arc, Mutex};

use indexmap::{IndexMap, IndexSet};
use rustc_hash::{FxBuildHasher, FxHashMap};

use crate::model::{CFG, ClassId, FuncId, IdTable, Site, SiteKind, SymId};
use crate::syntax::Name;
use crate::syntax::ast::{Arguments, ClassDef, Expr, FunctionDef, Lambda, Module, ScopeKey, Stmt};
use crate::syntax::walk::NodeRef;

/// The qualname of a module body.
pub const MODULE_NAME: &str = "<module>";

/// A set of names, iterated in insertion order.
pub type NameSet = IndexSet<Name, FxBuildHasher>;

/// Where a parameter's default value is: in `Arguments.defaults` or `Arguments.kw_defaults`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DefaultRef {
    /// index into `defaults`
    Pos(usize),
    /// index into `kw_defaults`
    Kw(usize),
}

/// Signature of a function, as needed to bind call arguments to parameters.
///
/// ```text
/// pos       positional parameters, positional-only ones included, in order
/// kwonly    keyword-only parameters
/// vararg    name of the `*args` parameter, or None
/// kwarg     name of the `**kwargs` parameter, or None
/// defaults  (parameter, default expression) pairs; the expressions are evaluated where the
///           function is defined
/// required  parameters without a default
/// ```
#[derive(Clone, Debug, Default)]
#[allow(missing_docs)]
pub struct Sig {
    pub pos: Vec<Name>,
    pub kwonly: Vec<Name>,
    pub vararg: Option<Name>,
    pub kwarg: Option<Name>,
    pub defaults: Vec<(Name, DefaultRef)>,
    pub required: Vec<Name>,
}

/// What a function is: a module body, a def or a lambda.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FuncKind {
    /// a module body
    Module,
    /// a def or async def
    Def,
    /// a lambda
    Lambda,
}

impl FuncKind {
    /// Return "module", "def" or "lambda".
    pub fn as_str(self) -> &'static str {
        match self {
            FuncKind::Module => "module",
            FuncKind::Def => "def",
            FuncKind::Lambda => "lambda",
        }
    }
}

/// The syntax node of a function.
#[derive(Clone, Debug)]
pub enum ScopeNode {
    /// a module body
    Module(Arc<Module>),
    /// a def or async def
    Def(Arc<FunctionDef>),
    /// a lambda
    Lambda(Arc<Lambda>),
}

/// The code a function runs: statements, or a lambda's single expression.
#[derive(Clone, Copy, Debug)]
pub enum Body<'a> {
    /// a module's or def's statements
    Stmts(&'a [Stmt]),
    /// a lambda's body
    Expr(&'a Expr),
}

impl<'a> Body<'a> {
    /// Return the body as syntax nodes.
    pub fn nodes(self) -> Vec<NodeRef<'a>> {
        match self {
            Body::Stmts(s) => s.iter().map(NodeRef::Stmt).collect(),
            Body::Expr(e) => vec![NodeRef::Expr(e)],
        }
    }
}

/// Everything the analysis knows statically about one function.
///
/// ```text
/// kind       module, def or lambda
/// node       the syntax node: the module, a def or a lambda
/// parent     lexically enclosing function, or None for the module body; classes in between
///            are skipped, as in Python's own scoping
/// locals     names bound in the function's own scope, parameters included and names
///            declared `global` or `nonlocal` excluded
/// owner      class whose body defines the function directly, or None
/// globals    names declared `global`, which are written in the module
/// nonlocals  names declared `nonlocal`, which are written in an enclosing function
/// must_writes  subset of `globals | nonlocals` assigned on every normal return path
/// ```
#[derive(Debug)]
#[allow(missing_docs)]
pub struct FuncInfo {
    pub id: FuncId,
    pub sym: SymId,
    pub kind: FuncKind,
    pub node: ScopeNode,
    pub parent: Option<FuncId>,
    pub sig: Sig,
    pub locals: NameSet,
    pub owner: Option<ClassId>,
    pub globals: NameSet,
    pub nonlocals: NameSet,
    pub must_writes: NameSet,
}

impl FuncInfo {
    /// Return the code the function runs.
    pub fn body(&self) -> Body<'_> {
        match &self.node {
            ScopeNode::Module(m) => Body::Stmts(&m.body),
            ScopeNode::Def(d) => Body::Stmts(&d.body),
            ScopeNode::Lambda(l) => Body::Expr(&l.body),
        }
    }

    /// Return the parameter list of a def or lambda, or None for a module body.
    pub fn args(&self) -> Option<&Arguments> {
        match &self.node {
            ScopeNode::Module(_) => None,
            ScopeNode::Def(d) => Some(&d.args),
            ScopeNode::Lambda(l) => Some(&l.args),
        }
    }

    /// Return the default value expression `d` of one of the function's parameters.
    pub fn default_expr(&self, d: DefaultRef) -> &Expr {
        let a = self.args().expect("only defs and lambdas have defaults");
        match d {
            DefaultRef::Pos(i) => &a.defaults[i],
            DefaultRef::Kw(i) => a.kw_defaults[i].as_ref().expect("a keyword default"),
        }
    }

    /// Return True for an `async def`.
    pub fn is_async(&self) -> bool {
        matches!(&self.node, ScopeNode::Def(d) if d.is_async)
    }
}

/// Everything the analysis knows statically about one class definition.
///
/// ```text
/// parent       function (or module body) whose code runs the class statement
/// known_attrs  names annotated in the class body or assigned as `self.<name>` in its methods
/// ```
#[derive(Debug)]
#[allow(missing_docs)]
pub struct ClassInfo {
    pub id: ClassId,
    pub sym: SymId,
    pub node: Arc<ClassDef>,
    pub parent: FuncId,
    pub known_attrs: NameSet,
}

impl ClassInfo {
    /// Return the statements of the class body.
    pub fn body(&self) -> &[Stmt] {
        &self.node.body
    }
}

/// One module of the program: a source file, or a namespace package that has none.
///
/// ```text
/// file     source path, or None for a namespace package (a directory without `__init__.py`)
/// func     id of the module body, or None for a namespace package
/// package  True for a package; relative imports in its `__init__` resolve against itself
/// exports  names that `from <name> import *` binds: the literal `__all__` when there is
///          one, otherwise every module-level name that does not start with "_"
/// ```
#[derive(Debug, Clone)]
#[allow(missing_docs)]
pub struct ModuleInfo {
    pub name: Name,
    pub file: Option<String>,
    pub func: Option<FuncId>,
    pub package: bool,
    pub exports: NameSet,
}

/// A parsed program: the functions, classes and CFGs of all its modules.
///
/// ```text
/// file           path of the first entry file
/// module         id of the first entry's module body
/// by_node        function id of each def, lambda and module node, by scope key
/// class_by_node  class id of each class node, by scope key
/// cfgs           one CFG per function, indexed like `funcs`
/// fingerprint    hash of every file name and source; saved states are only valid for the
///                program with the same fingerprint
/// modules        every module of the program by dotted name, namespace packages included;
///                a file imported under two names is one module with two names
/// entries        module bodies of the entry files, the roots of the analysis
/// site_cache     the `Site` of each (expression, kind) the interpreter made, by the
///                expression's address, which is stable while the program lives
/// ```
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Program {
    pub file: String,
    pub module: FuncId,
    pub funcs: Vec<FuncInfo>,
    pub by_node: FxHashMap<ScopeKey, FuncId>,
    pub classes: Vec<ClassInfo>,
    pub class_by_node: FxHashMap<ScopeKey, ClassId>,
    pub cfgs: Vec<CFG>,
    pub fingerprint: String,
    pub modules: IndexMap<Name, usize, FxBuildHasher>,
    pub module_infos: Vec<ModuleInfo>,
    pub entries: Vec<FuncId>,
    pub site_cache: Mutex<FxHashMap<(usize, SiteKind), Site>>,
}

impl Program {
    /// Return `make_site(caller, e, kind)`, computed once per expression and kind.
    ///
    /// The interpreter makes the same implicit sites (attribute reads, operators, ...) every
    /// time it evaluates an expression, and unparsing is the costly part of a site.
    pub fn site(&self, caller: FuncId, e: &Expr, kind: SiteKind) -> Site {
        let key = (std::ptr::from_ref(e) as usize, kind);
        let mut cache = self.site_cache.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        *cache.entry(key).or_insert_with(|| super::sites::make_site(caller, e, kind))
    }

    /// Return the record of function `f`.
    pub fn func(&self, f: FuncId) -> &FuncInfo {
        &self.funcs[f.index()]
    }

    /// Return the record of class `c`.
    pub fn class(&self, c: ClassId) -> &ClassInfo {
        &self.classes[c.index()]
    }

    /// Return the CFG of function `f`.
    pub fn cfg(&self, f: FuncId) -> &CFG {
        &self.cfgs[f.index()]
    }

    /// Return the module named `name`, if the program has it.
    pub fn module(&self, name: Name) -> Option<&ModuleInfo> {
        self.modules.get(&name).map(|&i| &self.module_infos[i])
    }

    /// Return every (name, module) pair, alias names included, in load order.
    pub fn modules_by_name(&self) -> impl Iterator<Item = (Name, &ModuleInfo)> {
        self.modules.iter().map(|(n, &i)| (*n, &self.module_infos[i]))
    }

    /// Return the function-identifier graph as (lexical parent, child) pairs.
    pub fn nesting_edges(&self) -> Vec<(FuncId, FuncId)> {
        self.funcs.iter().filter_map(|i| i.parent.map(|p| (p, i.id))).collect()
    }

    /// Return the module that defines a function or class with identity `sym`.
    pub fn module_of(&self, sym: &SymId) -> &ModuleInfo {
        self.module(Name::new(&sym.module)).expect("every definition is in a module of the program")
    }

    /// Return the id of the module body that defines a function or class with identity `sym`.
    pub fn module_body(&self, sym: &SymId) -> FuncId {
        self.module_of(sym).func.expect("a function is always defined in a module with a body")
    }

    /// Return the function whose `full` id is `uid`, if any.
    pub fn func_by_uid(&self, uid: &str) -> Option<FuncId> {
        self.funcs.iter().find(|f| f.sym.uid() == uid).map(|f| f.id)
    }

    /// Return the class whose `full` id is `uid`, if any.
    pub fn class_by_uid(&self, uid: &str) -> Option<ClassId> {
        self.classes.iter().find(|c| c.sym.uid() == uid).map(|c| c.id)
    }
}

impl IdTable for Program {
    fn func_sym(&self, f: FuncId) -> &SymId {
        &self.funcs[f.index()].sym
    }

    fn class_sym(&self, c: ClassId) -> &SymId {
        &self.classes[c.index()].sym
    }
}
