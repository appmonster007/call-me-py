//! Layer 5 of the interpreter: expressions. `eval` dispatches on the expression's kind; names,
//! lambdas, calls, literals and containers, conditional and boolean expressions, `await`,
//! `yield` and comprehensions live here.

use indexmap::IndexMap;
use rustc_hash::FxBuildHasher;

use crate::model::{EMPTY, SiteKind, Vals};
use crate::name;
use crate::stubs::Kws;
use crate::syntax::Name;
use crate::syntax::ast::{Call, Comprehension, Ctx, Expr, Lambda};
use crate::syntax::walk::{NodeRef, walk};

use super::base::Interp;
use crate::analysis::state::{State, mk_closure};

/// The values that comprehension targets had before the comprehension rebound them.
type Saved = IndexMap<Name, Option<Vals>, FxBuildHasher>;

impl Interp<'_> {
    /// Evaluate expression `e` by dispatching on its kind (`e_<Expr>` in the Python version).
    pub fn eval(&mut self, e: &Expr) -> Vals {
        match e {
            Expr::Name(n) => {
                if n.ctx == Ctx::Load {
                    self.lookup(n.id)
                } else {
                    EMPTY
                }
            }
            Expr::Lambda(l) => self.e_lambda(l),
            Expr::Call(c) => self.e_call(e, c),
            Expr::IfExp(x) => {
                self.eval(&x.test);
                self.weakly(|it| {
                    let b = it.eval(&x.body);
                    let o = it.eval(&x.orelse);
                    it.u(b, o)
                })
            }
            Expr::BoolOp(x) => {
                let mut out = match x.values.first() {
                    Some(v) => self.eval(v),
                    None => EMPTY,
                };
                if x.values.len() > 1 {
                    out = self.weakly(|it| {
                        for v in &x.values[1..] {
                            let r = it.eval(v);
                            out = it.u(out, r);
                        }
                        out
                    });
                }
                out
            }
            Expr::NamedExpr(x) => {
                // the binding is weak, since it may sit in a branch
                let v = self.eval(&x.value);
                if let Expr::Name(n) = &*x.target {
                    self.bind(n.id, v, true);
                }
                v
            }
            Expr::List(x) => {
                let u = self.union_of(&x.elts);
                let b = self.new_box(name!("list"), e);
                self.u(u, b)
            }
            Expr::Set(x) => {
                let u = self.union_of(&x.elts);
                let b = self.new_box(name!("set"), e);
                self.u(u, b)
            }
            Expr::Tuple(x) => self.union_of(&x.elts), // immutable: no Box needed
            Expr::Dict(d) => {
                let mut out = self.new_box(name!("dict"), e);
                for (k, v) in d.keys.iter().zip(&d.values) {
                    let r = match k {
                        None => {
                            // {**other}
                            let x = self.eval(v);
                            self.state.deref(x)
                        }
                        Some(k) => {
                            self.eval(k);
                            self.eval(v)
                        }
                    };
                    out = self.u(out, r);
                }
                out
            }
            Expr::Starred(s) => {
                // [*xs]: the elements of xs
                let v = self.eval(&s.value);
                self.state.deref(v)
            }
            Expr::Await(a) => {
                let v = self.eval(&a.value);
                let site = self.site(e, SiteKind::Op);
                let (found, out) = self.invoke_dunder(v, name!("__await__"), &[], site);
                if found { out } else { v }
            }
            Expr::Yield(y) => {
                let v = match &y.value {
                    Some(x) => self.eval(x),
                    None => EMPTY,
                };
                self.ret = self.u(self.ret, v);
                EMPTY
            }
            Expr::YieldFrom(y) => {
                let v = self.eval(&y.value);
                let d = self.state.deref(v);
                self.ret = self.u(self.ret, d);
                EMPTY
            }
            Expr::GeneratorExp(c) => self.e_comp_elements(&c.generators, &c.elt),
            Expr::ListComp(c) => {
                let out = self.e_comp_elements(&c.generators, &c.elt);
                let b = self.new_box(name!("list"), e);
                self.u(out, b)
            }
            Expr::SetComp(c) => {
                let out = self.e_comp_elements(&c.generators, &c.elt);
                let b = self.new_box(name!("set"), e);
                self.u(out, b)
            }
            Expr::DictComp(c) => {
                let saved = self.comp(&c.generators);
                let out = self.weakly(|it| {
                    it.eval(&c.key);
                    it.eval(&c.value)
                });
                self.end_comp(saved);
                let b = self.new_box(name!("dict"), e);
                self.u(out, b)
            }
            Expr::Attribute(a) => self.e_attribute(e, a),
            Expr::Subscript(s) => self.e_subscript(e, s),
            Expr::BinOp(b) => self.e_binop(e, b),
            Expr::UnaryOp(u) => self.e_unaryop(e, u),
            Expr::Compare(c) => self.e_compare(e, c),
            Expr::FormattedValue(f) => self.e_formatted_value(e, f),
            Expr::JoinedStr(_) | Expr::Constant(_) | Expr::Slice(_) => self.generic(e),
        }
    }

    /// Evaluate `e` if it is present; `None` gives `EMPTY`.
    pub fn eval_opt(&mut self, e: Option<&Expr>) -> Vals {
        e.map_or(EMPTY, |e| self.eval(e))
    }

    /// Evaluate the subexpressions of `e` for their calls, and return `EMPTY`.
    pub fn generic(&mut self, e: &Expr) -> Vals {
        for c in NodeRef::Expr(e).children() {
            if let NodeRef::Expr(x) = c {
                self.eval(x);
            }
        }
        EMPTY
    }

    /// Evaluate a lambda's default values, then return its closure token.
    fn e_lambda(&mut self, l: &Lambda) -> Vals {
        let fid = self.prog.by_node[&l.key];
        for d in l.args.defaults.iter().chain(l.args.kw_defaults.iter().flatten()) {
            self.eval(d);
        }
        let c = mk_closure(&mut self.state.values, fid, self.node);
        self.single(c)
    }

    /// Evaluate a call: the callee, then the arguments, then the call itself through `apply`.
    ///
    /// For a method call `recv.m(...)` the receiver is evaluated once. If `m` resolves to
    /// nothing, the call may be a container method such as `xs.append(f)`, and
    /// `mutate_container` records what it stores.
    fn e_call(&mut self, e: &Expr, c: &Call) -> Vals {
        let mut recv: Option<Vals> = None; // receiver of a method call, evaluated once
        let callee = match &*c.func {
            Expr::Attribute(a) => {
                let r = self.eval(&a.value);
                recv = Some(r);
                self.attribute(r, &c.func, a)
            }
            other => self.eval(other),
        };
        let mut pos: Vec<Vals> = Vec::new();
        let mut star: Option<Vals> = None; // None = no */** at the call
        let mut kws = Kws::default();
        let mut kwstar: Option<Vals> = None;
        for a in &c.args {
            if let Expr::Starred(s) = a {
                let v = self.eval(&s.value);
                let d = self.state.deref(v);
                star = Some(self.u(star.unwrap_or(EMPTY), d));
            } else {
                let v = self.eval(a);
                pos.push(v);
            }
        }
        for k in &c.keywords {
            let v = self.eval(&k.value);
            match k.arg {
                None => {
                    let d = self.state.deref(v);
                    kwstar = Some(self.u(kwstar.unwrap_or(EMPTY), d));
                }
                Some(name) => {
                    State::join_into(&mut self.state.values, &mut kws, name, v);
                }
            }
        }
        let site = self.site(e, SiteKind::Call);
        let mut out = self.apply(callee, &pos, star, &kws, kwstar, site);
        if callee == EMPTY
            && let (Some(recv), Expr::Attribute(a)) = (recv, &*c.func)
        {
            let m = self.mutate_container(&a.value, recv, a.attr, &pos, star, &kws, kwstar);
            out = self.u(out, m);
        }
        out
    }

    /// Record the values a container method stores into `recv`, and return the call's result.
    ///
    /// Handles `append`, `add`, `extend`, `insert`, `setdefault` and `update`; the stored values
    /// are joined into the containers with `store`. Only `setdefault` returns something: the
    /// container's elements plus the default. Any other method returns `EMPTY`.
    ///
    /// target  the receiver expression, widened when the container has no `Box`
    #[allow(clippy::too_many_arguments)]
    fn mutate_container(
        &mut self,
        target: &Expr,
        recv: Vals,
        method: Name,
        pos: &[Vals],
        star: Option<Vals>,
        kws: &Kws,
        kwstar: Option<Vals>,
    ) -> Vals {
        let p = |i: usize| pos.get(i).copied().unwrap_or(EMPTY);
        let star = star.unwrap_or(EMPTY);
        let added = match &*method {
            "append" | "appendleft" | "add" => self.u(p(0), star),
            "extend" | "extendleft" => {
                // takes an iterable: its elements
                let x = self.u(p(0), star);
                self.state.deref(x)
            }
            "insert" => self.u(p(1), star),
            "setdefault" => {
                let d = kws.get(&name!("default")).copied().unwrap_or(EMPTY);
                let a = self.u(p(1), d);
                let b = self.u(a, star);
                let added = self.u(b, kwstar.unwrap_or(EMPTY));
                self.store(target, recv, added, true);
                let elems = self.state.deref(recv);
                return self.u(elems, added);
            }
            "update" => {
                let a = self.u(p(0), star);
                let b = self.u(a, kwstar.unwrap_or(EMPTY));
                let mut added = self.state.deref(b);
                for &v in kws.values() {
                    added = self.u(added, v);
                }
                added
            }
            _ => return EMPTY,
        };
        self.store(target, recv, added, true);
        EMPTY
    }

    /// Return the union of the values of the expressions in `elts`.
    fn union_of(&mut self, elts: &[Expr]) -> Vals {
        let mut out = EMPTY;
        for x in elts {
            let v = self.eval(x);
            out = self.u(out, v);
        }
        out
    }

    /// Bind the targets of a comprehension's generators to the elements of their iterables.
    ///
    /// Return the targets' values from the enclosing scope, so that `end_comp` can restore
    /// them in flow mode. The `if` conditions are evaluated for their calls.
    pub fn comp(&mut self, gens: &[Comprehension]) -> Saved {
        let mut saved = Saved::default();
        if self.flow {
            for g in gens {
                for n in walk(NodeRef::Expr(&g.target)) {
                    if let NodeRef::Expr(Expr::Name(x)) = n {
                        let cur = self.env.get(&x.id).copied();
                        saved.entry(x.id).or_insert(cur);
                    }
                }
            }
        }
        for (i, g) in gens.iter().enumerate() {
            if i > 0 {
                self.weak_depth += 1;
            }
            let elems = self.iter_expr(&g.iter, g.is_async);
            self.assign(&g.target, elems);
            self.weakly(|it| {
                for c in &g.ifs {
                    it.eval(c);
                }
            });
            if i > 0 {
                self.weak_depth -= 1;
            }
        }
        saved
    }

    /// Restore the variables a comprehension rebound, since its targets have their own scope.
    pub fn end_comp(&mut self, saved: Saved) {
        for (name, vals) in saved {
            match vals {
                None => {
                    self.env.shift_remove(&name);
                }
                Some(v) => {
                    self.env.insert(name, v);
                }
            }
        }
    }

    /// Evaluate a generator expression, or a list or set comprehension's body: its elements.
    fn e_comp_elements(&mut self, gens: &[Comprehension], elt: &Expr) -> Vals {
        let saved = self.comp(gens);
        let out = self.weakly(|it| it.eval(elt));
        self.end_comp(saved);
        out
    }
}
