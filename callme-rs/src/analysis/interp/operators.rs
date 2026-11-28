//! Layer 6 of the interpreter: operators. The dunders behind binary and unary operators,
//! comparisons, subscripts, f-string fields and augmented assignment.

use crate::model::{EMPTY, SiteKind, TokId, Token, Vals};
use crate::name;
use crate::stubs::{Kws, STUBS};
use crate::syntax::Name;
use crate::syntax::ast::{
    AugAssign, BinOp, CmpOp, Compare, Expr, FormattedValue, Operator, Subscript, UnaryOp, UnaryOpExpr,
};

use super::base::Interp;
use super::names::is_load;

/// Return the (normal, reflected, in-place) special methods of a binary operator.
pub fn binop_dunders(op: Operator) -> (Name, Name, Name) {
    match op {
        Operator::Add => (name!("__add__"), name!("__radd__"), name!("__iadd__")),
        Operator::Sub => (name!("__sub__"), name!("__rsub__"), name!("__isub__")),
        Operator::Mult => (name!("__mul__"), name!("__rmul__"), name!("__imul__")),
        Operator::MatMult => (name!("__matmul__"), name!("__rmatmul__"), name!("__imatmul__")),
        Operator::Div => (name!("__truediv__"), name!("__rtruediv__"), name!("__itruediv__")),
        Operator::FloorDiv => (name!("__floordiv__"), name!("__rfloordiv__"), name!("__ifloordiv__")),
        Operator::Mod => (name!("__mod__"), name!("__rmod__"), name!("__imod__")),
        Operator::Pow => (name!("__pow__"), name!("__rpow__"), name!("__ipow__")),
        Operator::LShift => (name!("__lshift__"), name!("__rlshift__"), name!("__ilshift__")),
        Operator::RShift => (name!("__rshift__"), name!("__rrshift__"), name!("__irshift__")),
        Operator::BitOr => (name!("__or__"), name!("__ror__"), name!("__ior__")),
        Operator::BitXor => (name!("__xor__"), name!("__rxor__"), name!("__ixor__")),
        Operator::BitAnd => (name!("__and__"), name!("__rand__"), name!("__iand__")),
    }
}

/// Return the special method of a unary operator other than `not`.
fn unaryop_dunder(op: UnaryOp) -> Option<Name> {
    match op {
        UnaryOp::UAdd => Some(name!("__pos__")),
        UnaryOp::USub => Some(name!("__neg__")),
        UnaryOp::Invert => Some(name!("__invert__")),
        UnaryOp::Not => None,
    }
}

/// Return the (normal, reflected) special methods of a comparison other than `in` / `is`.
fn cmp_dunders(op: CmpOp) -> Option<(Name, Name)> {
    match op {
        CmpOp::Eq => Some((name!("__eq__"), name!("__eq__"))),
        CmpOp::NotEq => Some((name!("__ne__"), name!("__ne__"))),
        CmpOp::Lt => Some((name!("__lt__"), name!("__gt__"))),
        CmpOp::LtE => Some((name!("__le__"), name!("__ge__"))),
        CmpOp::Gt => Some((name!("__gt__"), name!("__lt__"))),
        CmpOp::GtE => Some((name!("__ge__"), name!("__le__"))),
        CmpOp::Is | CmpOp::IsNot | CmpOp::In | CmpOp::NotIn => None,
    }
}

impl Interp<'_> {
    /// Evaluate `x[i]` when it is read: call `__getitem__` on instances that define it.
    ///
    /// Other values are containers, collapsed to their elements, so their value plus their
    /// stored contents is the result. A store or delete target gives `EMPTY`.
    pub fn e_subscript(&mut self, outer: &Expr, e: &Subscript) -> Vals {
        let slc = self.eval(&e.slice);
        let val = self.eval(&e.value);
        if !is_load(outer) {
            return EMPTY;
        }
        let site = self.site(outer, SiteKind::Op);
        let mut out = EMPTY;
        let mut rest: Vec<TokId> = Vec::new();
        self.weak_depth += 1;
        for &t in self.toks(val).iter() {
            match self.tok(t) {
                Token::Instance { cls, .. } => {
                    let f = self.state.lookup_class_attr(cls, name!("__getitem__"), None);
                    if f != EMPTY {
                        let bound = self.bind_all(f, cls, t);
                        let r = self.apply(bound, &[slc], None, &Kws::default(), None, site);
                        out = self.u(out, r);
                        continue;
                    }
                }
                Token::Builtin { name, bound, bound_kw } => {
                    let full = Name::new(&format!("{name}.__getitem__"));
                    if STUBS.contains(full) {
                        let callee = self.one(Token::Builtin { name: full, bound, bound_kw });
                        let r = self.apply(callee, &[slc], None, &Kws::default(), None, site);
                        out = self.u(out, r);
                        continue;
                    }
                }
                _ => {}
            }
            rest.push(t);
        }
        self.weak_depth -= 1;
        let rest = self.state.values.of(rest);
        let d = self.state.deref(rest);
        self.u(out, d)
    }

    /// Evaluate `a op b` by calling `a.__op__(b)`, or `b.__rop__(a)` if `a` does not have it.
    pub fn e_binop(&mut self, outer: &Expr, e: &BinOp) -> Vals {
        let left = self.eval(&e.left);
        let right = self.eval(&e.right);
        let (op, rop, _) = binop_dunders(e.op);
        let site = self.site(outer, SiteKind::Op);
        let (found, out) = self.invoke_dunder(left, op, &[right], site);
        if found {
            return out;
        }
        self.invoke_dunder(right, rop, &[left], site).1
    }

    /// Evaluate `-a`, `+a`, `~a` or `not a` by calling the matching special method.
    ///
    /// `not a` calls `__bool__`, or `__len__` when no class defines it, and returns `EMPTY`.
    pub fn e_unaryop(&mut self, outer: &Expr, e: &UnaryOpExpr) -> Vals {
        let v = self.eval(&e.operand);
        let site = self.site(outer, SiteKind::Op);
        let Some(dunder) = unaryop_dunder(e.op) else {
            let (found, _) = self.invoke_dunder(v, name!("__bool__"), &[], site);
            if !found {
                self.invoke_dunder(v, name!("__len__"), &[], site);
            }
            return EMPTY;
        };
        self.invoke_dunder(v, dunder, &[], site).1
    }

    /// Evaluate a comparison chain by calling the comparison methods; the result is `EMPTY`.
    ///
    /// Each `a op b` calls `a.__op__(b)`, or the reflected method of `b` when no instance has
    /// the first; `in` and `not in` call `b.__contains__(a)`.
    pub fn e_compare(&mut self, outer: &Expr, e: &Compare) -> Vals {
        let mut left = self.eval(&e.left);
        let site = self.site(outer, SiteKind::Op);
        for (i, (op, comp)) in e.ops.iter().zip(&e.comparators).enumerate() {
            if i > 0 {
                self.weak_depth += 1;
            }
            let right = self.eval(comp);
            if matches!(op, CmpOp::In | CmpOp::NotIn) {
                self.invoke_dunder(right, name!("__contains__"), &[left], site);
            } else if let Some((a, b)) = cmp_dunders(*op) {
                let (found, _) = self.invoke_dunder(left, a, &[right], site);
                if !found {
                    self.invoke_dunder(right, b, &[left], site);
                }
            }
            left = right;
            if i > 0 {
                self.weak_depth -= 1;
            }
        }
        EMPTY
    }

    /// Evaluate an f-string field, calling the special methods that formatting it would call.
    ///
    /// `!r` calls `__repr__` and `!s` calls `__str__`; otherwise `__format__` is tried first.
    /// Each falls back to the next of `__str__` and `__repr__` when no class defines it.
    pub fn e_formatted_value(&mut self, outer: &Expr, e: &FormattedValue) -> Vals {
        let v = self.eval(&e.value);
        self.eval_opt(e.format_spec.as_deref());
        let site = self.site(outer, SiteKind::Op);
        if e.conversion == i32::from(b'r') {
            self.invoke_dunder(v, name!("__repr__"), &[], site);
        } else if e.conversion == i32::from(b's') {
            let (found, _) = self.invoke_dunder(v, name!("__str__"), &[], site);
            if !found {
                self.invoke_dunder(v, name!("__repr__"), &[], site);
            }
        } else {
            let (mut found, _) = self.invoke_dunder(v, name!("__format__"), &[EMPTY], site);
            if !found {
                found = self.invoke_dunder(v, name!("__str__"), &[], site).0;
            }
            if !found {
                self.invoke_dunder(v, name!("__repr__"), &[], site);
            }
        }
        EMPTY
    }

    /// Run `target op= value`, calling `__iop__` on the target, or `__op__` when it has none.
    ///
    /// `+=` and `|=` also store the value's elements into the target's containers, since lists,
    /// sets and dicts are mutated in place. In flow mode the target keeps its old value too,
    /// because it still refers to the same container.
    pub fn s_aug_assign(&mut self, s: &AugAssign) {
        let rhs = self.eval(&s.value);
        let lhs = match &s.target {
            Expr::Name(n) => self.lookup(n.id),
            _ => EMPTY,
        };
        let (op, _, iop) = binop_dunders(s.op);
        let mut res = EMPTY;
        if lhs != EMPTY {
            let site = self.site(&s.value, SiteKind::Op);
            let (found, r) = self.invoke_dunder(lhs, iop, &[rhs], site);
            res = if found { r } else { self.invoke_dunder(lhs, op, &[rhs], site).1 };
        }
        if matches!(s.op, Operator::Add | Operator::BitOr) {
            // xs += ys / s |= t / d |= e mutate in place
            let d = self.state.deref(rhs);
            self.store(&s.target, lhs, d, false);
        }
        // flow mode keeps lhs: `xs += ys` rebinds xs to the same (mutated) container
        let keep = if self.flow { lhs } else { EMPTY };
        let a = self.u(keep, res);
        let all = self.u(a, rhs);
        self.assign(&s.target, all);
    }
}
