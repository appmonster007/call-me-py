//! Interned identifiers: `Name` is a string stored once for the whole process.
//!
//! Every identifier of a parsed program, and every attribute or stub name the analysis makes,
//! is a `Name`. Two names are equal exactly when they are the same interned string, so
//! comparing and hashing a name costs one pointer operation, and copying it costs nothing.
//! Interned strings are never freed; the set of distinct names in a run is small.

use std::fmt;
use std::hash::{Hash, Hasher};
use std::ops::Deref;
use std::sync::{LazyLock, Mutex};

use rustc_hash::FxHashSet;

/// The process-wide table of interned strings.
static TABLE: LazyLock<Mutex<FxHashSet<&'static str>>> = LazyLock::new(Default::default);

/// An interned string: an identifier, a dotted module name, an attribute or a stub name.
///
/// Equality and hashing use the address of the interned string, which is unique per distinct
/// string; ordering compares the text, so sorted output does not depend on interning order.
#[derive(Clone, Copy)]
pub struct Name(&'static str);

impl Name {
    /// Return the interned name for `text`, adding it to the table on first use.
    pub fn new(text: &str) -> Name {
        let mut table = TABLE.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(&s) = table.get(text) {
            return Name(s);
        }
        let s: &'static str = Box::leak(text.to_owned().into_boxed_str());
        table.insert(s);
        Name(s)
    }

    /// Return the text of the name, which lives for the rest of the process.
    pub fn as_str(self) -> &'static str {
        self.0
    }
}

/// Return the interned `Name` of a string literal, interning it only on first use.
///
/// Each use site keeps its name in a static, so after the first call this costs one atomic
/// load; use it for the fixed names the analysis looks up, such as `name!("__init__")`.
#[macro_export]
macro_rules! name {
    ($s:literal) => {{
        static N: ::std::sync::OnceLock<$crate::syntax::Name> = ::std::sync::OnceLock::new();
        *N.get_or_init(|| $crate::syntax::Name::new($s))
    }};
}

impl PartialEq for Name {
    fn eq(&self, other: &Name) -> bool {
        std::ptr::eq(self.0, other.0)
    }
}

impl Eq for Name {}

impl Hash for Name {
    fn hash<H: Hasher>(&self, state: &mut H) {
        std::ptr::hash(self.0, state);
    }
}

impl PartialOrd for Name {
    fn partial_cmp(&self, other: &Name) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Name {
    fn cmp(&self, other: &Name) -> std::cmp::Ordering {
        if self == other { std::cmp::Ordering::Equal } else { self.0.cmp(other.0) }
    }
}

impl Deref for Name {
    type Target = str;

    fn deref(&self) -> &str {
        self.0
    }
}

impl fmt::Display for Name {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}

impl fmt::Debug for Name {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(self.0, f)
    }
}

impl From<&str> for Name {
    fn from(text: &str) -> Name {
        Name::new(text)
    }
}

#[cfg(test)]
mod tests {
    use super::Name;

    #[test]
    fn interning_gives_one_name_per_text() {
        let a = Name::new("alpha");
        let b = Name::new(&String::from("alpha"));
        assert_eq!(a, b);
        assert_ne!(a, Name::new("beta"));
        assert!(a < Name::new("beta"));
        assert_eq!(a.as_str(), "alpha");
    }
}
