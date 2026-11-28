//! Source text from syntax: a port of CPython 3.13's `ast.unparse` (`ast._Unparser`).
//!
//! Call sites and CFG points are labelled with unparsed source, so the output must match
//! CPython's byte for byte: the same parenthesisation by precedence, the same choice of
//! quotes, the same escapes (`str.isprintable` comes from the generated `tables`), and the
//! same `repr` of numbers. The methods keep the names of the `_Unparser` methods they port.
//!
//! The labels need only a prefix: a site keeps 60 characters and a CFG point the first line.
//! `Unparser` can therefore stop writing once it has `max_chars` characters, or at the first
//! newline, which keeps unparsing a long `def` cheap. Nested pieces whose quoting depends on
//! their whole text (f-strings, lambda parameters) are always rendered in full.
//!
//! `mod.rs` holds the API and the writer, `stmt` the statements and parameter lists, `expr`
//! the expressions, f-strings and patterns, and `literals` the `repr` of constants.

mod expr;
mod literals;
mod stmt;

use literals::str_literal_helper;
pub use literals::{float_repr, is_printable, str_repr};

use super::ast::*;

/// `_Precedence`: binding strength of an expression's context, weakest first.
mod prec {
    /// `<target> := <expr>`
    pub const NAMED_EXPR: u8 = 1;
    /// `a, b`
    pub const TUPLE: u8 = 2;
    /// `yield`, `yield from`
    pub const YIELD: u8 = 3;
    /// `if`-`else`, `lambda`
    pub const TEST: u8 = 4;
    /// `or`
    pub const OR: u8 = 5;
    /// `and`
    pub const AND: u8 = 6;
    /// `not`
    pub const NOT: u8 = 7;
    /// comparisons
    pub const CMP: u8 = 8;
    /// `|` (CPython's `EXPR` and `BOR`)
    pub const EXPR: u8 = 9;
    /// `|`
    pub const BOR: u8 = 9;
    /// `^`
    pub const BXOR: u8 = 10;
    /// `&`
    pub const BAND: u8 = 11;
    /// `<<`, `>>`
    pub const SHIFT: u8 = 12;
    /// `+`, `-`
    pub const ARITH: u8 = 13;
    /// `*`, `@`, `/`, `%`, `//`
    pub const TERM: u8 = 14;
    /// unary `+`, `-`, `~`
    pub const FACTOR: u8 = 15;
    /// `**`
    pub const POWER: u8 = 16;
    /// `await`
    pub const AWAIT: u8 = 17;
    /// atoms
    pub const ATOM: u8 = 18;

    /// Return the next stronger precedence (`_Precedence.next`).
    pub fn next(p: u8) -> u8 {
        if p < ATOM { p + 1 } else { ATOM }
    }
}

/// `_SINGLE_QUOTES + _MULTI_QUOTES`, in CPython's order.
const ALL_QUOTES: [&str; 4] = ["'", "\"", "\"\"\"", "'''"];
/// `_MULTI_QUOTES`.
const MULTI_QUOTES: [&str; 2] = ["\"\"\"", "'''"];
/// `_INFSTR`: what an infinite float is written as.
const INFSTR: &str = "1e309";

/// Return `ast.unparse(e)`.
pub fn expr(e: &Expr) -> String {
    let mut u = Unparser::new(None, false);
    u.traverse(e, prec::TEST);
    u.out
}

/// Return at least the first `max_chars` characters of `ast.unparse(e)` (all of it if shorter).
pub fn expr_prefix(e: &Expr, max_chars: usize) -> String {
    let mut u = Unparser::new(Some(max_chars), false);
    u.traverse(e, prec::TEST);
    u.out
}

/// Return at least the first `max_chars` characters of `ast.unparse(p)` for a `match` pattern.
pub fn pattern_prefix(p: &Pattern, max_chars: usize) -> String {
    let mut u = Unparser::new(Some(max_chars), false);
    u.pattern(p, prec::TEST);
    u.out
}

/// Return `ast.unparse(s)`.
pub fn stmt(s: &Stmt) -> String {
    let mut u = Unparser::new(None, false);
    u.stmt(s);
    u.out
}

/// Return the first line of `ast.unparse(s)`, or at least its first `max_chars` characters.
pub fn stmt_first_line(s: &Stmt, max_chars: Option<usize>) -> String {
    let mut u = Unparser::new(max_chars, true);
    u.stmt(s);
    let mut out = u.out;
    if let Some(i) = out.find('\n') {
        out.truncate(i);
    }
    out
}

/// The unparser state: output, indentation, and where to stop.
struct Unparser {
    /// the text written so far
    out: String,
    /// True once anything was written, even an empty string (`_source` is not empty)
    wrote: bool,
    /// current block depth
    indent: usize,
    /// inside a `try` with `except*`
    in_try_star: bool,
    /// stop after this many characters
    max_chars: Option<usize>,
    /// stop at the first newline
    first_line: bool,
    /// characters in `out`
    chars: usize,
    /// True once the requested prefix is complete; later writes are dropped
    done: bool,
}

impl Unparser {
    /// Create an unparser that stops after `max_chars` characters or, with `first_line`, at
    /// the first newline.
    pub(super) fn new(max_chars: Option<usize>, first_line: bool) -> Unparser {
        Unparser {
            out: String::new(),
            wrote: false,
            indent: 0,
            in_try_star: false,
            max_chars,
            first_line,
            chars: 0,
            done: false,
        }
    }

    /// `write`: append text, unless the requested prefix is already complete.
    pub(super) fn write(&mut self, s: &str) {
        self.wrote = true;
        if self.done {
            return;
        }
        self.out.push_str(s);
        self.chars += s.chars().count();
        if (self.first_line && s.contains('\n')) || self.max_chars.is_some_and(|m| self.chars > m) {
            self.done = true;
        }
    }

    /// `maybe_newline`: a newline, unless nothing has been written yet.
    pub(super) fn maybe_newline(&mut self) {
        if self.wrote {
            self.write("\n");
        }
    }

    /// `fill`: a newline, the indentation, then `text`.
    pub(super) fn fill(&mut self, text: &str) {
        self.maybe_newline();
        let pad = "    ".repeat(self.indent);
        self.write(&pad);
        self.write(text);
    }

    /// `block`: a colon, then `body` one level deeper.
    pub(super) fn block(&mut self, body: impl FnOnce(&mut Unparser)) {
        self.write(":");
        self.indent += 1;
        body(self);
        self.indent -= 1;
    }

    /// Run `f` between `(` and `)` if `cond` holds (`delimit_if`).
    pub(super) fn parens_if(&mut self, cond: bool, f: impl FnOnce(&mut Unparser)) {
        if cond {
            self.write("(");
        }
        f(self);
        if cond {
            self.write(")");
        }
    }

    /// Write the statements of a block.
    pub(super) fn body(&mut self, body: &[Stmt]) {
        for s in body {
            if self.done {
                return;
            }
            self.stmt(s);
        }
    }

    /// `_write_docstring_and_traverse_body`: a leading docstring is written as such.
    pub(super) fn docstring_and_body(&mut self, body: &[Stmt]) {
        if let Some(Stmt::Expr(ExprStmt { value: Expr::Constant(c), .. })) = body.first()
            && let Const::Str(s) = &c.value
        {
            self.fill("");
            if c.kind_u {
                self.write("u");
            }
            let (text, quotes) = str_literal_helper(s, &MULTI_QUOTES, false);
            self.write(quotes[0]);
            self.write(&text);
            self.write(quotes[0]);
            self.body(&body[1..]);
        } else {
            self.body(body);
        }
    }

    /// Write a comma-separated list, with `f` writing each item (`interleave`).
    pub(super) fn interleave<T>(&mut self, items: &[T], sep: &str, mut f: impl FnMut(&mut Unparser, &T)) {
        for (i, x) in items.iter().enumerate() {
            if i > 0 {
                self.write(sep);
            }
            f(self, x);
        }
    }

    /// `items_view`: like `interleave` with ", ", but a single item gets a trailing comma.
    pub(super) fn items_view(&mut self, items: &[Expr]) {
        if let [one] = items {
            self.traverse(one, prec::TEST);
            self.write(",");
        } else {
            self.interleave(items, ", ", |u, e| u.traverse(e, prec::TEST));
        }
    }
}
