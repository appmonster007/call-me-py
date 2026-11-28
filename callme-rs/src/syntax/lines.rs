//! Source text helpers: newline normalisation and byte offsets to CPython positions.

use super::ast::Pos;

/// Return `text` with `\r\n` and lone `\r` turned into `\n`.
///
/// This is what Python's `Path.read_text` (universal newlines) does, so a file loaded here
/// has the same text, the same positions and the same fingerprint as in the Python version.
pub fn normalize_newlines(text: &str) -> String {
    if !text.contains('\r') {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\r' {
            if chars.peek() == Some(&'\n') {
                chars.next();
            }
            out.push('\n');
        } else {
            out.push(c);
        }
    }
    out
}

/// The start offset of every line of a source text, to turn byte offsets into positions.
///
/// A line ends after `\n`, after `\r\n`, or after a `\r` that no `\n` follows.
pub struct LineIndex {
    /// byte offset where each line starts; the first is 0
    starts: Vec<u32>,
}

impl LineIndex {
    /// Index the lines of `text`.
    pub fn new(text: &str) -> LineIndex {
        let bytes = text.as_bytes();
        let mut starts = vec![0u32];
        let mut i = 0;
        while i < bytes.len() {
            match bytes[i] {
                b'\n' => starts.push(offset(i + 1)),
                b'\r' if bytes.get(i + 1) != Some(&b'\n') => starts.push(offset(i + 1)),
                _ => {}
            }
            i += 1;
        }
        LineIndex { starts }
    }

    /// Return the CPython position (1-based line, byte column) of byte offset `at`.
    pub fn pos(&self, at: u32) -> Pos {
        let line = self.starts.partition_point(|&s| s <= at);
        Pos { line: offset(line), col: at - self.starts[line - 1] }
    }
}

/// Convert a byte count to `u32`; sources over 4 GiB are not supported.
fn offset(n: usize) -> u32 {
    u32::try_from(n).expect("source files are smaller than 4 GiB")
}

#[cfg(test)]
mod tests {
    use super::{LineIndex, normalize_newlines};
    use crate::syntax::ast::Pos;

    #[test]
    fn positions_count_bytes_from_the_line_start() {
        let idx = LineIndex::new("ab\nc\u{e9}d\r\ne");
        assert_eq!(idx.pos(0), Pos { line: 1, col: 0 });
        assert_eq!(idx.pos(3), Pos { line: 2, col: 0 });
        assert_eq!(idx.pos(6), Pos { line: 2, col: 3 });
        assert_eq!(idx.pos(9), Pos { line: 3, col: 0 });
    }

    #[test]
    fn newlines_are_normalised_like_read_text() {
        assert_eq!(normalize_newlines("a\r\nb\rc\n"), "a\nb\nc\n");
    }
}
