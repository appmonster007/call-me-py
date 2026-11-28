//! Layer 2 of the interpreter: modules. Running an imported module's body, reading
//! `mod.attr`, and the `import` statements.

use crate::frontend::{NameSet, absolute_module};
use crate::model::{EMPTY, Token, Vals};
use crate::stubs::{BUILTIN_NAMES, STUB_MODULES, STUB_PACKAGES, STUBS};
use crate::syntax::Name;
use crate::syntax::ast::{Import, ImportFrom};

use super::base::Interp;
use crate::analysis::state::DepKey;

/// Return the token for the stub module or callable named `name`.
fn builtin(name: Name) -> Token {
    Token::Builtin { name, bound: Vec::new().into(), bound_kw: Vec::new().into() }
}

impl Interp<'_> {
    /// Run the body of program module `name` and of its parent packages, as an import does.
    ///
    /// Each body's node is registered, so that it is analysed, but no call edge is recorded,
    /// because a traced run shows no call into a module body either. A namespace package has
    /// no body, and read-only mode registers nothing.
    pub fn run_module(&mut self, name: Name) {
        if self.readonly {
            return;
        }
        let parts: Vec<&str> = name.split('.').collect();
        for i in 1..=parts.len() {
            let prefix = Name::new(&parts[..i].join("."));
            let Some(func) = self.prog.module(prefix).and_then(|m| m.func) else {
                continue;
            };
            let mod_node = self.state.node_of(func, &[], None, false);
            self.state.register(mod_node);
            self.state.read(DepKey::Writes(mod_node));
            if self.flow
                && let Some(mw) = self.state.writes.get(mod_node).cloned()
                && !mw.is_empty()
            {
                self.apply_writes(&mw, &NameSet::default());
            }
        }
    }

    /// Return attribute `attr` of program module `name`: one of its globals, or a submodule.
    ///
    /// A global's value is its summary in the module body, plus what other scopes stored into
    /// it (`global` writes and `module.attr = ...`, in `State.extern_`). A submodule
    /// `name.attr` of the program is an attribute too, as Python sets it on its package once it
    /// is imported.
    pub fn module_attr(&mut self, name: Name, attr: Name) -> Vals {
        let mut out = EMPTY;
        if let Some(body) = self.prog.module(name).and_then(|m| m.func) {
            let summary = self.state.union_env(body, attr);
            self.state.read(DepKey::Extern(body));
            let ext = self.state.extern_.get(body).and_then(|e| e.get(&attr)).copied().unwrap_or(EMPTY);
            out = self.u(summary, ext);
        }
        let sub = Name::new(&format!("{name}.{attr}"));
        if self.prog.module(sub).is_some() {
            let m = self.one(Token::Module(sub));
            out = self.u(out, m);
        }
        out
    }

    /// Return every module of the program, and run each, as an import of an unknown name may.
    ///
    /// This models `__import__(name)` and `importlib.import_module(name)`: the analysis does
    /// not track strings, so any module may be the one imported.
    pub fn import_any(&mut self) -> Vals {
        let names: Vec<Name> = self.prog.modules.keys().copied().collect();
        for &n in &names {
            self.run_module(n);
        }
        let toks = names.into_iter().map(|n| self.state.values.tok(Token::Module(n))).collect();
        self.state.values.of(toks)
    }

    /// Run `import`: bind program modules as `Module`s and stub modules as `Builtin`s.
    ///
    /// `import a.b.c` runs `a`, `a.b` and `a.b.c` and binds `a`; `import a.b.c as x` binds `x`
    /// to `a.b.c` itself. A program module wins over a stub module of the same name. Any other
    /// module binds nothing, so calls through it get no edge.
    pub fn s_import(&mut self, s: &Import) {
        for a in &s.names {
            let top = Name::new(a.name.split('.').next().unwrap_or(&a.name));
            if self.prog.module(a.name).is_some() {
                self.run_module(a.name);
                let m = self.one(Token::Module(if a.asname.is_some() { a.name } else { top }));
                self.bind(a.asname.unwrap_or(top), m, false);
            } else if let Some(asname) = a.asname
                && (STUB_MODULES.contains(&a.name) || STUB_PACKAGES.contains(&a.name))
            {
                let b = self.one(builtin(a.name));
                self.bind(asname, b, false);
            } else if a.asname.is_none()
                && (STUB_MODULES.contains(&a.name) || STUB_MODULES.contains(&top) || STUB_PACKAGES.contains(&top))
            {
                let b = self.one(builtin(top));
                self.bind(top, b, false);
            }
        }
    }

    /// Run `from module import name`: bind attributes of a program module, or stubs.
    ///
    /// For a program module, the module and its parent packages run, and each name is bound to
    /// the module's attribute (`module_attr`); a name that is a submodule runs as well.
    /// `from module import *` binds every name in the module's `exports`. A relative import
    /// resolves against the current module's package. For other modules only the names that
    /// stubs provide are bound.
    pub fn s_import_from(&mut self, s: &ImportFrom) {
        let base = absolute_module(self.prog.module_of(&self.info.sym), s.level, s.module);
        if let Some(base) = base
            && let Some(info) = self.prog.module(base)
        {
            let exports = info.exports.clone();
            self.run_module(base);
            for a in &s.names {
                let star = &*a.name == "*";
                let names: Vec<Name> = if star {
                    let mut e: Vec<Name> = exports.iter().copied().collect();
                    e.sort();
                    e
                } else {
                    vec![a.name]
                };
                for n in names {
                    let sub = Name::new(&format!("{base}.{n}"));
                    if self.prog.module(sub).is_some() {
                        self.run_module(sub);
                    }
                    let v = self.module_attr(base, n);
                    self.bind(if star { n } else { a.asname.unwrap_or(n) }, v, false);
                }
            }
            return;
        }
        let Some(module) = s.module else {
            return;
        };
        if s.level > 0 || (!STUB_MODULES.contains(&module) && !STUB_PACKAGES.contains(&module)) {
            return;
        }
        for a in &s.names {
            if &*a.name == "*" {
                if &*module == "builtins" {
                    for &n in BUILTIN_NAMES.iter() {
                        let b = self.one(builtin(n));
                        self.bind(n, b, false);
                    }
                } else {
                    let prefix = format!("{module}.");
                    let full_names: Vec<Name> =
                        STUBS.names().filter(|f| f.starts_with(&prefix) && !f[prefix.len()..].contains('.')).collect();
                    for full in full_names {
                        let b = self.one(builtin(full));
                        self.bind(Name::new(&full[prefix.len()..]), b, false);
                    }
                }
                continue;
            }
            let full = Name::new(&format!("{module}.{}", a.name));
            if STUBS.contains(full) || STUB_MODULES.contains(&full) || STUB_PACKAGES.contains(&full) {
                let b = self.one(builtin(full));
                self.bind(a.asname.unwrap_or(a.name), b, false);
            } else if &*module == "builtins" && BUILTIN_NAMES.contains(&a.name) {
                let b = self.one(builtin(a.name));
                self.bind(a.asname.unwrap_or(a.name), b, false);
            }
        }
    }
}
