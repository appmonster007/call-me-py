//! Parsing: Ruff's parser, then lowering of its tree into the CPython-shaped `ast`.
//!
//! This is the only module that uses Ruff's crates. Their API is internal to Ruff and changes
//! between releases, so the version is pinned exactly in `Cargo.toml`; an upgrade only needs
//! this file to be adapted.
//!
//! Lowering keeps CPython's shapes and positions where Ruff's differ:
//!
//! ```text
//!   decorated def / class   positioned at the `def`, `async` or `class` keyword, not the `@`
//!   generator argument      `f(x for x in y)`: the generator starts at the call's `(`
//!   `elif`                  a nested `If` alone in `orelse`, positioned at `elif`
//!   strings                 implicit concatenation is one `Constant`; f-strings become a
//!                           `JoinedStr` of merged string constants and `FormattedValue`s,
//!                           with `{x=}` spelled out as CPython does
//!   numbers                 integers as decimal digits, imaginary literals by their imaginary part
//!   comparisons             `left`, `ops` and `comparators`
//! ```
//!
//! Source is parsed as Python 3.13, the version the Python implementation runs on; syntax that
//! 3.13 rejects is a `SyntaxError` here too.

use std::sync::Arc;

use ruff_python_ast as r;
use ruff_python_parser::{Mode, ParseOptions};
use ruff_text_size::{Ranged, TextSize};

use super::SyntaxError;
use super::ast::*;
use super::lines::LineIndex;

/// Parse `source` as a Python 3.13 module and return its CPython-shaped tree.
///
/// Raises (returns) `SyntaxError` with the position of the first parse error, or of the first
/// use of syntax that Python 3.13 does not have.
pub fn parse_module(source: &str) -> Result<Module, SyntaxError> {
    let options = ParseOptions::from(Mode::Module).with_target_version(r::PythonVersion::PY313);
    let parsed = ruff_python_parser::parse_unchecked(source, options);
    let lines = LineIndex::new(source);
    if let Some(err) = parsed.errors().first() {
        return Err(SyntaxError { msg: err.error.to_string(), pos: lines.pos(err.location.start().into()) });
    }
    if let Some(err) = parsed.unsupported_syntax_errors().first() {
        return Err(SyntaxError { msg: err.to_string(), pos: lines.pos(err.range.start().into()) });
    }
    let Some(module) = parsed.try_into_module() else {
        return Err(SyntaxError { msg: "not a module".into(), pos: Pos { line: 1, col: 0 } });
    };
    let low = Lower { src: source.as_bytes(), lines };
    Ok(Module { body: low.stmts(&module.syntax().body), key: ScopeKey::fresh() })
}

/// The state of one lowering: the source bytes, for keyword positions, and its line index.
struct Lower<'s> {
    /// the source text
    src: &'s [u8],
    /// line starts of the source
    lines: LineIndex,
}

/// Return a `Name` for a Ruff identifier.
fn name(id: &r::Identifier) -> Name {
    Name::new(id.as_str())
}

impl Lower<'_> {
    /// Return the CPython position of byte offset `at`.
    fn pos(&self, at: TextSize) -> Pos {
        self.lines.pos(at.into())
    }

    /// Return the offset where `keyword` ends right before `at`, skipping blanks and line
    /// continuations, or None if `keyword` is not there.
    fn keyword_before(&self, at: TextSize, keyword: &str) -> Option<TextSize> {
        let mut i = usize::from(at);
        let b = self.src;
        loop {
            if i > 0 && matches!(b[i - 1], b' ' | b'\t' | b'\x0c') {
                i -= 1;
            } else if i >= 2 && b[i - 1] == b'\n' && b[i - 2] == b'\\' {
                i -= 2;
            } else if i >= 3 && b[i - 1] == b'\n' && b[i - 2] == b'\r' && b[i - 3] == b'\\' {
                i -= 3;
            } else if i >= 2 && b[i - 1] == b'\r' && b[i - 2] == b'\\' {
                i -= 2;
            } else {
                break;
            }
        }
        let k = keyword.len();
        (i >= k && &b[i - k..i] == keyword.as_bytes()).then(|| TextSize::try_from(i - k).expect("offsets fit in u32"))
    }

    /// Return CPython's position of a def or class: its `def` / `async` / `class` keyword.
    ///
    /// fallback  Ruff's start of the statement, used if the keyword is not found
    fn def_pos(&self, name_start: TextSize, keyword: &str, is_async: bool, fallback: TextSize) -> Pos {
        let Some(kw) = self.keyword_before(name_start, keyword) else {
            return self.pos(fallback);
        };
        if is_async && let Some(a) = self.keyword_before(kw, "async") {
            return self.pos(a);
        }
        self.pos(kw)
    }

    /// Lower a statement list.
    fn stmts(&self, body: &[r::Stmt]) -> Vec<Stmt> {
        body.iter().map(|s| self.stmt(s)).collect()
    }

    /// Lower an optional expression.
    fn opt(&self, e: Option<&r::Expr>) -> Option<Expr> {
        e.map(|e| self.expr(e))
    }

    /// Lower an optional boxed expression into a boxed expression.
    fn opt_box(&self, e: Option<&r::Expr>) -> Option<Box<Expr>> {
        e.map(|e| Box::new(self.expr(e)))
    }

    /// Lower an expression list.
    fn exprs(&self, es: &[r::Expr]) -> Vec<Expr> {
        es.iter().map(|e| self.expr(e)).collect()
    }

    /// Lower one statement.
    fn stmt(&self, s: &r::Stmt) -> Stmt {
        let pos = self.pos(s.range().start());
        match s {
            r::Stmt::FunctionDef(f) => Stmt::FunctionDef(Arc::new(FunctionDef {
                pos: self.def_pos(f.name.range.start(), "def", f.is_async, f.range.start()),
                is_async: f.is_async,
                name: name(&f.name),
                args: self.arguments(&f.parameters),
                body: self.stmts(&f.body),
                decorator_list: f.decorator_list.iter().map(|d| self.expr(&d.expression)).collect(),
                returns: self.opt_box(f.returns.as_deref()),
                type_params: self.type_params(f.type_params.as_deref()),
                key: ScopeKey::fresh(),
            })),
            r::Stmt::ClassDef(c) => {
                let (bases, keywords) = match c.arguments.as_deref() {
                    Some(a) => (self.call_args(a), self.keywords(&a.keywords)),
                    None => (Vec::new(), Vec::new()),
                };
                Stmt::ClassDef(Arc::new(ClassDef {
                    pos: self.def_pos(c.name.range.start(), "class", false, c.range.start()),
                    name: name(&c.name),
                    bases,
                    keywords,
                    body: self.stmts(&c.body),
                    decorator_list: c.decorator_list.iter().map(|d| self.expr(&d.expression)).collect(),
                    type_params: self.type_params(c.type_params.as_deref()),
                    key: ScopeKey::fresh(),
                }))
            }
            r::Stmt::Return(x) => Stmt::Return(Return { pos, value: self.opt(x.value.as_deref()) }),
            r::Stmt::Delete(x) => Stmt::Delete(Delete { pos, targets: self.exprs(&x.targets) }),
            r::Stmt::TypeAlias(x) => Stmt::TypeAlias(TypeAlias {
                pos,
                name: self.expr(&x.name),
                type_params: self.type_params(x.type_params.as_deref()),
                value: self.expr(&x.value),
            }),
            r::Stmt::Assign(x) => {
                Stmt::Assign(Assign { pos, targets: self.exprs(&x.targets), value: self.expr(&x.value) })
            }
            r::Stmt::AugAssign(x) => Stmt::AugAssign(AugAssign {
                pos,
                target: self.expr(&x.target),
                op: operator(x.op),
                value: self.expr(&x.value),
            }),
            r::Stmt::AnnAssign(x) => Stmt::AnnAssign(AnnAssign {
                pos,
                target: self.expr(&x.target),
                annotation: self.expr(&x.annotation),
                value: self.opt(x.value.as_deref()),
                simple: x.simple,
            }),
            r::Stmt::For(x) => Stmt::For(For {
                pos,
                is_async: x.is_async,
                target: self.expr(&x.target),
                iter: self.expr(&x.iter),
                body: self.stmts(&x.body),
                orelse: self.stmts(&x.orelse),
            }),
            r::Stmt::While(x) => Stmt::While(While {
                pos,
                test: self.expr(&x.test),
                body: self.stmts(&x.body),
                orelse: self.stmts(&x.orelse),
            }),
            r::Stmt::If(x) => Stmt::If(If {
                pos,
                test: self.expr(&x.test),
                body: self.stmts(&x.body),
                orelse: self.elif_chain(&x.elif_else_clauses),
            }),
            r::Stmt::With(x) => Stmt::With(With {
                pos,
                is_async: x.is_async,
                items: x
                    .items
                    .iter()
                    .map(|i| WithItem {
                        context_expr: self.expr(&i.context_expr),
                        optional_vars: self.opt(i.optional_vars.as_deref()),
                    })
                    .collect(),
                body: self.stmts(&x.body),
            }),
            r::Stmt::Match(x) => Stmt::Match(Match {
                pos,
                subject: self.expr(&x.subject),
                cases: x
                    .cases
                    .iter()
                    .map(|c| MatchCase {
                        pattern: self.pattern(&c.pattern),
                        guard: self.opt(c.guard.as_deref()),
                        body: self.stmts(&c.body),
                    })
                    .collect(),
            }),
            r::Stmt::Raise(x) => {
                Stmt::Raise(Raise { pos, exc: self.opt(x.exc.as_deref()), cause: self.opt(x.cause.as_deref()) })
            }
            r::Stmt::Try(x) => Stmt::Try(Try {
                pos,
                body: self.stmts(&x.body),
                handlers: x
                    .handlers
                    .iter()
                    .map(|h| {
                        let r::ExceptHandler::ExceptHandler(h) = h;
                        ExceptHandler {
                            pos: self.pos(h.range.start()),
                            type_: self.opt(h.type_.as_deref()),
                            name: h.name.as_ref().map(name),
                            body: self.stmts(&h.body),
                        }
                    })
                    .collect(),
                orelse: self.stmts(&x.orelse),
                finalbody: self.stmts(&x.finalbody),
                is_star: x.is_star,
            }),
            r::Stmt::Assert(x) => {
                Stmt::Assert(Assert { pos, test: self.expr(&x.test), msg: self.opt(x.msg.as_deref()) })
            }
            r::Stmt::Import(x) => Stmt::Import(Import { pos, names: self.aliases(&x.names) }),
            r::Stmt::ImportFrom(x) => Stmt::ImportFrom(ImportFrom {
                pos,
                module: x.module.as_ref().map(name),
                names: self.aliases(&x.names),
                level: x.level,
            }),
            r::Stmt::Global(x) => Stmt::Global(Global { pos, names: x.names.iter().map(name).collect() }),
            r::Stmt::Nonlocal(x) => Stmt::Nonlocal(Global { pos, names: x.names.iter().map(name).collect() }),
            r::Stmt::Expr(x) => Stmt::Expr(ExprStmt { pos, value: self.expr(&x.value) }),
            r::Stmt::Pass(_) => Stmt::Pass(pos),
            r::Stmt::Break(_) => Stmt::Break(pos),
            r::Stmt::Continue(_) => Stmt::Continue(pos),
            // IPython escapes only exist in notebook mode, which is never used.
            r::Stmt::IpyEscapeCommand(_) => Stmt::Pass(pos),
        }
    }

    /// Lower the `elif` / `else` clauses of an `if` into CPython's nested `orelse`.
    fn elif_chain(&self, clauses: &[r::ElifElseClause]) -> Vec<Stmt> {
        let Some((first, rest)) = clauses.split_first() else {
            return Vec::new();
        };
        match &first.test {
            Some(test) => vec![Stmt::If(If {
                pos: self.pos(first.range.start()),
                test: self.expr(test),
                body: self.stmts(&first.body),
                orelse: self.elif_chain(rest),
            })],
            None => self.stmts(&first.body),
        }
    }

    /// Lower import aliases.
    fn aliases(&self, names: &[r::Alias]) -> Vec<Alias> {
        names
            .iter()
            .map(|a| Alias { pos: self.pos(a.range.start()), name: name(&a.name), asname: a.asname.as_ref().map(name) })
            .collect()
    }

    /// Lower PEP 695 type parameters.
    fn type_params(&self, tp: Option<&r::TypeParams>) -> Vec<TypeParam> {
        let Some(tp) = tp else { return Vec::new() };
        tp.type_params
            .iter()
            .map(|p| match p {
                r::TypeParam::TypeVar(v) => TypeParam::TypeVar {
                    pos: self.pos(v.range.start()),
                    name: name(&v.name),
                    bound: self.opt_box(v.bound.as_deref()),
                    default_value: self.opt_box(v.default.as_deref()),
                },
                r::TypeParam::ParamSpec(v) => TypeParam::ParamSpec {
                    pos: self.pos(v.range.start()),
                    name: name(&v.name),
                    default_value: self.opt_box(v.default.as_deref()),
                },
                r::TypeParam::TypeVarTuple(v) => TypeParam::TypeVarTuple {
                    pos: self.pos(v.range.start()),
                    name: name(&v.name),
                    default_value: self.opt_box(v.default.as_deref()),
                },
            })
            .collect()
    }

    /// Lower a parameter list into CPython's `arguments`.
    fn arguments(&self, p: &r::Parameters) -> Arguments {
        let positional = p.posonlyargs.iter().chain(p.args.iter());
        Arguments {
            posonlyargs: p.posonlyargs.iter().map(|a| self.arg(&a.parameter)).collect(),
            args: p.args.iter().map(|a| self.arg(&a.parameter)).collect(),
            vararg: p.vararg.as_deref().map(|a| self.arg(a)),
            kwonlyargs: p.kwonlyargs.iter().map(|a| self.arg(&a.parameter)).collect(),
            kw_defaults: p.kwonlyargs.iter().map(|a| self.opt(a.default.as_deref())).collect(),
            kwarg: p.kwarg.as_deref().map(|a| self.arg(a)),
            defaults: positional.filter_map(|a| a.default.as_deref()).map(|d| self.expr(d)).collect(),
        }
    }

    /// Lower one parameter; CPython positions it at its name, after any `*` or `**`.
    fn arg(&self, p: &r::Parameter) -> Arg {
        Arg {
            pos: self.pos(p.name.range.start()),
            arg: name(&p.name),
            annotation: self.opt_box(p.annotation.as_deref()),
        }
    }

    /// Lower keyword arguments.
    fn keywords(&self, kws: &[r::Keyword]) -> Vec<Keyword> {
        kws.iter()
            .map(|k| Keyword {
                pos: self.pos(k.range.start()),
                arg: k.arg.as_ref().map(name),
                value: self.expr(&k.value),
            })
            .collect()
    }

    /// Lower the positional arguments of a call or class.
    ///
    /// A lone generator argument written without its own parentheses starts at the call's
    /// `(` in CPython.
    fn call_args(&self, a: &r::Arguments) -> Vec<Expr> {
        if let [r::Expr::Generator(g)] = &a.args[..]
            && a.keywords.is_empty()
            && !g.parenthesized
        {
            return vec![Expr::GeneratorExp(Comp {
                pos: self.pos(a.range.start()),
                elt: Box::new(self.expr(&g.elt)),
                generators: self.comprehensions(&g.generators),
            })];
        }
        self.exprs(&a.args)
    }

    /// Lower the `for` clauses of a comprehension.
    fn comprehensions(&self, gens: &[r::Comprehension]) -> Vec<Comprehension> {
        gens.iter()
            .map(|g| Comprehension {
                target: self.expr(&g.target),
                iter: self.expr(&g.iter),
                ifs: self.exprs(&g.ifs),
                is_async: g.is_async,
            })
            .collect()
    }

    /// Lower an f-string, possibly implicitly concatenated with plain strings, to a `JoinedStr`.
    fn fstring(&self, pos: Pos, value: &r::FStringValue) -> Expr {
        let mut parts = Parts::default();
        for part in value {
            match part {
                r::FStringPartRef::Literal(s) => parts.text(self.pos(s.range.start()), &s.value),
                r::FStringPartRef::FString(f) => self.fstring_elements(&f.elements, &mut parts),
            }
        }
        Expr::JoinedStr(JoinedStr { pos, values: parts.finish() })
    }

    /// Add the literal text and replacement fields of an f-string (or format spec) to `parts`.
    fn fstring_elements(&self, elements: &r::InterpolatedStringElements, parts: &mut Parts) {
        for el in elements {
            match el {
                r::InterpolatedStringElement::Literal(l) => parts.text(self.pos(l.range.start()), &l.value),
                r::InterpolatedStringElement::Interpolation(i) => {
                    if let Some(d) = &i.debug_text {
                        let text = format!("{}{}{}", d.leading(), d.expression(), d.trailing());
                        parts.text(self.pos(i.range.start() + TextSize::from(1)), &text);
                    }
                    let conversion = match i.conversion {
                        r::ConversionFlag::None if i.debug_text.is_some() && i.format_spec.is_none() => 114,
                        r::ConversionFlag::None => -1,
                        r::ConversionFlag::Str => 115,
                        r::ConversionFlag::Ascii => 97,
                        r::ConversionFlag::Repr => 114,
                    };
                    let format_spec = i.format_spec.as_deref().map(|spec| {
                        let mut inner = Parts::default();
                        self.fstring_elements(&spec.elements, &mut inner);
                        let start = spec.range.start();
                        // CPython positions the spec at its `:`; Ruff's range starts after it.
                        let colon = if self.src.get(usize::from(start).wrapping_sub(1)) == Some(&b':') {
                            start - TextSize::from(1)
                        } else {
                            start
                        };
                        Box::new(Expr::JoinedStr(JoinedStr { pos: self.pos(colon), values: inner.finish() }))
                    });
                    parts.value(Expr::FormattedValue(FormattedValue {
                        pos: self.pos(i.range.start()),
                        value: Box::new(self.expr(&i.expression)),
                        conversion,
                        format_spec,
                    }));
                }
            }
        }
    }

    /// Lower one expression.
    #[allow(clippy::too_many_lines)]
    fn expr(&self, e: &r::Expr) -> Expr {
        let pos = self.pos(e.range().start());
        match e {
            r::Expr::BoolOp(x) => Expr::BoolOp(BoolOpExpr {
                pos,
                op: match x.op {
                    r::BoolOp::And => BoolOp::And,
                    r::BoolOp::Or => BoolOp::Or,
                },
                values: self.exprs(&x.values),
            }),
            r::Expr::Named(x) => Expr::NamedExpr(NamedExpr {
                pos,
                target: Box::new(self.expr(&x.target)),
                value: Box::new(self.expr(&x.value)),
            }),
            r::Expr::BinOp(x) => Expr::BinOp(BinOp {
                pos,
                left: Box::new(self.expr(&x.left)),
                op: operator(x.op),
                right: Box::new(self.expr(&x.right)),
            }),
            r::Expr::UnaryOp(x) => Expr::UnaryOp(UnaryOpExpr {
                pos,
                op: match x.op {
                    r::UnaryOp::Invert => UnaryOp::Invert,
                    r::UnaryOp::Not => UnaryOp::Not,
                    r::UnaryOp::UAdd => UnaryOp::UAdd,
                    r::UnaryOp::USub => UnaryOp::USub,
                },
                operand: Box::new(self.expr(&x.operand)),
            }),
            r::Expr::Lambda(x) => Expr::Lambda(Arc::new(Lambda {
                pos,
                args: x.parameters.as_deref().map(|p| self.arguments(p)).unwrap_or_default(),
                body: Box::new(self.expr(&x.body)),
                key: ScopeKey::fresh(),
            })),
            r::Expr::If(x) => Expr::IfExp(IfExp {
                pos,
                test: Box::new(self.expr(&x.test)),
                body: Box::new(self.expr(&x.body)),
                orelse: Box::new(self.expr(&x.orelse)),
            }),
            r::Expr::Dict(x) => Expr::Dict(Dict {
                pos,
                keys: x.items.iter().map(|i| self.opt(i.key.as_ref())).collect(),
                values: x.items.iter().map(|i| self.expr(&i.value)).collect(),
            }),
            r::Expr::Set(x) => Expr::Set(Elts { pos, elts: self.exprs(&x.elts), ctx: Ctx::Load }),
            r::Expr::ListComp(x) => Expr::ListComp(Comp {
                pos,
                elt: Box::new(self.expr(&x.elt)),
                generators: self.comprehensions(&x.generators),
            }),
            r::Expr::SetComp(x) => Expr::SetComp(Comp {
                pos,
                elt: Box::new(self.expr(&x.elt)),
                generators: self.comprehensions(&x.generators),
            }),
            r::Expr::DictComp(x) => Expr::DictComp(DictComp {
                pos,
                key: Box::new(
                    x.key
                        .as_deref()
                        .map_or(Expr::Constant(Constant { pos, value: Const::None, kind_u: false }), |k| self.expr(k)),
                ),
                value: Box::new(self.expr(&x.value)),
                generators: self.comprehensions(&x.generators),
            }),
            r::Expr::Generator(x) => Expr::GeneratorExp(Comp {
                pos,
                elt: Box::new(self.expr(&x.elt)),
                generators: self.comprehensions(&x.generators),
            }),
            r::Expr::Await(x) => Expr::Await(Unary { pos, value: Box::new(self.expr(&x.value)) }),
            r::Expr::Yield(x) => Expr::Yield(Yield { pos, value: self.opt_box(x.value.as_deref()) }),
            r::Expr::YieldFrom(x) => Expr::YieldFrom(Unary { pos, value: Box::new(self.expr(&x.value)) }),
            r::Expr::Compare(x) => Expr::Compare(Compare {
                pos,
                left: Box::new(self.expr(&x.operands[0])),
                ops: x.ops.iter().map(|o| cmpop(*o)).collect(),
                comparators: self.exprs(&x.operands[1..]),
            }),
            r::Expr::Call(x) => Expr::Call(Call {
                pos,
                func: Box::new(self.expr(&x.func)),
                args: self.call_args(&x.arguments),
                keywords: self.keywords(&x.arguments.keywords),
            }),
            r::Expr::FString(x) => self.fstring(pos, &x.value),
            r::Expr::TString(x) => {
                // Python 3.13 has no t-strings; `parse_module` reports them before lowering.
                let mut parts = Parts::default();
                for t in &x.value {
                    self.fstring_elements(&t.elements, &mut parts);
                }
                Expr::JoinedStr(JoinedStr { pos, values: parts.finish() })
            }
            r::Expr::StringLiteral(x) => {
                let text: String = x.value.iter().map(|s| &*s.value).collect();
                Expr::Constant(Constant { pos, value: Const::Str(text.into()), kind_u: x.value.is_unicode() })
            }
            r::Expr::BytesLiteral(x) => {
                let bytes: Vec<u8> = x.value.bytes().collect();
                Expr::Constant(Constant { pos, value: Const::Bytes(bytes.into()), kind_u: false })
            }
            r::Expr::NumberLiteral(x) => Expr::Constant(Constant {
                pos,
                value: match &x.value {
                    r::Number::Int(i) => Const::Int(int_decimal(&i.to_string()).into()),
                    r::Number::Float(f) => Const::Float(*f),
                    r::Number::Complex { imag, .. } => Const::Complex(*imag),
                },
                kind_u: false,
            }),
            r::Expr::BooleanLiteral(x) => Expr::Constant(Constant { pos, value: Const::Bool(x.value), kind_u: false }),
            r::Expr::NoneLiteral(_) => Expr::Constant(Constant { pos, value: Const::None, kind_u: false }),
            r::Expr::EllipsisLiteral(_) => Expr::Constant(Constant { pos, value: Const::Ellipsis, kind_u: false }),
            r::Expr::Attribute(x) => Expr::Attribute(Attribute {
                pos,
                value: Box::new(self.expr(&x.value)),
                attr: name(&x.attr),
                ctx: ctx(x.ctx),
            }),
            r::Expr::Subscript(x) => Expr::Subscript(Subscript {
                pos,
                value: Box::new(self.expr(&x.value)),
                slice: Box::new(self.expr(&x.slice)),
                ctx: ctx(x.ctx),
            }),
            r::Expr::Starred(x) => {
                Expr::Starred(Starred { pos, value: Box::new(self.expr(&x.value)), ctx: ctx(x.ctx) })
            }
            r::Expr::Name(x) => Expr::Name(NameExpr { pos, id: Name::new(x.id.as_str()), ctx: ctx(x.ctx) }),
            r::Expr::List(x) => Expr::List(Elts { pos, elts: self.exprs(&x.elts), ctx: ctx(x.ctx) }),
            r::Expr::Tuple(x) => Expr::Tuple(Elts { pos, elts: self.exprs(&x.elts), ctx: ctx(x.ctx) }),
            r::Expr::Slice(x) => Expr::Slice(Slice {
                pos,
                lower: self.opt_box(x.lower.as_deref()),
                upper: self.opt_box(x.upper.as_deref()),
                step: self.opt_box(x.step.as_deref()),
            }),
            r::Expr::IpyEscapeCommand(_) => Expr::Constant(Constant { pos, value: Const::None, kind_u: false }),
        }
    }

    /// Lower a `match` pattern.
    fn pattern(&self, p: &r::Pattern) -> Pattern {
        let pos = self.pos(p.range().start());
        let pats = |ps: &[r::Pattern]| ps.iter().map(|p| self.pattern(p)).collect::<Vec<_>>();
        match p {
            r::Pattern::MatchValue(x) => Pattern::MatchValue { pos, value: Box::new(self.expr(&x.value)) },
            r::Pattern::MatchSingleton(x) => Pattern::MatchSingleton {
                pos,
                value: match x.value {
                    r::Singleton::None => Const::None,
                    r::Singleton::True => Const::Bool(true),
                    r::Singleton::False => Const::Bool(false),
                },
            },
            r::Pattern::MatchSequence(x) => Pattern::MatchSequence { pos, patterns: pats(&x.patterns) },
            r::Pattern::MatchMapping(x) => Pattern::MatchMapping {
                pos,
                keys: self.exprs(&x.keys),
                patterns: pats(&x.patterns),
                rest: x.rest.as_ref().map(name),
            },
            r::Pattern::MatchClass(x) => Pattern::MatchClass {
                pos,
                cls: Box::new(self.expr(&x.cls)),
                patterns: pats(&x.arguments.patterns),
                kwd_attrs: x.arguments.keywords.iter().map(|k| name(&k.attr)).collect(),
                kwd_patterns: x.arguments.keywords.iter().map(|k| self.pattern(&k.pattern)).collect(),
            },
            r::Pattern::MatchStar(x) => Pattern::MatchStar { pos, name: x.name.as_ref().map(name) },
            r::Pattern::MatchAs(x) => Pattern::MatchAs {
                pos,
                pattern: x.pattern.as_deref().map(|p| Box::new(self.pattern(p))),
                name: x.name.as_ref().map(name),
            },
            r::Pattern::MatchOr(x) => Pattern::MatchOr { pos, patterns: pats(&x.patterns) },
        }
    }
}

/// The values of a `JoinedStr` being built: adjacent literal text merges into one constant.
#[derive(Default)]
struct Parts {
    /// finished values
    values: Vec<Expr>,
    /// literal text not yet closed, with the position of its first non-empty piece
    text: Option<(Pos, String)>,
}

impl Parts {
    /// Append literal text found at `pos`; empty pieces are skipped, as in CPython.
    fn text(&mut self, pos: Pos, s: &str) {
        if s.is_empty() {
            return;
        }
        match &mut self.text {
            Some((_, buf)) => buf.push_str(s),
            None => self.text = Some((pos, s.to_owned())),
        }
    }

    /// Close the pending text into a string constant.
    fn flush(&mut self) {
        if let Some((pos, s)) = self.text.take() {
            self.values.push(Expr::Constant(Constant { pos, value: Const::Str(s.into()), kind_u: false }));
        }
    }

    /// Append a replacement field.
    fn value(&mut self, e: Expr) {
        self.flush();
        self.values.push(e);
    }

    /// Return all values, the pending text included.
    fn finish(mut self) -> Vec<Expr> {
        self.flush();
        self.values
    }
}

/// Lower a binary operator.
fn operator(op: r::Operator) -> Operator {
    match op {
        r::Operator::Add => Operator::Add,
        r::Operator::Sub => Operator::Sub,
        r::Operator::Mult => Operator::Mult,
        r::Operator::MatMult => Operator::MatMult,
        r::Operator::Div => Operator::Div,
        r::Operator::Mod => Operator::Mod,
        r::Operator::Pow => Operator::Pow,
        r::Operator::LShift => Operator::LShift,
        r::Operator::RShift => Operator::RShift,
        r::Operator::BitOr => Operator::BitOr,
        r::Operator::BitXor => Operator::BitXor,
        r::Operator::BitAnd => Operator::BitAnd,
        r::Operator::FloorDiv => Operator::FloorDiv,
    }
}

/// Lower a comparison operator.
fn cmpop(op: r::CmpOp) -> CmpOp {
    match op {
        r::CmpOp::Eq => CmpOp::Eq,
        r::CmpOp::NotEq => CmpOp::NotEq,
        r::CmpOp::Lt => CmpOp::Lt,
        r::CmpOp::LtE => CmpOp::LtE,
        r::CmpOp::Gt => CmpOp::Gt,
        r::CmpOp::GtE => CmpOp::GtE,
        r::CmpOp::Is => CmpOp::Is,
        r::CmpOp::IsNot => CmpOp::IsNot,
        r::CmpOp::In => CmpOp::In,
        r::CmpOp::NotIn => CmpOp::NotIn,
    }
}

/// Lower an expression context.
fn ctx(c: r::ExprContext) -> Ctx {
    match c {
        r::ExprContext::Store => Ctx::Store,
        r::ExprContext::Del => Ctx::Del,
        r::ExprContext::Load | r::ExprContext::Invalid => Ctx::Load,
    }
}

/// Return the decimal digits of an integer literal such as `0x_ff`, `0o17`, `0b1` or `1_000`.
///
/// Ruff keeps integers that do not fit in 64 bits as their source text, so the conversion is
/// done here with base-10^9 limbs; small integers arrive as decimal digits already.
fn int_decimal(token: &str) -> String {
    let t: String = token.chars().filter(|&c| c != '_').collect();
    let lower = t.to_ascii_lowercase();
    let (digits, radix) = if let Some(d) = lower.strip_prefix("0x") {
        (d, 16)
    } else if let Some(d) = lower.strip_prefix("0o") {
        (d, 8)
    } else if let Some(d) = lower.strip_prefix("0b") {
        (d, 2)
    } else {
        let d = lower.trim_start_matches('0');
        return if d.is_empty() { "0".into() } else { d.into() };
    };
    const BASE: u64 = 1_000_000_000;
    let mut limbs: Vec<u64> = vec![0]; // little-endian, base 10^9
    for c in digits.chars() {
        let mut carry = u64::from(c.to_digit(radix).unwrap_or(0));
        for limb in &mut limbs {
            let v = *limb * u64::from(radix) + carry;
            *limb = v % BASE;
            carry = v / BASE;
        }
        while carry > 0 {
            limbs.push(carry % BASE);
            carry /= BASE;
        }
    }
    let mut out = limbs.last().copied().unwrap_or(0).to_string();
    for limb in limbs.iter().rev().skip(1) {
        out.push_str(&format!("{limb:09}"));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::int_decimal;

    #[test]
    fn integer_literals_become_decimal() {
        assert_eq!(int_decimal("1_000"), "1000");
        assert_eq!(int_decimal("0x_ff"), "255");
        assert_eq!(int_decimal("0o17"), "15");
        assert_eq!(int_decimal("0b101"), "5");
        assert_eq!(int_decimal("0x10000000000000000"), "18446744073709551616");
        assert_eq!(int_decimal("000"), "0");
    }
}
