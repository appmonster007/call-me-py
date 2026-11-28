//! Call sites: the `Site` of a syntactic call, shared by the CFG builder and the interpreter.

use crate::model::{FuncId, Site, SiteKind};
use crate::syntax::Name;
use crate::syntax::ast::Expr;
use crate::syntax::unparse;

use super::program::Program;

/// Return `text` cut to `limit` characters, the last three replaced by "...", if longer.
pub fn cut(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_owned();
    }
    let mut out: String = text.chars().take(limit - 3).collect();
    out.push_str("...");
    out
}

/// Return the `Site` of a call node, or of a decorator when `kind` is `Decorator`.
///
/// The text is the call's source, prefixed with "@" for a decorator and cut to 60 characters.
/// The CFG builder and the interpreter both build their sites with this function, so the two
/// always agree.
pub fn make_site(caller: FuncId, node: &Expr, kind: SiteKind) -> Site {
    let mut text = unparse::expr_prefix(node, 61);
    if kind == SiteKind::Decorator {
        text.insert(0, '@');
    }
    let pos = node.pos();
    Site { caller, line: pos.line, col: pos.col, text: Name::new(&cut(&text, 60)), kind }
}

/// Return the call sites that function `fid` executes, sorted by position.
///
/// Decorator applications and calls in class bodies the function runs are included. They
/// are read from the function's CFG, so both always agree.
pub fn call_sites(prog: &Program, fid: FuncId) -> Vec<Site> {
    let mut sites: Vec<Site> = prog.cfg(fid).calls.keys().copied().collect();
    sites.sort_by_key(Site::position_key);
    sites
}
