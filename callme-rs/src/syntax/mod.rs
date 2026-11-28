//! The syntax layer: what CPython's `ast` module gives the Python version.
//!
//! `parse_module` turns source text into a CPython-shaped tree (`ast`), with CPython's node
//! positions; `walk` traverses it in CPython's order and `unparse` turns it back into
//! CPython's source text. Nothing outside this package sees Ruff's parser.
//!
//! This package depends on nothing else in the crate, and everything else may use it.

pub mod ast;
pub mod lines;
pub mod lower;
pub mod name;
pub mod tables;
pub mod unparse;
pub mod walk;

use std::fmt;

pub use ast::{Name, Pos};
pub use lower::parse_module;

/// A syntax error, with the position where parsing failed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SyntaxError {
    /// the parser's message
    pub msg: String,
    /// where the error was found
    pub pos: Pos,
}

impl fmt::Display for SyntaxError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} (line {}, column {})", self.msg, self.pos.line, self.pos.col + 1)
    }
}

impl std::error::Error for SyntaxError {}
