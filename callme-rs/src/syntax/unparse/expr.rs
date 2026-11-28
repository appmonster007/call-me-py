//! Expressions, f-strings and `match` patterns (`visit_<Expr>`, `visit_JoinedStr`,
//! `visit_Match<Pattern>`), with the operator tables.

use super::literals::{constant_repr, str_literal_helper, str_repr};
use super::{ALL_QUOTES, MULTI_QUOTES, Unparser, prec};
use crate::syntax::ast::*;

impl Unparser {
    /// `visit_comprehension`, for each clause of a comprehension.
    pub(super) fn generators(&mut self, gens: &[Comprehension]) {
        for g in gens {
            self.write(if g.is_async { " async for " } else { " for " });
            self.traverse(&g.target, prec::TUPLE);
            self.write(" in ");
            self.traverse(&g.iter, prec::next(prec::TEST));
            for c in &g.ifs {
                self.write(" if ");
                self.traverse(c, prec::next(prec::TEST));
            }
        }
    }

    /// Write expression `e`, whose context has precedence `p` (`traverse` with
    /// `set_precedence`).
    #[allow(clippy::too_many_lines)]
    pub(super) fn traverse(&mut self, e: &Expr, p: u8) {
        if self.done {
            return;
        }
        match e {
            Expr::NamedExpr(x) => self.parens_if(p > prec::NAMED_EXPR, |u| {
                u.traverse(&x.target, prec::ATOM);
                u.write(" := ");
                u.traverse(&x.value, prec::ATOM);
            }),
            Expr::Await(x) => self.parens_if(p > prec::AWAIT, |u| {
                u.write("await ");
                u.traverse(&x.value, prec::ATOM);
            }),
            Expr::Yield(x) => self.parens_if(p > prec::YIELD, |u| {
                u.write("yield");
                if let Some(v) = &x.value {
                    u.write(" ");
                    u.traverse(v, prec::ATOM);
                }
            }),
            Expr::YieldFrom(x) => self.parens_if(p > prec::YIELD, |u| {
                u.write("yield from ");
                u.traverse(&x.value, prec::ATOM);
            }),
            Expr::JoinedStr(x) => self.joined_str(x),
            Expr::FormattedValue(x) => self.formatted_value(x),
            Expr::Name(x) => self.write(&x.id),
            Expr::Constant(c) => {
                if c.value == Const::Ellipsis {
                    self.write("...");
                } else {
                    if c.kind_u {
                        self.write("u");
                    }
                    self.write(&constant_repr(&c.value));
                }
            }
            Expr::List(x) => {
                self.write("[");
                self.interleave(&x.elts, ", ", |u, e| u.traverse(e, prec::TEST));
                self.write("]");
            }
            Expr::ListComp(x) => {
                self.write("[");
                self.traverse(&x.elt, prec::TEST);
                self.generators(&x.generators);
                self.write("]");
            }
            Expr::GeneratorExp(x) => {
                self.write("(");
                self.traverse(&x.elt, prec::TEST);
                self.generators(&x.generators);
                self.write(")");
            }
            Expr::SetComp(x) => {
                self.write("{");
                self.traverse(&x.elt, prec::TEST);
                self.generators(&x.generators);
                self.write("}");
            }
            Expr::DictComp(x) => {
                self.write("{");
                self.traverse(&x.key, prec::TEST);
                self.write(": ");
                self.traverse(&x.value, prec::TEST);
                self.generators(&x.generators);
                self.write("}");
            }
            Expr::IfExp(x) => self.parens_if(p > prec::TEST, |u| {
                u.traverse(&x.body, prec::next(prec::TEST));
                u.write(" if ");
                u.traverse(&x.test, prec::next(prec::TEST));
                u.write(" else ");
                u.traverse(&x.orelse, prec::TEST);
            }),
            Expr::Set(x) => {
                if x.elts.is_empty() {
                    self.write("{*()}");
                } else {
                    self.write("{");
                    self.interleave(&x.elts, ", ", |u, e| u.traverse(e, prec::TEST));
                    self.write("}");
                }
            }
            Expr::Dict(x) => {
                self.write("{");
                for (i, (k, v)) in x.keys.iter().zip(&x.values).enumerate() {
                    if i > 0 {
                        self.write(", ");
                    }
                    match k {
                        None => {
                            self.write("**");
                            self.traverse(v, prec::EXPR);
                        }
                        Some(k) => {
                            self.traverse(k, prec::TEST);
                            self.write(": ");
                            self.traverse(v, prec::TEST);
                        }
                    }
                }
                self.write("}");
            }
            Expr::Tuple(x) => {
                self.parens_if(x.elts.is_empty() || p > prec::TUPLE, |u| u.items_view(&x.elts));
            }
            Expr::UnaryOp(x) => {
                let (op, op_prec) = match x.op {
                    UnaryOp::Invert => ("~", prec::FACTOR),
                    UnaryOp::Not => ("not", prec::NOT),
                    UnaryOp::UAdd => ("+", prec::FACTOR),
                    UnaryOp::USub => ("-", prec::FACTOR),
                };
                self.parens_if(p > op_prec, |u| {
                    u.write(op);
                    if op_prec != prec::FACTOR {
                        u.write(" ");
                    }
                    u.traverse(&x.operand, op_prec);
                });
            }
            Expr::BinOp(x) => {
                let (op, op_prec) = binop(x.op);
                self.parens_if(p > op_prec, |u| {
                    let (lp, rp) = if x.op == Operator::Pow {
                        (prec::next(op_prec), op_prec)
                    } else {
                        (op_prec, prec::next(op_prec))
                    };
                    u.traverse(&x.left, lp);
                    u.write(" ");
                    u.write(op);
                    u.write(" ");
                    u.traverse(&x.right, rp);
                });
            }
            Expr::Compare(x) => self.parens_if(p > prec::CMP, |u| {
                u.traverse(&x.left, prec::next(prec::CMP));
                for (o, c) in x.ops.iter().zip(&x.comparators) {
                    u.write(" ");
                    u.write(cmpop(*o));
                    u.write(" ");
                    u.traverse(c, prec::next(prec::CMP));
                }
            }),
            Expr::BoolOp(x) => {
                let (op, mut op_prec) = match x.op {
                    BoolOp::And => (" and ", prec::AND),
                    BoolOp::Or => (" or ", prec::OR),
                };
                self.parens_if(p > op_prec, |u| {
                    for (i, v) in x.values.iter().enumerate() {
                        if i > 0 {
                            u.write(op);
                        }
                        // CPython raises the precedence for every operand in turn.
                        op_prec = prec::next(op_prec);
                        u.traverse(v, op_prec);
                    }
                });
            }
            Expr::Attribute(x) => {
                self.traverse(&x.value, prec::ATOM);
                // `3.real` would be a float literal, so CPython writes `3 .real`.
                if let Expr::Constant(Constant { value: Const::Int(_) | Const::Bool(_), .. }) = &*x.value {
                    self.write(" ");
                }
                self.write(".");
                self.write(&x.attr);
            }
            Expr::Call(x) => {
                self.traverse(&x.func, prec::ATOM);
                self.write("(");
                let mut comma = false;
                for a in &x.args {
                    if comma {
                        self.write(", ");
                    }
                    comma = true;
                    self.traverse(a, prec::TEST);
                }
                for k in &x.keywords {
                    if comma {
                        self.write(", ");
                    }
                    comma = true;
                    self.keyword(k);
                }
                self.write(")");
            }
            Expr::Subscript(x) => {
                self.traverse(&x.value, prec::ATOM);
                self.write("[");
                match &*x.slice {
                    Expr::Tuple(t) if !t.elts.is_empty() => self.items_view(&t.elts),
                    s => self.traverse(s, prec::TEST),
                }
                self.write("]");
            }
            Expr::Starred(x) => {
                self.write("*");
                self.traverse(&x.value, prec::EXPR);
            }
            Expr::Slice(x) => {
                if let Some(l) = &x.lower {
                    self.traverse(l, prec::TEST);
                }
                self.write(":");
                if let Some(u) = &x.upper {
                    self.traverse(u, prec::TEST);
                }
                if let Some(s) = &x.step {
                    self.write(":");
                    self.traverse(s, prec::TEST);
                }
            }
            Expr::Lambda(x) => self.parens_if(p > prec::TEST, |u| {
                u.write("lambda");
                let mut inner = Unparser::new(None, false);
                inner.arguments(&x.args);
                if inner.wrote {
                    u.write(" ");
                    u.write(&inner.out);
                }
                u.write(": ");
                u.traverse(&x.body, prec::TEST);
            }),
        }
    }

    /// `visit_JoinedStr`: an f-string, with quotes that work for every part.
    pub(super) fn joined_str(&mut self, x: &JoinedStr) {
        self.write("f");
        let parts: Vec<(String, bool)> = x
            .values
            .iter()
            .map(|v| {
                let mut inner = Unparser::new(None, false);
                inner.fstring_inner(v, false);
                (inner.out, matches!(v, Expr::Constant(_)))
            })
            .collect();
        let mut quote_types: Vec<&'static str> = ALL_QUOTES.to_vec();
        let mut new_parts: Vec<String> = Vec::new();
        let mut fallback_to_repr = false;
        for (value, is_constant) in &parts {
            if *is_constant {
                let (v, new_quotes) = str_literal_helper(value, &quote_types, true);
                if new_quotes.iter().all(|q| !quote_types.contains(q)) {
                    fallback_to_repr = true;
                    break;
                }
                quote_types = new_quotes;
                new_parts.push(v);
            } else {
                if value.contains('\n') {
                    quote_types.retain(|q| MULTI_QUOTES.contains(q));
                }
                let new_quotes: Vec<&str> = quote_types.iter().copied().filter(|q| !value.contains(q)).collect();
                if !new_quotes.is_empty() {
                    quote_types = new_quotes;
                }
                new_parts.push(value.clone());
            }
        }
        if fallback_to_repr {
            quote_types = vec!["'''"];
            new_parts.clear();
            for (value, is_constant) in &parts {
                if *is_constant {
                    let r = str_repr(&format!("\"{value}"));
                    new_parts.push(r[2..r.len() - 1].to_owned());
                } else {
                    new_parts.push(value.clone());
                }
            }
        }
        let q = quote_types[0];
        self.write(q);
        self.write(&new_parts.concat());
        self.write(q);
    }

    /// `_write_fstring_inner`: one part of an f-string or of a format spec, without quotes.
    pub(super) fn fstring_inner(&mut self, e: &Expr, is_format_spec: bool) {
        match e {
            Expr::JoinedStr(j) => {
                for v in &j.values {
                    self.fstring_inner(v, is_format_spec);
                }
            }
            Expr::Constant(Constant { value: Const::Str(s), .. }) => {
                let mut v = s.replace('{', "{{").replace('}', "}}");
                if is_format_spec {
                    v = v.replace('\\', "\\\\").replace('\'', "\\'").replace('"', "\\\"").replace('\n', "\\n");
                }
                self.write(&v);
            }
            Expr::FormattedValue(f) => self.formatted_value(f),
            other => self.traverse(other, prec::TEST),
        }
    }

    /// `visit_FormattedValue`: `{value!conversion:format_spec}`.
    pub(super) fn formatted_value(&mut self, f: &FormattedValue) {
        self.write("{");
        let mut inner = Unparser::new(None, false);
        inner.traverse(&f.value, prec::next(prec::TEST));
        if inner.out.starts_with('{') {
            self.write(" ");
        }
        self.write(&inner.out);
        if f.conversion != -1 {
            self.write("!");
            let c = u8::try_from(f.conversion).map_or('?', char::from);
            self.write(c.encode_utf8(&mut [0; 4]));
        }
        if let Some(spec) = &f.format_spec {
            self.write(":");
            self.fstring_inner(spec, true);
        }
        self.write("}");
    }

    /// Write a `match` pattern in a context of precedence `p` (`visit_Match*`).
    pub(super) fn pattern(&mut self, pat: &Pattern, p: u8) {
        match pat {
            Pattern::MatchValue { value, .. } => self.traverse(value, prec::TEST),
            Pattern::MatchSingleton { value, .. } => self.write(&constant_repr(value)),
            Pattern::MatchSequence { patterns, .. } => {
                self.write("[");
                self.interleave(patterns, ", ", |u, q| u.pattern(q, prec::TEST));
                self.write("]");
            }
            Pattern::MatchStar { name, .. } => {
                self.write("*");
                self.write(name.as_ref().map_or("_", |n| n.as_str()));
            }
            Pattern::MatchMapping { keys, patterns, rest, .. } => {
                self.write("{");
                for (i, (k, q)) in keys.iter().zip(patterns).enumerate() {
                    if i > 0 {
                        self.write(", ");
                    }
                    self.traverse(k, prec::TEST);
                    self.write(": ");
                    self.pattern(q, prec::TEST);
                }
                if let Some(r) = rest {
                    if !keys.is_empty() {
                        self.write(", ");
                    }
                    self.write("**");
                    self.write(r);
                }
                self.write("}");
            }
            Pattern::MatchClass { cls, patterns, kwd_attrs, kwd_patterns, .. } => {
                self.traverse(cls, prec::ATOM);
                self.write("(");
                self.interleave(patterns, ", ", |u, q| u.pattern(q, prec::TEST));
                if !kwd_attrs.is_empty() {
                    if !patterns.is_empty() {
                        self.write(", ");
                    }
                    for (i, (a, q)) in kwd_attrs.iter().zip(kwd_patterns).enumerate() {
                        if i > 0 {
                            self.write(", ");
                        }
                        self.write(a);
                        self.write("=");
                        self.pattern(q, prec::TEST);
                    }
                }
                self.write(")");
            }
            Pattern::MatchAs { pattern, name, .. } => match (name, pattern) {
                (None, _) => self.write("_"),
                (Some(n), None) => self.write(n),
                (Some(n), Some(q)) => self.parens_if(p > prec::TEST, |u| {
                    u.pattern(q, prec::BOR);
                    u.write(" as ");
                    u.write(n);
                }),
            },
            Pattern::MatchOr { patterns, .. } => self.parens_if(p > prec::BOR, |u| {
                u.interleave(patterns, " | ", |u, q| u.pattern(q, prec::next(prec::BOR)));
            }),
        }
    }
}

/// Return the text and precedence of a binary operator (`binop`, `binop_precedence`).
pub(super) fn binop(op: Operator) -> (&'static str, u8) {
    match op {
        Operator::Add => ("+", prec::ARITH),
        Operator::Sub => ("-", prec::ARITH),
        Operator::Mult => ("*", prec::TERM),
        Operator::MatMult => ("@", prec::TERM),
        Operator::Div => ("/", prec::TERM),
        Operator::Mod => ("%", prec::TERM),
        Operator::LShift => ("<<", prec::SHIFT),
        Operator::RShift => (">>", prec::SHIFT),
        Operator::BitOr => ("|", prec::BOR),
        Operator::BitXor => ("^", prec::BXOR),
        Operator::BitAnd => ("&", prec::BAND),
        Operator::FloorDiv => ("//", prec::TERM),
        Operator::Pow => ("**", prec::POWER),
    }
}

/// Return the text of a comparison operator (`cmpops`).
fn cmpop(op: CmpOp) -> &'static str {
    match op {
        CmpOp::Eq => "==",
        CmpOp::NotEq => "!=",
        CmpOp::Lt => "<",
        CmpOp::LtE => "<=",
        CmpOp::Gt => ">",
        CmpOp::GtE => ">=",
        CmpOp::Is => "is",
        CmpOp::IsNot => "is not",
        CmpOp::In => "in",
        CmpOp::NotIn => "not in",
    }
}
