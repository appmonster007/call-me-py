//! The syntax tree, shaped like CPython 3.13's `ast` module.
//!
//! Node and field names are those of `ast` (`FunctionDef.decorator_list`, `Call.keywords`,
//! `Dict.keys` with `None` for `**d`, `If.orelse` holding an `elif` as a nested `If`), so the
//! analysis reads like its Python original. `lower` builds this tree from Ruff's.
//!
//! Positions are CPython's: `Pos.line` is the 1-based `lineno` and `Pos.col` the 0-based
//! `col_offset` in UTF-8 bytes, which call sites, function ids and allocation sites use.
//! Nodes that CPython gives no position (`comprehension`, `arguments`, `withitem`,
//! `match_case`) have none here either.
//!
//! The nodes that open a scope (def, lambda, class, module) are shared through `Arc`, so that
//! the program's function and class tables can hold them, and carry a `ScopeKey`, which stands
//! in for `id(node)` in the Python version.

use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};

pub use super::name::Name;

/// A CPython source position: 1-based line and 0-based column in UTF-8 bytes.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Pos {
    /// 1-based line (`lineno`)
    pub line: u32,
    /// 0-based column in UTF-8 bytes (`col_offset`)
    pub col: u32,
}

/// Unique identity of a scope node (def, lambda, class or module) for the whole process.
///
/// It replaces `id(node)` as the key of the program's node tables.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ScopeKey(pub u32);

/// The source of fresh scope keys.
static NEXT_KEY: AtomicU32 = AtomicU32::new(0);

impl ScopeKey {
    /// Return a key that no other scope node has.
    pub fn fresh() -> ScopeKey {
        ScopeKey(NEXT_KEY.fetch_add(1, Ordering::Relaxed))
    }
}

/// A parsed module: its statements and its scope key.
#[derive(Debug)]
pub struct Module {
    /// the module's statements
    pub body: Vec<Stmt>,
    /// identity of the module body as a scope
    pub key: ScopeKey,
}

/// A statement.
#[derive(Debug)]
#[allow(missing_docs)]
pub enum Stmt {
    FunctionDef(Arc<FunctionDef>),
    ClassDef(Arc<ClassDef>),
    Return(Return),
    Delete(Delete),
    Assign(Assign),
    TypeAlias(TypeAlias),
    AugAssign(AugAssign),
    AnnAssign(AnnAssign),
    For(For),
    While(While),
    If(If),
    With(With),
    Match(Match),
    Raise(Raise),
    Try(Try),
    Assert(Assert),
    Import(Import),
    ImportFrom(ImportFrom),
    Global(Global),
    Nonlocal(Global),
    Expr(ExprStmt),
    Pass(Pos),
    Break(Pos),
    Continue(Pos),
}

/// `def` or `async def` (CPython's `FunctionDef` and `AsyncFunctionDef`).
#[derive(Debug)]
pub struct FunctionDef {
    /// position of the `def` (or `async`) keyword, not of the first decorator
    pub pos: Pos,
    /// True for `async def`
    pub is_async: bool,
    /// the function's name
    pub name: Name,
    /// its parameters
    pub args: Arguments,
    /// its statements
    pub body: Vec<Stmt>,
    /// decorator expressions, top to bottom
    pub decorator_list: Vec<Expr>,
    /// the return annotation
    pub returns: Option<Box<Expr>>,
    /// PEP 695 type parameters
    pub type_params: Vec<TypeParam>,
    /// identity of this def as a scope
    pub key: ScopeKey,
}

/// `class`.
#[derive(Debug)]
pub struct ClassDef {
    /// position of the `class` keyword
    pub pos: Pos,
    /// the class name
    pub name: Name,
    /// base class expressions
    pub bases: Vec<Expr>,
    /// keywords such as `metaclass=M`
    pub keywords: Vec<Keyword>,
    /// the class body
    pub body: Vec<Stmt>,
    /// decorator expressions, top to bottom
    pub decorator_list: Vec<Expr>,
    /// PEP 695 type parameters
    pub type_params: Vec<TypeParam>,
    /// identity of this class as a scope
    pub key: ScopeKey,
}

/// `return [value]`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Return {
    pub pos: Pos,
    pub value: Option<Expr>,
}

/// `del a, b`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Delete {
    pub pos: Pos,
    pub targets: Vec<Expr>,
}

/// `a = b = value`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Assign {
    pub pos: Pos,
    pub targets: Vec<Expr>,
    pub value: Expr,
}

/// `type X[T] = value`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct TypeAlias {
    pub pos: Pos,
    pub name: Expr,
    pub type_params: Vec<TypeParam>,
    pub value: Expr,
}

/// `target op= value`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct AugAssign {
    pub pos: Pos,
    pub target: Expr,
    pub op: Operator,
    pub value: Expr,
}

/// `target: annotation [= value]`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct AnnAssign {
    pub pos: Pos,
    pub target: Expr,
    pub annotation: Expr,
    pub value: Option<Expr>,
    /// True for a plain name target that was not parenthesised
    pub simple: bool,
}

/// `for` or `async for` (CPython's `For` and `AsyncFor`).
#[derive(Debug)]
#[allow(missing_docs)]
pub struct For {
    pub pos: Pos,
    pub is_async: bool,
    pub target: Expr,
    pub iter: Expr,
    pub body: Vec<Stmt>,
    pub orelse: Vec<Stmt>,
}

/// `while test: ... else: ...`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct While {
    pub pos: Pos,
    pub test: Expr,
    pub body: Vec<Stmt>,
    pub orelse: Vec<Stmt>,
}

/// `if test: ... else: ...`; an `elif` is a nested `If` alone in `orelse`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct If {
    /// position of the `if` (or `elif`) keyword
    pub pos: Pos,
    pub test: Expr,
    pub body: Vec<Stmt>,
    pub orelse: Vec<Stmt>,
}

/// `with` or `async with` (CPython's `With` and `AsyncWith`).
#[derive(Debug)]
#[allow(missing_docs)]
pub struct With {
    pub pos: Pos,
    pub is_async: bool,
    pub items: Vec<WithItem>,
    pub body: Vec<Stmt>,
}

/// `match subject: case ...`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Match {
    pub pos: Pos,
    pub subject: Expr,
    pub cases: Vec<MatchCase>,
}

/// `raise [exc [from cause]]`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Raise {
    pub pos: Pos,
    pub exc: Option<Expr>,
    pub cause: Option<Expr>,
}

/// `try` or `try` with `except*` (CPython's `Try` and `TryStar`).
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Try {
    pub pos: Pos,
    pub body: Vec<Stmt>,
    pub handlers: Vec<ExceptHandler>,
    pub orelse: Vec<Stmt>,
    pub finalbody: Vec<Stmt>,
    /// True for `except*` handlers (`TryStar`)
    pub is_star: bool,
}

/// `assert test[, msg]`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Assert {
    pub pos: Pos,
    pub test: Expr,
    pub msg: Option<Expr>,
}

/// `import a.b as c, d`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Import {
    pub pos: Pos,
    pub names: Vec<Alias>,
}

/// `from [.]*module import names`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct ImportFrom {
    pub pos: Pos,
    /// the module after the dots, if any
    pub module: Option<Name>,
    pub names: Vec<Alias>,
    /// the number of leading dots
    pub level: u32,
}

/// `global a, b` or `nonlocal a, b`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Global {
    pub pos: Pos,
    pub names: Vec<Name>,
}

/// An expression statement (CPython's `Expr` statement).
#[derive(Debug)]
#[allow(missing_docs)]
pub struct ExprStmt {
    pub pos: Pos,
    pub value: Expr,
}

/// An expression.
#[derive(Debug)]
#[allow(missing_docs)]
pub enum Expr {
    BoolOp(BoolOpExpr),
    NamedExpr(NamedExpr),
    BinOp(BinOp),
    UnaryOp(UnaryOpExpr),
    Lambda(Arc<Lambda>),
    IfExp(IfExp),
    Dict(Dict),
    Set(Elts),
    ListComp(Comp),
    SetComp(Comp),
    DictComp(DictComp),
    GeneratorExp(Comp),
    Await(Unary),
    Yield(Yield),
    YieldFrom(Unary),
    Compare(Compare),
    Call(Call),
    FormattedValue(FormattedValue),
    JoinedStr(JoinedStr),
    Constant(Constant),
    Attribute(Attribute),
    Subscript(Subscript),
    Starred(Starred),
    Name(NameExpr),
    List(Elts),
    Tuple(Elts),
    Slice(Slice),
}

/// `a and b and c`, `a or b`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct BoolOpExpr {
    pub pos: Pos,
    pub op: BoolOp,
    pub values: Vec<Expr>,
}

/// `target := value`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct NamedExpr {
    pub pos: Pos,
    pub target: Box<Expr>,
    pub value: Box<Expr>,
}

/// `left op right`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct BinOp {
    pub pos: Pos,
    pub left: Box<Expr>,
    pub op: Operator,
    pub right: Box<Expr>,
}

/// `op operand`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct UnaryOpExpr {
    pub pos: Pos,
    pub op: UnaryOp,
    pub operand: Box<Expr>,
}

/// `lambda args: body`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Lambda {
    pub pos: Pos,
    pub args: Arguments,
    pub body: Box<Expr>,
    /// identity of this lambda as a scope
    pub key: ScopeKey,
}

/// `body if test else orelse`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct IfExp {
    pub pos: Pos,
    pub test: Box<Expr>,
    pub body: Box<Expr>,
    pub orelse: Box<Expr>,
}

/// `{k: v, **d}`; the key of a `**d` entry is `None`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Dict {
    pub pos: Pos,
    pub keys: Vec<Option<Expr>>,
    pub values: Vec<Expr>,
}

/// A node with a list of elements: `Set`, `List` and `Tuple`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Elts {
    pub pos: Pos,
    pub elts: Vec<Expr>,
    /// Load, Store or Del (always Load for `Set`)
    pub ctx: Ctx,
}

/// `ListComp`, `SetComp` and `GeneratorExp`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Comp {
    pub pos: Pos,
    pub elt: Box<Expr>,
    pub generators: Vec<Comprehension>,
}

/// `{key: value for ...}`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct DictComp {
    pub pos: Pos,
    pub key: Box<Expr>,
    pub value: Box<Expr>,
    pub generators: Vec<Comprehension>,
}

/// A node with one operand: `Await` and `YieldFrom`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Unary {
    pub pos: Pos,
    pub value: Box<Expr>,
}

/// `yield [value]`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Yield {
    pub pos: Pos,
    pub value: Option<Box<Expr>>,
}

/// `left op1 c1 op2 c2 ...`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Compare {
    pub pos: Pos,
    pub left: Box<Expr>,
    pub ops: Vec<CmpOp>,
    pub comparators: Vec<Expr>,
}

/// `func(args, keywords)`; `*a` is a `Starred` argument and `**k` a keyword without name.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Call {
    pub pos: Pos,
    pub func: Box<Expr>,
    pub args: Vec<Expr>,
    pub keywords: Vec<Keyword>,
}

/// One `{value!conversion:format_spec}` field of an f-string.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct FormattedValue {
    /// position of the `{`
    pub pos: Pos,
    pub value: Box<Expr>,
    /// -1, or the code of `s`, `r` or `a`
    pub conversion: i32,
    /// a `JoinedStr`
    pub format_spec: Option<Box<Expr>>,
}

/// An f-string: string constants and `FormattedValue`s.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct JoinedStr {
    pub pos: Pos,
    pub values: Vec<Expr>,
}

/// A literal value.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Constant {
    pub pos: Pos,
    pub value: Const,
    /// True for a string written with a `u` prefix (CPython's `kind == "u"`)
    pub kind_u: bool,
}

/// The value of a `Constant`.
#[derive(Debug, Clone, PartialEq)]
pub enum Const {
    /// `None`
    None,
    /// `True` or `False`
    Bool(bool),
    /// `...`
    Ellipsis,
    /// a string
    Str(Box<str>),
    /// a bytes literal
    Bytes(Box<[u8]>),
    /// an integer, as its decimal digits
    Int(Box<str>),
    /// a float
    Float(f64),
    /// an imaginary literal such as `2j`: its imaginary part
    Complex(f64),
}

/// `value.attr`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Attribute {
    pub pos: Pos,
    pub value: Box<Expr>,
    pub attr: Name,
    pub ctx: Ctx,
}

/// `value[slice]`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Subscript {
    pub pos: Pos,
    pub value: Box<Expr>,
    pub slice: Box<Expr>,
    pub ctx: Ctx,
}

/// `*value`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Starred {
    pub pos: Pos,
    pub value: Box<Expr>,
    pub ctx: Ctx,
}

/// A variable (CPython's `Name` expression).
#[derive(Debug)]
#[allow(missing_docs)]
pub struct NameExpr {
    pub pos: Pos,
    pub id: Name,
    pub ctx: Ctx,
}

/// `lower:upper:step`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Slice {
    pub pos: Pos,
    pub lower: Option<Box<Expr>>,
    pub upper: Option<Box<Expr>>,
    pub step: Option<Box<Expr>>,
}

/// Whether an expression is read, assigned or deleted (`ast.Load`, `Store`, `Del`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[allow(missing_docs)]
pub enum Ctx {
    Load,
    Store,
    Del,
}

/// `and` / `or`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[allow(missing_docs)]
pub enum BoolOp {
    And,
    Or,
}

/// A binary operator.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[allow(missing_docs)]
pub enum Operator {
    Add,
    Sub,
    Mult,
    MatMult,
    Div,
    Mod,
    Pow,
    LShift,
    RShift,
    BitOr,
    BitXor,
    BitAnd,
    FloorDiv,
}

/// A unary operator.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[allow(missing_docs)]
pub enum UnaryOp {
    Invert,
    Not,
    UAdd,
    USub,
}

/// A comparison operator.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[allow(missing_docs)]
pub enum CmpOp {
    Eq,
    NotEq,
    Lt,
    LtE,
    Gt,
    GtE,
    Is,
    IsNot,
    In,
    NotIn,
}

/// One `for target in iter if cond` clause of a comprehension.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Comprehension {
    pub target: Expr,
    pub iter: Expr,
    pub ifs: Vec<Expr>,
    pub is_async: bool,
}

/// One `except [type [as name]]:` clause.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct ExceptHandler {
    /// position of the `except` keyword
    pub pos: Pos,
    pub type_: Option<Expr>,
    pub name: Option<Name>,
    pub body: Vec<Stmt>,
}

/// The parameter list of a def or lambda (CPython's `arguments`).
///
/// `defaults` belong to the last positional parameters; `kw_defaults` has one entry per
/// keyword-only parameter, `None` where it has no default.
#[derive(Debug, Default)]
#[allow(missing_docs)]
pub struct Arguments {
    pub posonlyargs: Vec<Arg>,
    pub args: Vec<Arg>,
    pub vararg: Option<Arg>,
    pub kwonlyargs: Vec<Arg>,
    pub kw_defaults: Vec<Option<Expr>>,
    pub kwarg: Option<Arg>,
    pub defaults: Vec<Expr>,
}

/// One parameter (CPython's `arg`).
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Arg {
    /// position of the parameter's name
    pub pos: Pos,
    pub arg: Name,
    pub annotation: Option<Box<Expr>>,
}

/// `name=value`, or `**value` when `arg` is None.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Keyword {
    pub pos: Pos,
    pub arg: Option<Name>,
    pub value: Expr,
}

/// `name [as asname]` in an import; `name` may be dotted, or `*`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct Alias {
    pub pos: Pos,
    pub name: Name,
    pub asname: Option<Name>,
}

/// `context_expr [as optional_vars]` in a `with`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct WithItem {
    pub context_expr: Expr,
    pub optional_vars: Option<Expr>,
}

/// `case pattern [if guard]: body`.
#[derive(Debug)]
#[allow(missing_docs)]
pub struct MatchCase {
    pub pattern: Pattern,
    pub guard: Option<Expr>,
    pub body: Vec<Stmt>,
}

/// A `match` pattern.
#[derive(Debug)]
#[allow(missing_docs)]
pub enum Pattern {
    MatchValue { pos: Pos, value: Box<Expr> },
    MatchSingleton { pos: Pos, value: Const },
    MatchSequence { pos: Pos, patterns: Vec<Pattern> },
    MatchMapping { pos: Pos, keys: Vec<Expr>, patterns: Vec<Pattern>, rest: Option<Name> },
    MatchClass { pos: Pos, cls: Box<Expr>, patterns: Vec<Pattern>, kwd_attrs: Vec<Name>, kwd_patterns: Vec<Pattern> },
    MatchStar { pos: Pos, name: Option<Name> },
    MatchAs { pos: Pos, pattern: Option<Box<Pattern>>, name: Option<Name> },
    MatchOr { pos: Pos, patterns: Vec<Pattern> },
}

/// A PEP 695 type parameter.
#[derive(Debug)]
#[allow(missing_docs)]
pub enum TypeParam {
    TypeVar { pos: Pos, name: Name, bound: Option<Box<Expr>>, default_value: Option<Box<Expr>> },
    ParamSpec { pos: Pos, name: Name, default_value: Option<Box<Expr>> },
    TypeVarTuple { pos: Pos, name: Name, default_value: Option<Box<Expr>> },
}

impl Stmt {
    /// Return the statement's CPython position.
    pub fn pos(&self) -> Pos {
        match self {
            Stmt::FunctionDef(s) => s.pos,
            Stmt::ClassDef(s) => s.pos,
            Stmt::Return(s) => s.pos,
            Stmt::Delete(s) => s.pos,
            Stmt::Assign(s) => s.pos,
            Stmt::TypeAlias(s) => s.pos,
            Stmt::AugAssign(s) => s.pos,
            Stmt::AnnAssign(s) => s.pos,
            Stmt::For(s) => s.pos,
            Stmt::While(s) => s.pos,
            Stmt::If(s) => s.pos,
            Stmt::With(s) => s.pos,
            Stmt::Match(s) => s.pos,
            Stmt::Raise(s) => s.pos,
            Stmt::Try(s) => s.pos,
            Stmt::Assert(s) => s.pos,
            Stmt::Import(s) => s.pos,
            Stmt::ImportFrom(s) => s.pos,
            Stmt::Global(s) | Stmt::Nonlocal(s) => s.pos,
            Stmt::Expr(s) => s.pos,
            Stmt::Pass(p) | Stmt::Break(p) | Stmt::Continue(p) => *p,
        }
    }

    /// Return CPython's class name for the statement, e.g. "AsyncFunctionDef" or "TryStar".
    pub fn type_name(&self) -> &'static str {
        match self {
            Stmt::FunctionDef(s) if s.is_async => "AsyncFunctionDef",
            Stmt::FunctionDef(_) => "FunctionDef",
            Stmt::ClassDef(_) => "ClassDef",
            Stmt::Return(_) => "Return",
            Stmt::Delete(_) => "Delete",
            Stmt::Assign(_) => "Assign",
            Stmt::TypeAlias(_) => "TypeAlias",
            Stmt::AugAssign(_) => "AugAssign",
            Stmt::AnnAssign(_) => "AnnAssign",
            Stmt::For(s) if s.is_async => "AsyncFor",
            Stmt::For(_) => "For",
            Stmt::While(_) => "While",
            Stmt::If(_) => "If",
            Stmt::With(s) if s.is_async => "AsyncWith",
            Stmt::With(_) => "With",
            Stmt::Match(_) => "Match",
            Stmt::Raise(_) => "Raise",
            Stmt::Try(s) if s.is_star => "TryStar",
            Stmt::Try(_) => "Try",
            Stmt::Assert(_) => "Assert",
            Stmt::Import(_) => "Import",
            Stmt::ImportFrom(_) => "ImportFrom",
            Stmt::Global(_) => "Global",
            Stmt::Nonlocal(_) => "Nonlocal",
            Stmt::Expr(_) => "Expr",
            Stmt::Pass(_) => "Pass",
            Stmt::Break(_) => "Break",
            Stmt::Continue(_) => "Continue",
        }
    }
}

impl Expr {
    /// Return the expression's CPython position.
    pub fn pos(&self) -> Pos {
        match self {
            Expr::BoolOp(e) => e.pos,
            Expr::NamedExpr(e) => e.pos,
            Expr::BinOp(e) => e.pos,
            Expr::UnaryOp(e) => e.pos,
            Expr::Lambda(e) => e.pos,
            Expr::IfExp(e) => e.pos,
            Expr::Dict(e) => e.pos,
            Expr::Set(e) | Expr::List(e) | Expr::Tuple(e) => e.pos,
            Expr::ListComp(e) | Expr::SetComp(e) | Expr::GeneratorExp(e) => e.pos,
            Expr::DictComp(e) => e.pos,
            Expr::Await(e) | Expr::YieldFrom(e) => e.pos,
            Expr::Yield(e) => e.pos,
            Expr::Compare(e) => e.pos,
            Expr::Call(e) => e.pos,
            Expr::FormattedValue(e) => e.pos,
            Expr::JoinedStr(e) => e.pos,
            Expr::Constant(e) => e.pos,
            Expr::Attribute(e) => e.pos,
            Expr::Subscript(e) => e.pos,
            Expr::Starred(e) => e.pos,
            Expr::Name(e) => e.pos,
            Expr::Slice(e) => e.pos,
        }
    }

    /// Return CPython's class name for the expression, e.g. "GeneratorExp".
    pub fn type_name(&self) -> &'static str {
        match self {
            Expr::BoolOp(_) => "BoolOp",
            Expr::NamedExpr(_) => "NamedExpr",
            Expr::BinOp(_) => "BinOp",
            Expr::UnaryOp(_) => "UnaryOp",
            Expr::Lambda(_) => "Lambda",
            Expr::IfExp(_) => "IfExp",
            Expr::Dict(_) => "Dict",
            Expr::Set(_) => "Set",
            Expr::ListComp(_) => "ListComp",
            Expr::SetComp(_) => "SetComp",
            Expr::DictComp(_) => "DictComp",
            Expr::GeneratorExp(_) => "GeneratorExp",
            Expr::Await(_) => "Await",
            Expr::Yield(_) => "Yield",
            Expr::YieldFrom(_) => "YieldFrom",
            Expr::Compare(_) => "Compare",
            Expr::Call(_) => "Call",
            Expr::FormattedValue(_) => "FormattedValue",
            Expr::JoinedStr(_) => "JoinedStr",
            Expr::Constant(_) => "Constant",
            Expr::Attribute(_) => "Attribute",
            Expr::Subscript(_) => "Subscript",
            Expr::Starred(_) => "Starred",
            Expr::Name(_) => "Name",
            Expr::List(_) => "List",
            Expr::Tuple(_) => "Tuple",
            Expr::Slice(_) => "Slice",
        }
    }

    /// Return the expression context of a name, attribute, subscript, starred, list or tuple.
    pub fn ctx(&self) -> Option<Ctx> {
        match self {
            Expr::Name(e) => Some(e.ctx),
            Expr::Attribute(e) => Some(e.ctx),
            Expr::Subscript(e) => Some(e.ctx),
            Expr::Starred(e) => Some(e.ctx),
            Expr::List(e) | Expr::Tuple(e) => Some(e.ctx),
            _ => None,
        }
    }

    /// Return the string value if this is a string constant.
    pub fn as_str_constant(&self) -> Option<&str> {
        match self {
            Expr::Constant(Constant { value: Const::Str(s), .. }) => Some(s),
            _ => None,
        }
    }
}

impl Pattern {
    /// Return the pattern's CPython position.
    pub fn pos(&self) -> Pos {
        match self {
            Pattern::MatchValue { pos, .. }
            | Pattern::MatchSingleton { pos, .. }
            | Pattern::MatchSequence { pos, .. }
            | Pattern::MatchMapping { pos, .. }
            | Pattern::MatchClass { pos, .. }
            | Pattern::MatchStar { pos, .. }
            | Pattern::MatchAs { pos, .. }
            | Pattern::MatchOr { pos, .. } => *pos,
        }
    }

    /// Return CPython's class name for the pattern, e.g. "MatchAs".
    pub fn type_name(&self) -> &'static str {
        match self {
            Pattern::MatchValue { .. } => "MatchValue",
            Pattern::MatchSingleton { .. } => "MatchSingleton",
            Pattern::MatchSequence { .. } => "MatchSequence",
            Pattern::MatchMapping { .. } => "MatchMapping",
            Pattern::MatchClass { .. } => "MatchClass",
            Pattern::MatchStar { .. } => "MatchStar",
            Pattern::MatchAs { .. } => "MatchAs",
            Pattern::MatchOr { .. } => "MatchOr",
        }
    }
}

impl TypeParam {
    /// Return the type parameter's CPython position.
    pub fn pos(&self) -> Pos {
        match self {
            TypeParam::TypeVar { pos, .. } | TypeParam::ParamSpec { pos, .. } | TypeParam::TypeVarTuple { pos, .. } => {
                *pos
            }
        }
    }

    /// Return CPython's class name for the type parameter.
    pub fn type_name(&self) -> &'static str {
        match self {
            TypeParam::TypeVar { .. } => "TypeVar",
            TypeParam::ParamSpec { .. } => "ParamSpec",
            TypeParam::TypeVarTuple { .. } => "TypeVarTuple",
        }
    }
}
