//! The `repr` of constants as `ast.unparse` writes them: strings (with CPython's choice of
//! quotes and escapes), bytes, integers, floats and imaginary numbers.

use super::{INFSTR, MULTI_QUOTES};
use crate::syntax::ast::Const;
use crate::syntax::tables::NON_PRINTABLE;

/// Return whether `c` is printable, as `str.isprintable` decides.
pub fn is_printable(c: char) -> bool {
    let c = u32::from(c);
    let i = NON_PRINTABLE.partition_point(|&(_, hi)| hi < c);
    !(i < NON_PRINTABLE.len() && NON_PRINTABLE[i].0 <= c)
}

/// Push the `\x`, `\u` or `\U` escape of `c` (as `unicode_escape` and `repr` write it).
pub(super) fn push_hex_escape(out: &mut String, c: char) {
    let v = u32::from(c);
    if v < 0x100 {
        out.push_str(&format!("\\x{v:02x}"));
    } else if v < 0x10000 {
        out.push_str(&format!("\\u{v:04x}"));
    } else {
        out.push_str(&format!("\\U{v:08x}"));
    }
}

/// Return `c.encode("unicode_escape").decode()` for a backslash or a non-printable character.
pub(super) fn unicode_escape(c: char, out: &mut String) {
    match c {
        '\\' => out.push_str("\\\\"),
        '\t' => out.push_str("\\t"),
        '\n' => out.push_str("\\n"),
        '\r' => out.push_str("\\r"),
        c if u32::from(c) < 0x20 || (0x7f..0x100).contains(&u32::from(c)) => push_hex_escape(out, c),
        c if c.is_ascii() => out.push(c),
        c => push_hex_escape(out, c),
    }
}

/// `_str_literal_helper`: escape `s` for a string literal and return it with the quote types
/// that can enclose it, best first.
///
/// escape_special_whitespace  also escape `\n` and `\t` (f-string parts)
pub(super) fn str_literal_helper(
    s: &str,
    quote_types: &[&'static str],
    escape_special_whitespace: bool,
) -> (String, Vec<&'static str>) {
    let mut escaped = String::with_capacity(s.len());
    for c in s.chars() {
        if !escape_special_whitespace && (c == '\n' || c == '\t') {
            escaped.push(c);
        } else if c == '\\' || !is_printable(c) {
            unicode_escape(c, &mut escaped);
        } else {
            escaped.push(c);
        }
    }
    let mut possible: Vec<&'static str> = quote_types.to_vec();
    if escaped.contains('\n') {
        possible.retain(|q| MULTI_QUOTES.contains(q));
    }
    possible.retain(|q| !escaped.contains(q));
    if possible.is_empty() {
        let r = str_repr(s);
        let first = r.chars().next().unwrap_or('\'');
        let quote =
            quote_types.iter().copied().find(|q| q.contains(first)).unwrap_or(if first == '"' { "\"" } else { "'" });
        return (r[1..r.len() - 1].to_owned(), vec![quote]);
    }
    if let Some(last) = escaped.chars().last() {
        // Stable sort: quotes that do not start with the last character come first.
        possible.sort_by_key(|q| q.starts_with(last));
        if possible[0].starts_with(last) {
            escaped.pop();
            escaped.push('\\');
            escaped.push(last);
        }
    }
    (escaped, possible)
}

/// Return Python's `repr` of a string.
pub fn str_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            c if c == quote || c == '\\' => {
                out.push('\\');
                out.push(c);
            }
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            c if u32::from(c) < 0x20 || u32::from(c) == 0x7f => push_hex_escape(&mut out, c),
            c if c.is_ascii() || is_printable(c) => out.push(c),
            c => push_hex_escape(&mut out, c),
        }
    }
    out.push(quote);
    out
}

/// Return Python's `repr` of a bytes value.
pub(super) fn bytes_repr(b: &[u8]) -> String {
    let quote = if b.contains(&b'\'') && !b.contains(&b'"') { b'"' } else { b'\'' };
    let mut out = String::from("b");
    out.push(char::from(quote));
    for &c in b {
        match c {
            c if c == quote || c == b'\\' => {
                out.push('\\');
                out.push(char::from(c));
            }
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            c if !(0x20..0x7f).contains(&c) => out.push_str(&format!("\\x{c:02x}")),
            c => out.push(char::from(c)),
        }
    }
    out.push(char::from(quote));
    out
}

/// Return Python's `repr` of a float; with `add_dot_0`, integral values get ".0" (floats do,
/// the parts of a complex do not).
pub fn float_repr(f: f64, add_dot_0: bool) -> String {
    if f.is_nan() {
        return "nan".into();
    }
    if f.is_infinite() {
        return if f > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let sci = format!("{:e}", f.abs());
    let (mantissa, exp) = sci.split_once('e').unwrap_or((&sci, "0"));
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let exp: i32 = exp.parse().unwrap_or(0);
    let decpt = exp + 1; // the value is 0.<digits> * 10^decpt
    let sign = if f.is_sign_negative() { "-" } else { "" };
    let n = i32::try_from(digits.len()).unwrap_or(i32::MAX);
    let body = if decpt <= -4 || decpt > 16 {
        let mut m = digits[..1].to_owned();
        if digits.len() > 1 {
            m.push('.');
            m.push_str(&digits[1..]);
        }
        let e = decpt - 1;
        format!("{m}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs())
    } else if decpt <= 0 {
        format!("0.{}{digits}", "0".repeat(usize::try_from(-decpt).unwrap_or(0)))
    } else if decpt >= n {
        let zeros = "0".repeat(usize::try_from(decpt - n).unwrap_or(0));
        if add_dot_0 { format!("{digits}{zeros}.0") } else { format!("{digits}{zeros}") }
    } else {
        let at = usize::try_from(decpt).unwrap_or(0);
        format!("{}.{}", &digits[..at], &digits[at..])
    };
    format!("{sign}{body}")
}

/// `_write_constant`: the `repr` of a constant, with infinities written as `1e309`.
pub(super) fn constant_repr(c: &Const) -> String {
    match c {
        Const::None => "None".into(),
        Const::Bool(true) => "True".into(),
        Const::Bool(false) => "False".into(),
        Const::Ellipsis => "Ellipsis".into(),
        Const::Str(s) => str_repr(s),
        Const::Bytes(b) => bytes_repr(b),
        Const::Int(i) => i.to_string(),
        Const::Float(f) => float_repr(*f, true).replace("inf", INFSTR).replace("nan", &format!("({INFSTR}-{INFSTR})")),
        Const::Complex(im) => {
            let r = format!("{}j", float_repr(*im, false));
            r.replace("inf", INFSTR).replace("nan", &format!("({INFSTR}-{INFSTR})"))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{float_repr, str_repr};

    #[test]
    fn floats_print_like_python_repr() {
        assert_eq!(float_repr(0.0, true), "0.0");
        assert_eq!(float_repr(123.0, true), "123.0");
        assert_eq!(float_repr(0.001, true), "0.001");
        assert_eq!(float_repr(1e-5, true), "1e-05");
        assert_eq!(float_repr(1e16, true), "1e+16");
        assert_eq!(float_repr(1_234_567_890_123_456.0, true), "1234567890123456.0");
        assert_eq!(float_repr(1.5, false), "1.5");
        assert_eq!(float_repr(1.0, false), "1");
        assert_eq!(float_repr(1e100, true), "1e+100");
    }

    #[test]
    fn strings_print_like_python_repr() {
        assert_eq!(str_repr("a'b"), "\"a'b\"");
        assert_eq!(str_repr("a'b\""), "'a\\'b\"'");
        assert_eq!(str_repr("\u{e9}\n\u{a0}"), "'\u{e9}\\n\\xa0'");
    }
}
