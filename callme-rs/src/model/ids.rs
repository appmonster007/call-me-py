//! Identities: of functions and classes, of call sites, and module names from file paths.
//!
//! A `FuncId` or `ClassId` is an index into the program's function or class table; the data
//! that the Python version keeps in the id itself (qualname, file, line, column, module) is
//! the table entry's `SymId`, whose string forms are rendered once.

use std::fmt;

use crate::syntax::Name;

/// Identity of one function: a def, async def, lambda or a module body (`<module>`).
///
/// It indexes `Program.funcs`; ids are numbered in the order the Python version inserts
/// functions into its `funcs` dict.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct FuncId(pub u32);

/// Identity of one class definition; it indexes `Program.classes`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ClassId(pub u32);

impl FuncId {
    /// Return the id as a table index.
    pub fn index(self) -> usize {
        self.0 as usize
    }
}

impl ClassId {
    /// Return the id as a table index.
    pub fn index(self) -> usize {
        self.0 as usize
    }
}

/// How ids are printed: `file:line:col:qualname`, `module.qualname` or `qualname`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Style {
    /// `file:line:col:qualname`, always unique
    #[default]
    Full,
    /// `module.qualname`
    Module,
    /// `qualname` alone, which can collide
    Short,
}

impl Style {
    /// Parse "full", "module" or "short".
    pub fn parse(s: &str) -> Option<Style> {
        match s {
            "full" => Some(Style::Full),
            "module" => Some(Style::Module),
            "short" => Some(Style::Short),
            _ => None,
        }
    }

    /// Return the style's name.
    pub fn as_str(self) -> &'static str {
        match self {
            Style::Full => "full",
            Style::Module => "module",
            Style::Short => "short",
        }
    }
}

/// The identity data of a function or class (the Python version's `_SymId`).
///
/// Equality of ids is equality of (qualname, file, line, column); `module` is not part of it.
#[derive(Clone, Debug)]
pub struct SymId {
    /// dotted scope path of the definition, e.g. "Outer.make.inner"
    pub qualname: String,
    /// source path as given, e.g. "pkg/util.py"
    pub file: String,
    /// 1-based line of the definition
    pub lineno: u32,
    /// 0-based column of the definition
    pub col: u32,
    /// dotted name of the defining module, e.g. "pkg.util"
    pub module: String,
    /// the `full` form, `file:line:col:qualname`
    uid: String,
    /// the `module` form, `module.qualname`
    module_form: String,
}

impl SymId {
    /// Create an id; an empty `module` is derived from `file` (see `module_name`).
    pub fn new(qualname: String, file: String, lineno: u32, col: u32, module: &str) -> SymId {
        let module = if module.is_empty() { module_name(&file) } else { module.to_owned() };
        let uid = format!("{file}:{lineno}:{col}:{qualname}");
        let module_form = format!("{module}.{qualname}");
        SymId { qualname, file, lineno, col, module, uid, module_form }
    }

    /// Return the last part of the qualname, e.g. "inner" for "Outer.make.inner".
    pub fn name(&self) -> &str {
        self.qualname.rsplit('.').next().unwrap_or(&self.qualname)
    }

    /// Return the qualname without its last part, e.g. "Outer.make", or "" at top level.
    pub fn scope(&self) -> &str {
        self.qualname.rsplit_once('.').map_or("", |(s, _)| s)
    }

    /// Return the `full` form `file:line:col:qualname`, unique even when names repeat.
    pub fn uid(&self) -> &str {
        &self.uid
    }

    /// Return this id as a string in `style`.
    pub fn render(&self, style: Style) -> &str {
        match style {
            Style::Full => &self.uid,
            Style::Module => &self.module_form,
            Style::Short => &self.qualname,
        }
    }
}

impl fmt::Display for SymId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.uid)
    }
}

/// Return the dotted module name for a file path taken as is, e.g. "pkg.util" for "pkg/util.py".
///
/// A trailing `__init__` is dropped. If no name is left, the file path is returned as is.
/// Programs loaded from disk name their modules relative to the import root instead.
pub fn module_name(file: &str) -> String {
    let mut parts: Vec<&str> = file.split('/').filter(|p| !p.is_empty() && *p != "." && *p != "..").collect();
    if let Some(last) = parts.last_mut() {
        *last = strip_suffix(last);
    }
    if parts.last() == Some(&"__init__") {
        parts.pop();
    }
    let joined = parts.join(".");
    if joined.is_empty() { file.to_owned() } else { joined }
}

/// Return `name` without its last suffix (`PurePath.with_suffix("")` on the last part).
fn strip_suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(i) if i > 0 && i + 1 < name.len() => &name[..i],
        _ => name,
    }
}

/// The kind of a call site.
///
/// `Call` and `Decorator` are syntactic: they are CFG call points. The others are the
/// implicit dunder calls the interpreter makes, which have no CFG point.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum SiteKind {
    /// a call expression
    Call,
    /// a decorator application
    Decorator,
    /// property, descriptor, `__getattr__` and `__setattr__` calls
    Attr,
    /// operators, subscripts, f-strings
    Op,
    /// `__iter__` / `__next__` of for loops and comprehensions
    Iter,
    /// `__enter__` / `__exit__`
    With,
    /// `__init_subclass__` and metaclass hooks
    Class,
}

impl SiteKind {
    /// Return the kind's name as it appears in the output.
    pub fn as_str(self) -> &'static str {
        match self {
            SiteKind::Call => "call",
            SiteKind::Decorator => "decorator",
            SiteKind::Attr => "attr",
            SiteKind::Op => "op",
            SiteKind::Iter => "iter",
            SiteKind::With => "with",
            SiteKind::Class => "class",
        }
    }

    /// Parse a kind name.
    pub fn parse(s: &str) -> Option<SiteKind> {
        Some(match s {
            "call" => SiteKind::Call,
            "decorator" => SiteKind::Decorator,
            "attr" => SiteKind::Attr,
            "op" => SiteKind::Op,
            "iter" => SiteKind::Iter,
            "with" => SiteKind::With,
            "class" => SiteKind::Class,
            _ => return None,
        })
    }
}

/// A call site, identified by its caller and source position.
///
/// ```text
/// text  source text of the call, at most 60 characters; decorators start with "@"
/// kind  syntactic (`Call`, `Decorator`) or one of the implicit kinds
/// ```
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Site {
    /// the function whose code makes the call
    pub caller: FuncId,
    /// 1-based line
    pub line: u32,
    /// 0-based column in UTF-8 bytes
    pub col: u32,
    /// the call's source text
    pub text: Name,
    /// the kind of call
    pub kind: SiteKind,
}

impl Site {
    /// Return the sort key `(line, col, kind)` that `call_sites` orders a function's sites by.
    pub fn position_key(&self) -> (u32, u32, &'static str) {
        (self.line, self.col, self.kind.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::module_name;

    #[test]
    fn module_names_follow_the_path() {
        assert_eq!(module_name("pkg/util.py"), "pkg.util");
        assert_eq!(module_name("./pkg/__init__.py"), "pkg");
        assert_eq!(module_name("<string>"), "<string>");
        assert_eq!(module_name("../a/b.py"), "a.b");
        assert_eq!(module_name("__init__.py"), "__init__.py");
    }
}
