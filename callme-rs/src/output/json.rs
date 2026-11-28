//! JSON values, written exactly as Python's `json.dumps` writes them, and read back.
//!
//! The writer matches `json.dumps(obj, indent=2)` (graphs) and `json.dump(obj)` (saved states)
//! byte for byte: `ensure_ascii` escapes every character outside `' '..='~'`, objects keep
//! their key order, an indented dump separates items with `",\n"`, and a compact one with
//! `", "` and `": "`.

use std::fmt::Write as _;

/// A JSON value; objects keep their keys in insertion order.
#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    /// `null`
    Null,
    /// `true` / `false`
    Bool(bool),
    /// an integer
    Int(i64),
    /// a string
    Str(String),
    /// an array
    List(Vec<Json>),
    /// an object, as (key, value) pairs in order
    Obj(Vec<(String, Json)>),
}

impl From<&str> for Json {
    fn from(s: &str) -> Json {
        Json::Str(s.to_owned())
    }
}

impl From<String> for Json {
    fn from(s: String) -> Json {
        Json::Str(s)
    }
}

impl From<u32> for Json {
    fn from(n: u32) -> Json {
        Json::Int(i64::from(n))
    }
}

impl From<usize> for Json {
    fn from(n: usize) -> Json {
        Json::Int(i64::try_from(n).unwrap_or(i64::MAX))
    }
}

impl From<bool> for Json {
    fn from(b: bool) -> Json {
        Json::Bool(b)
    }
}

impl<T: Into<Json>> From<Option<T>> for Json {
    fn from(v: Option<T>) -> Json {
        v.map_or(Json::Null, Into::into)
    }
}

/// Build a `Json::Obj` from `key => value` pairs, in order.
#[macro_export]
macro_rules! obj {
    ($($k:expr => $v:expr),* $(,)?) => {
        $crate::output::json::Json::Obj(vec![$(($k.to_string(), $crate::output::json::Json::from($v))),*])
    };
}

impl Json {
    /// Return the value of key `k` of an object.
    pub fn get(&self, k: &str) -> Option<&Json> {
        match self {
            Json::Obj(items) => items.iter().find(|(key, _)| key == k).map(|(_, v)| v),
            _ => None,
        }
    }

    /// Return the items of a list (an empty slice for other values).
    pub fn items(&self) -> &[Json] {
        match self {
            Json::List(v) => v,
            _ => &[],
        }
    }

    /// Return the string, if this is one.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(s) => Some(s),
            _ => None,
        }
    }

    /// Return the integer, if this is one.
    pub fn as_int(&self) -> Option<i64> {
        match self {
            Json::Int(i) => Some(*i),
            _ => None,
        }
    }

    /// Return the bool, if this is one.
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Json::Bool(b) => Some(*b),
            _ => None,
        }
    }

    /// Return `json.dumps(self, indent=indent)`.
    pub fn pretty(&self, indent: usize) -> String {
        let mut out = String::new();
        self.write_pretty(&mut out, indent, 0);
        out
    }

    /// Return `json.dumps(self)`: no newlines, separators `", "` and `": "`.
    pub fn compact(&self) -> String {
        let mut out = String::new();
        self.write_compact(&mut out);
        out
    }

    /// Append the indented form at nesting `level`.
    fn write_pretty(&self, out: &mut String, indent: usize, level: usize) {
        match self {
            Json::List(v) if !v.is_empty() => {
                out.push('[');
                for (i, x) in v.iter().enumerate() {
                    out.push_str(if i == 0 { "\n" } else { ",\n" });
                    push_indent(out, indent * (level + 1));
                    x.write_pretty(out, indent, level + 1);
                }
                out.push('\n');
                push_indent(out, indent * level);
                out.push(']');
            }
            Json::Obj(v) if !v.is_empty() => {
                out.push('{');
                for (i, (k, x)) in v.iter().enumerate() {
                    out.push_str(if i == 0 { "\n" } else { ",\n" });
                    push_indent(out, indent * (level + 1));
                    write_str(out, k);
                    out.push_str(": ");
                    x.write_pretty(out, indent, level + 1);
                }
                out.push('\n');
                push_indent(out, indent * level);
                out.push('}');
            }
            other => other.write_compact(out),
        }
    }

    /// Append the compact form.
    fn write_compact(&self, out: &mut String) {
        match self {
            Json::Null => out.push_str("null"),
            Json::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Json::Int(i) => {
                let _ = write!(out, "{i}");
            }
            Json::Str(s) => write_str(out, s),
            Json::List(v) => {
                out.push('[');
                for (i, x) in v.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    x.write_compact(out);
                }
                out.push(']');
            }
            Json::Obj(v) => {
                out.push('{');
                for (i, (k, x)) in v.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    write_str(out, k);
                    out.push_str(": ");
                    x.write_compact(out);
                }
                out.push('}');
            }
        }
    }
}

/// Append `n` spaces.
fn push_indent(out: &mut String, n: usize) {
    out.extend(std::iter::repeat_n(' ', n));
}

/// Append `s` as a JSON string with `ensure_ascii` escaping.
fn write_str(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            c => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
        }
    }
    out.push('"');
}

/// A JSON syntax error at a byte offset.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    /// what was wrong
    pub msg: String,
    /// where, as a byte offset
    pub at: usize,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} at byte {}", self.msg, self.at)
    }
}

/// Parse a JSON document.
pub fn parse(text: &str) -> Result<Json, ParseError> {
    let mut p = Parser { s: text.as_bytes(), i: 0 };
    let v = p.value()?;
    p.ws();
    if p.i != p.s.len() {
        return Err(p.err("trailing data"));
    }
    Ok(v)
}

/// A recursive-descent JSON parser over bytes.
struct Parser<'s> {
    /// the document
    s: &'s [u8],
    /// the next byte
    i: usize,
}

impl Parser<'_> {
    /// Return an error at the current position.
    fn err(&self, msg: &str) -> ParseError {
        ParseError { msg: msg.into(), at: self.i }
    }

    /// Skip whitespace.
    fn ws(&mut self) {
        while self.i < self.s.len() && matches!(self.s[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }

    /// Consume `lit` or fail.
    fn lit(&mut self, lit: &str, v: Json) -> Result<Json, ParseError> {
        if self.s[self.i..].starts_with(lit.as_bytes()) {
            self.i += lit.len();
            Ok(v)
        } else {
            Err(self.err("invalid literal"))
        }
    }

    /// Parse one value.
    fn value(&mut self) -> Result<Json, ParseError> {
        self.ws();
        match self.s.get(self.i) {
            None => Err(self.err("unexpected end")),
            Some(b'n') => self.lit("null", Json::Null),
            Some(b't') => self.lit("true", Json::Bool(true)),
            Some(b'f') => self.lit("false", Json::Bool(false)),
            Some(b'"') => Ok(Json::Str(self.string()?)),
            Some(b'[') => {
                self.i += 1;
                let mut v = Vec::new();
                self.ws();
                if self.s.get(self.i) == Some(&b']') {
                    self.i += 1;
                    return Ok(Json::List(v));
                }
                loop {
                    v.push(self.value()?);
                    self.ws();
                    match self.s.get(self.i) {
                        Some(b',') => self.i += 1,
                        Some(b']') => {
                            self.i += 1;
                            return Ok(Json::List(v));
                        }
                        _ => return Err(self.err("expected , or ]")),
                    }
                }
            }
            Some(b'{') => {
                self.i += 1;
                let mut v = Vec::new();
                self.ws();
                if self.s.get(self.i) == Some(&b'}') {
                    self.i += 1;
                    return Ok(Json::Obj(v));
                }
                loop {
                    self.ws();
                    if self.s.get(self.i) != Some(&b'"') {
                        return Err(self.err("expected a key"));
                    }
                    let k = self.string()?;
                    self.ws();
                    if self.s.get(self.i) != Some(&b':') {
                        return Err(self.err("expected :"));
                    }
                    self.i += 1;
                    let x = self.value()?;
                    v.push((k, x));
                    self.ws();
                    match self.s.get(self.i) {
                        Some(b',') => self.i += 1,
                        Some(b'}') => {
                            self.i += 1;
                            return Ok(Json::Obj(v));
                        }
                        _ => return Err(self.err("expected , or }")),
                    }
                }
            }
            Some(_) => self.number(),
        }
    }

    /// Parse an integer (the documents read here hold no floats).
    fn number(&mut self) -> Result<Json, ParseError> {
        let start = self.i;
        if self.s.get(self.i) == Some(&b'-') {
            self.i += 1;
        }
        while self.i < self.s.len() && self.s[self.i].is_ascii_digit() {
            self.i += 1;
        }
        let text = std::str::from_utf8(&self.s[start..self.i]).map_err(|_| self.err("invalid number"))?;
        text.parse().map(Json::Int).map_err(|_| self.err("invalid number"))
    }

    /// Parse four hex digits.
    fn hex4(&mut self) -> Result<u32, ParseError> {
        let h = self.s.get(self.i..self.i + 4).ok_or_else(|| self.err("short \\u escape"))?;
        let v = u32::from_str_radix(std::str::from_utf8(h).unwrap_or("x"), 16)
            .map_err(|_| self.err("invalid \\u escape"))?;
        self.i += 4;
        Ok(v)
    }

    /// Parse a string, starting at its opening quote.
    fn string(&mut self) -> Result<String, ParseError> {
        self.i += 1;
        let mut out: Vec<u8> = Vec::new();
        loop {
            let Some(&b) = self.s.get(self.i) else {
                return Err(self.err("unterminated string"));
            };
            self.i += 1;
            match b {
                b'"' => break,
                b'\\' => {
                    let Some(&e) = self.s.get(self.i) else {
                        return Err(self.err("unterminated escape"));
                    };
                    self.i += 1;
                    let c = match e {
                        b'"' => '"',
                        b'\\' => '\\',
                        b'/' => '/',
                        b'b' => '\u{8}',
                        b'f' => '\u{c}',
                        b'n' => '\n',
                        b'r' => '\r',
                        b't' => '\t',
                        b'u' => {
                            let mut v = self.hex4()?;
                            if (0xD800..0xDC00).contains(&v) && self.s[self.i..].starts_with(b"\\u") {
                                self.i += 2;
                                let lo = self.hex4()?;
                                v = 0x10000 + ((v - 0xD800) << 10) + (lo.wrapping_sub(0xDC00) & 0x3FF);
                            }
                            char::from_u32(v).unwrap_or('\u{fffd}')
                        }
                        _ => return Err(self.err("invalid escape")),
                    };
                    let mut buf = [0u8; 4];
                    out.extend_from_slice(c.encode_utf8(&mut buf).as_bytes());
                }
                b => out.push(b),
            }
        }
        String::from_utf8(out).map_err(|_| self.err("invalid UTF-8"))
    }
}

#[cfg(test)]
mod tests {
    use super::{Json, parse};

    #[test]
    fn dumps_like_python() {
        let v = Json::Obj(vec![
            ("a".into(), Json::List(vec![Json::Int(1), Json::Str("\u{e9}\"x\"".into())])),
            ("b".into(), Json::Obj(vec![])),
            ("c".into(), Json::List(vec![])),
            ("d".into(), Json::Null),
        ]);
        assert_eq!(
            v.pretty(2),
            "{\n  \"a\": [\n    1,\n    \"\\u00e9\\\"x\\\"\"\n  ],\n  \"b\": {},\n  \"c\": [],\n  \"d\": null\n}"
        );
        assert_eq!(v.compact(), "{\"a\": [1, \"\\u00e9\\\"x\\\"\"], \"b\": {}, \"c\": [], \"d\": null}");
        assert_eq!(Json::Str("\u{1f600}\u{7f}".into()).compact(), "\"\\ud83d\\ude00\\u007f\"");
    }

    #[test]
    fn parses_what_it_writes() {
        let v = Json::Obj(vec![(
            "k".into(),
            Json::List(vec![Json::Int(-3), Json::Bool(true), Json::Str("\u{1f600}\n".into())]),
        )]);
        assert_eq!(parse(&v.compact()).unwrap(), v);
        assert_eq!(parse(&v.pretty(2)).unwrap(), v);
    }
}
