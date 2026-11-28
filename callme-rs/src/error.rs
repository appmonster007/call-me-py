//! The errors the library returns, in place of the Python version's exceptions.

use std::fmt;

use crate::syntax::SyntaxError;

/// An error from loading, analysing, or reading and writing files.
#[derive(Debug)]
pub enum Error {
    /// invalid source in a file (Python's `SyntaxError`)
    Syntax {
        /// the file name as given
        file: String,
        /// what failed and where
        err: SyntaxError,
    },
    /// a file could not be read or written (Python's `OSError`)
    Io {
        /// the path
        path: String,
        /// the system's message
        err: std::io::Error,
    },
    /// a request that does not fit the program or state (Python's `ValueError`)
    Value(String),
    /// a value of the wrong kind, such as another pass's state (Python's `TypeError`)
    Type(String),
    /// the fixpoint did not converge within the round limit (Python's `RuntimeError`)
    Runtime(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Syntax { file, err } => write!(f, "{file}: syntax error: {err}"),
            Error::Io { path, err } => write!(f, "{path}: {err}"),
            Error::Value(m) | Error::Type(m) | Error::Runtime(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for Error {}

/// The result type of the library.
pub type Result<T> = std::result::Result<T, Error>;
