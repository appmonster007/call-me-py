//! Loading files: find the entry modules and every project module they import, transitively.
//!
//! Module names are resolved the way Python's path finder does: regular packages, plain
//! modules and namespace packages, searched along the search path. Paths are kept as the
//! strings `pathlib` would print, so that file names in ids match the Python version.

use std::collections::VecDeque;
use std::path::Path;
use std::sync::Arc;

use rustc_hash::FxHashMap;

use crate::error::{Error, Result};
use crate::syntax::Name;
use crate::syntax::ast::{Expr, Module, Stmt};
use crate::syntax::lines::normalize_newlines;
use crate::syntax::tables::{BUILTIN_MODULE_NAMES, STDLIB_MODULE_NAMES};
use crate::syntax::walk::{NodeRef, walk};

use super::builder::{ProgramBuilder, absolute_module, module_all};
use super::program::Program;

/// Return `p` normalised as `str(pathlib.PurePosixPath(p))` prints it.
///
/// Repeated slashes and `.` components are dropped; `..` is kept; "" becomes ".".
pub fn norm_path(p: &str) -> String {
    let absolute = p.starts_with('/');
    let parts: Vec<&str> = p.split('/').filter(|c| !c.is_empty() && *c != ".").collect();
    let body = parts.join("/");
    match (absolute, body.is_empty()) {
        (true, _) => format!("/{body}"),
        (false, true) => ".".into(),
        (false, false) => body,
    }
}

/// Return `dir / name` as `pathlib` prints it.
fn join(dir: &str, name: &str) -> String {
    norm_path(&format!("{dir}/{name}"))
}

/// Return `os.path.normpath(p)`: also folds `x/..` pairs.
fn normpath(p: &str) -> String {
    let absolute = p.starts_with('/');
    let mut out: Vec<&str> = Vec::new();
    for c in p.split('/') {
        match c {
            "" | "." => {}
            ".." if out.last().is_some_and(|l| *l != "..") => {
                out.pop();
            }
            ".." if absolute => {}
            c => out.push(c),
        }
    }
    let body = out.join("/");
    match (absolute, body.is_empty()) {
        (true, _) => format!("/{body}"),
        (false, true) => ".".into(),
        (false, false) => body,
    }
}

/// Return the parent of a normalised path (`PurePath.parent`).
fn parent(p: &str) -> String {
    match p.rsplit_once('/') {
        Some(("", _)) => "/".into(),
        Some((d, _)) => d.to_owned(),
        None => ".".into(),
    }
}

/// Return the last component of a path (`PurePath.name`).
fn file_name(p: &str) -> &str {
    p.rsplit('/').next().unwrap_or(p)
}

/// Return the last component without its suffix (`PurePath.stem`).
fn stem(p: &str) -> &str {
    let name = file_name(p);
    match name.rfind('.') {
        Some(i) if i > 0 && i + 1 < name.len() => &name[..i],
        _ => name,
    }
}

/// Return True if `p` is a regular file.
fn is_file(p: &str) -> bool {
    Path::new(p).is_file()
}

/// Return True if `p` is a directory.
fn is_dir(p: &str) -> bool {
    Path::new(p).is_dir()
}

/// Return the absolute, symlink-free form of `p` (`Path.resolve`), or `p` made absolute.
fn resolve(p: &str) -> String {
    match std::fs::canonicalize(p) {
        Ok(r) => r.to_string_lossy().into_owned(),
        Err(_) => std::env::current_dir().map_or_else(|_| p.to_owned(), |d| normpath(&format!("{}/{p}", d.display()))),
    }
}

/// Return True if `s` is a Python identifier (`str.isidentifier`).
fn is_identifier(s: &str) -> bool {
    let mut chars = s.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    (first == '_' || first.is_alphabetic()) && chars.all(|c| c == '_' || c.is_alphanumeric())
}

/// Where a module was found on the search path.
///
/// ```text
/// file  its source file, or None for a namespace package
/// dirs  directories its submodules are searched in; empty for a module that is no package
/// ```
#[derive(Clone, Debug)]
struct Found {
    /// the source file
    file: Option<String>,
    /// directories of its submodules
    dirs: Vec<String>,
}

/// Return the import root and the dotted module name of the entry file at `path`.
///
/// The name is the file's stem, prefixed by every enclosing directory that is a package
/// (has an `__init__.py`), as pyright and mypy name a file; the first directory that is not
/// a package is the root. `main.py` is `main`, and `app/core.py` in package `app` is
/// `app.core` with root `.`.
pub fn entry_module(path: &str) -> (String, String) {
    let mut parts: Vec<String> = if stem(path) == "__init__" { Vec::new() } else { vec![stem(path).to_owned()] };
    let mut d = parent(path);
    while is_file(&join(&d, "__init__.py")) {
        let resolved = resolve(&d);
        let name = file_name(&resolved).to_owned();
        if name.is_empty() {
            break; // the file system root
        }
        parts.insert(0, name);
        d = normpath(&format!("{d}/.."));
    }
    (d, parts.join("."))
}

/// Return the module named by `__import__("m")` or `importlib.import_module("m")`, or None.
///
/// Only a string literal as the first argument counts, and relative names are skipped.
fn dynamic_import_name(e: &Expr) -> Option<Name> {
    let Expr::Call(c) = e else {
        return None;
    };
    let name = match &*c.func {
        Expr::Name(n) => n.id,
        Expr::Attribute(a) => a.attr,
        _ => return None,
    };
    if !matches!(&*name, "__import__" | "import_module") || c.args.is_empty() {
        return None;
    }
    let s = c.args[0].as_str_constant()?;
    (!s.starts_with('.')).then(|| Name::new(s))
}

/// Loads entry files and, transitively, every project module they import.
///
/// Modules are searched in the entry roots, then in the extra search path, the way Python's
/// path finder searches `sys.path`: in each directory a package (`name/__init__.py`) wins over
/// `name.py`, and a directory without `__init__.py` is a namespace package only if no regular
/// module is found anywhere. Submodules are searched in their package's directories.
struct Loader {
    /// the program being built
    b: ProgramBuilder,
    /// (path, root, module name) of each entry file
    entries: Vec<(String, String, String)>,
    /// directories searched for top-level modules, without duplicates
    roots: Vec<String>,
    /// lookup results by module name
    found: FxHashMap<Name, Option<Found>>,
    /// module name by resolved source path
    by_file: FxHashMap<String, Name>,
    /// loaded modules whose imports are not followed yet
    todo: VecDeque<usize>,
}

impl Loader {
    /// Prepare to load `entries`, searching their roots and then `search_path`.
    fn new(entries: &[String], search_path: &[String]) -> Loader {
        let entries: Vec<(String, String, String)> = entries
            .iter()
            .map(|p| {
                let p = norm_path(p);
                let (root, name) = entry_module(&p);
                (p, root, name)
            })
            .collect();
        let mut roots: Vec<String> = Vec::new();
        for d in entries.iter().map(|(_, r, _)| r.clone()).chain(search_path.iter().map(|d| norm_path(d))) {
            if !roots.contains(&d) {
                roots.push(d);
            }
        }
        Loader {
            b: ProgramBuilder::new(),
            entries,
            roots,
            found: FxHashMap::default(),
            by_file: FxHashMap::default(),
            todo: VecDeque::new(),
        }
    }

    /// Return where module `name` is, or None when it is not a project module (cached).
    ///
    /// Names of modules built into the interpreter are never project modules, and a top-level
    /// namespace directory never shadows a standard library module, which Python would find
    /// later on `sys.path`.
    fn find(&mut self, name: Name) -> Option<Found> {
        if let Some(f) = self.found.get(&name) {
            return f.clone();
        }
        let (parent, last) = name.rsplit_once('.').unwrap_or(("", &name));
        let dirs: Vec<String> = if !is_identifier(last) {
            Vec::new()
        } else if !parent.is_empty() {
            self.find(Name::new(parent)).map(|f| f.dirs).unwrap_or_default()
        } else if !BUILTIN_MODULE_NAMES.contains(&&*name) {
            self.roots.clone()
        } else {
            Vec::new()
        };
        let mut result: Option<Found> = None;
        let mut namespace: Vec<String> = Vec::new();
        for d in &dirs {
            let pkg = join(d, last);
            let init = join(&pkg, "__init__.py");
            if is_file(&init) {
                result = Some(Found { file: Some(init), dirs: vec![pkg] });
                break;
            }
            let module = join(d, &format!("{last}.py"));
            if is_file(&module) {
                result = Some(Found { file: Some(module), dirs: Vec::new() });
                break;
            }
            if is_dir(&pkg) {
                namespace.push(pkg);
            }
        }
        if result.is_none() && !namespace.is_empty() && (!parent.is_empty() || !STDLIB_MODULE_NAMES.contains(&&*name)) {
            result = Some(Found { file: None, dirs: namespace });
        }
        self.found.insert(name, result.clone());
        result
    }

    /// Load module `name` and its parent packages if they are project modules.
    ///
    /// Return True if `name` is a module of the program. A newly loaded file is queued, so
    /// that its own imports are loaded too.
    fn need(&mut self, name: Name) -> Result<bool> {
        if self.b.modules.contains_key(&name) {
            return Ok(true);
        }
        let Some(loc) = self.find(name) else {
            return Ok(false);
        };
        if let Some((parent, _)) = name.rsplit_once('.') {
            self.need(Name::new(parent))?;
        }
        let Some(file) = loc.file else {
            self.b.add_namespace(name);
            return Ok(true);
        };
        let key = resolve(&file);
        if let Some(other) = self.by_file.get(&key) {
            // the same file under a second name: one module
            let i = self.b.modules[other];
            self.b.modules.insert(name, i);
            return Ok(true);
        }
        let package = file_name(&file) == "__init__.py";
        self.load(&file, &file, name, package)?;
        Ok(true)
    }

    /// Parse the file at `path` as module `name`, queue it for its imports, and return its index.
    ///
    /// Returns `Error::Value` if another file already is module `name`, as two entry files with
    /// the same name in different directories would be.
    fn load(&mut self, path: &str, filename: &str, name: Name, package: bool) -> Result<usize> {
        if let Some(&other) = self.b.modules.get(&name) {
            let other_file = self.b.module_info(other).file.clone().unwrap_or_default();
            return Err(Error::Value(format!("{filename} and {other_file} would both be module '{name}'")));
        }
        self.by_file.insert(resolve(path), name);
        let text = std::fs::read_to_string(path).map_err(|err| Error::Io { path: path.to_owned(), err })?;
        let i = self.b.add_module(&normalize_newlines(&text), filename, name, package)?;
        self.todo.push_back(i);
        Ok(i)
    }

    /// Load every module that module `i` may import, in source order.
    ///
    /// Every scope counts: imports inside functions, `try` blocks and `if TYPE_CHECKING:`.
    /// `from X import n` also needs `X.n`, which may be a submodule, and a star import of a
    /// package needs the submodules its `__all__` lists. Dynamic imports count when they name
    /// the module with a string literal.
    fn load_imports(&mut self, i: usize) -> Result<()> {
        let info = self.b.module_info(i).clone();
        let Some(tree): Option<Arc<Module>> = self.b.tree(&info) else {
            return Ok(());
        };
        for n in walk(NodeRef::Module(&tree)) {
            match n {
                NodeRef::Stmt(Stmt::Import(x)) => {
                    for a in &x.names {
                        self.need(a.name)?;
                    }
                }
                NodeRef::Stmt(Stmt::ImportFrom(x)) => {
                    let Some(base) = absolute_module(&info, x.level, x.module) else {
                        continue;
                    };
                    self.need(base)?;
                    for a in &x.names {
                        if &*a.name != "*" {
                            self.need(Name::new(&format!("{base}.{}", a.name)))?;
                        } else if self.need(base)?
                            && let Some(sub) =
                                self.b.modules.get(&base).and_then(|&m| self.b.tree(self.b.module_info(m)))
                        {
                            let mut names: Vec<Name> =
                                module_all(&sub.body).map(|s| s.into_iter().collect()).unwrap_or_default();
                            names.sort();
                            for x in names {
                                self.need(Name::new(&format!("{base}.{x}")))?;
                            }
                        }
                    }
                }
                NodeRef::Expr(e) => {
                    if let Some(dyn_name) = dynamic_import_name(e) {
                        self.need(dyn_name)?;
                    }
                }
                _ => {}
            }
        }
        Ok(())
    }

    /// Load the entries and every module they import, and return the `Program`.
    fn run(mut self) -> Result<Program> {
        let mut entries: Vec<usize> = Vec::new();
        for (path, _, name) in self.entries.clone() {
            let key = resolve(&path);
            if let Some(n) = self.by_file.get(&key) {
                entries.push(self.b.modules[n]);
                continue;
            }
            let name = Name::new(&name);
            if let Some((parent, _)) = name.rsplit_once('.') {
                self.need(Name::new(parent))?;
            }
            let package = file_name(&path) == "__init__.py";
            entries.push(self.load(&path, &path, name, package)?);
        }
        while let Some(i) = self.todo.pop_front() {
            self.load_imports(i)?;
        }
        Ok(self.b.finish(&entries))
    }
}

/// Read the UTF-8 Python entry files at `paths` and every project module they import.
///
/// Return one `Program` whose roots are the entries' module bodies. A module is named by its
/// dotted path relative to its import root (`entry_module`), and each file keeps the path it
/// was found under. Imports that name no file under the search path, such as the standard
/// library, stay external. Returns `Error::Syntax` for invalid source in any loaded file, and
/// `Error::Value` when `paths` is empty.
///
/// ```text
/// search_path  more directories to search for imported modules, after the entries' own
///              roots, as `PYTHONPATH` adds to `sys.path`
/// ```
pub fn parse_files(paths: &[String], search_path: &[String]) -> Result<Program> {
    if paths.is_empty() {
        return Err(Error::Value("parse_files needs at least one entry file".into()));
    }
    Loader::new(paths, search_path).run()
}

/// Read the file at `path` and every project module it imports; see `parse_files`.
pub fn parse_file(path: &str, search_path: &[String]) -> Result<Program> {
    parse_files(&[path.to_owned()], search_path)
}

#[cfg(test)]
mod tests {
    use super::{norm_path, normpath, parent, stem};

    #[test]
    fn paths_print_like_pathlib() {
        assert_eq!(norm_path("./a//b/./c.py"), "a/b/c.py");
        assert_eq!(norm_path(""), ".");
        assert_eq!(norm_path("../x"), "../x");
        assert_eq!(normpath("a/b/.."), "a");
        assert_eq!(normpath("./.."), "..");
        assert_eq!(normpath("../.."), "../..");
        assert_eq!(parent("main.py"), ".");
        assert_eq!(parent("a/b/main.py"), "a/b");
        assert_eq!(stem("a/__init__.py"), "__init__");
    }
}
