//! Layer 3 of the interpreter: calls. Binding arguments to parameters (with default values),
//! creating instances, and `apply`, which calls every callable a value may be.

use crate::frontend::{DefaultRef, NameSet};
use crate::model::{Alloc, EMPTY, FuncId, NodeId, Site, TokId, Token, Vals};
use crate::name;
use crate::stubs::{Args, Kws, STUBS};
use crate::syntax::Name;
use crate::syntax::ast::Expr;

use super::base::{Interp, bind_attr};
use crate::analysis::state::{DepKey, Env, State, Writes};

impl Interp<'_> {
    /// Evaluate default value `d` of closure `func` (created in `parent`) in read-only mode,
    /// where the function was defined.
    ///
    /// With a known parent node, the default is evaluated in that node's summary environment;
    /// otherwise in the enclosing function, joined over all its contexts.
    pub fn default_value(&mut self, func: FuncId, parent: Option<NodeId>, d: DefaultRef) -> Vals {
        let prog = self.prog;
        let expr: &Expr = prog.func(func).default_expr(d);
        match parent {
            Some(p) => {
                self.state.read(DepKey::Env(p));
                let env = self.state.envs.get(p).cloned().unwrap_or_default();
                let mut it = Interp::new(prog, self.state, Some(p), true, None);
                it.env = env;
                it.eval(expr)
            }
            None => {
                let mut it = Interp::new(prog, self.state, None, true, prog.func(func).parent);
                it.eval(expr)
            }
        }
    }

    /// Bind call arguments to the parameters of closure `func`; return the callee's entry state.
    ///
    /// The result is a list of (parameter, values) pairs sorted by name, without parameters
    /// that get no values. With `*args` or `**kwargs` at the call, their values may fill any
    /// parameter that is still open. Default values that are not constants are evaluated where
    /// the function was defined. Return None when the call cannot match the signature: too many
    /// positional arguments, an unknown or repeated keyword, or a missing required argument.
    pub fn bind_args(
        &mut self,
        func: FuncId,
        parent: Option<NodeId>,
        pos: &[Vals],
        star: Option<Vals>,
        kws: &Kws,
        kwstar: Option<Vals>,
    ) -> Option<Vec<(Name, Vals)>> {
        let prog = self.prog;
        let sig = &prog.func(func).sig;
        let mut vals = Env::default();
        let mut assigned = NameSet::default();
        let values = &mut self.state.values;
        for (i, &v) in pos.iter().enumerate() {
            if let Some(&p) = sig.pos.get(i) {
                State::join_into(values, &mut vals, p, v);
                assigned.insert(p);
            } else {
                let va = sig.vararg?; // without `*args`: too many positional arguments
                State::join_into(values, &mut vals, va, v);
            }
        }
        if let Some(s) = star {
            for &n in sig.pos.iter().skip(pos.len()) {
                State::join_into(values, &mut vals, n, s);
            }
            if let Some(va) = sig.vararg {
                State::join_into(values, &mut vals, va, s);
            }
        }
        let is_param = |n: &Name| sig.pos.contains(n) || sig.kwonly.contains(n);
        for (&k, &v) in kws {
            if is_param(&k) {
                if assigned.contains(&k) {
                    return None;
                }
                State::join_into(values, &mut vals, k, v);
                assigned.insert(k);
            } else {
                let kw = sig.kwarg?; // without `**kwargs`: an unknown keyword
                State::join_into(values, &mut vals, kw, v);
            }
        }
        if let Some(ks) = kwstar {
            for &n in sig.pos.iter().chain(&sig.kwonly) {
                if !assigned.contains(&n) {
                    State::join_into(values, &mut vals, n, ks);
                }
            }
            if let Some(kw) = sig.kwarg {
                State::join_into(values, &mut vals, kw, ks);
            }
        }
        if sig.required.iter().any(|n| !assigned.contains(n)) && star.is_none() && kwstar.is_none() {
            return None; // missing required argument
        }
        for &(n, d) in &sig.defaults {
            if !assigned.contains(&n) && !matches!(prog.func(func).default_expr(d), Expr::Constant(_)) {
                let v = self.default_value(func, parent, d);
                State::join_into(&mut self.state.values, &mut vals, n, v);
            }
        }
        let mut out: Vec<(Name, Vals)> = vals.into_iter().filter(|&(_, v)| v != EMPTY).collect();
        out.sort_by_key(|p| p.0);
        Some(out)
    }

    /// Create instances of class token `tok` as `type.__call__` does, and return them.
    ///
    /// `__new__` is called if the class or a base defines it; otherwise a new instance is
    /// allocated at `site`. Each created instance of the class then has `__init__` called with
    /// the same arguments.
    pub fn instantiate(
        &mut self,
        tok: TokId,
        pos: &[Vals],
        star: Option<Vals>,
        kws: &Kws,
        kwstar: Option<Vals>,
        site: Site,
    ) -> Vals {
        let Token::ClassObj(cls) = self.tok(tok) else {
            return EMPTY;
        };
        self.weakly(|it| {
            let new_fn = it.state.lookup_class_attr(cls, name!("__new__"), None);
            let created = if new_fn == EMPTY {
                it.one(Token::Instance { cls, alloc: Some(Alloc::of(&site)) })
            } else {
                let bound_new = it.bind_all(new_fn, cls, tok);
                let mut args = vec![it.single(tok)];
                args.extend_from_slice(pos);
                it.apply(bound_new, &args, star, kws, kwstar, site)
            };
            for &obj in it.toks(created).iter() {
                let Some(ocls) = it.instance_cls(obj) else {
                    continue;
                };
                if it.state.mro(ocls).contains(&cls) {
                    let init = it.state.lookup_class_attr(ocls, name!("__init__"), None);
                    if init != EMPTY {
                        let bound_init = it.bind_all(init, ocls, obj);
                        it.apply(bound_init, pos, star, kws, kwstar, site);
                    }
                }
            }
            created
        })
    }

    /// Call every callable in `callee` with these arguments at `site`, and return the results.
    ///
    /// What happens depends on the token:
    ///
    /// ```text
    ///   Closure      the arguments are bound (`bind_args`), the callee node is resolved (a new
    ///                context if needed), a (site, callee) edge is recorded, and its return
    ///                value so far is the result; a call that cannot match the signature is
    ///                skipped
    ///   Builtin      its stub runs, with bound arguments prepended and containers dereferenced
    ///   BoundMethod  the receiver is passed as the first argument
    ///   ClassObj     a metaclass `__call__` if there is one, else `instantiate`
    ///   Instance     its class's `__call__`
    ///   StaticMethod its function is called unbound
    /// ```
    ///
    /// Other tokens, modules among them, are not callable and are skipped. In read-only mode no
    /// context is registered and no edge is recorded. In flow mode, side-effect writes from
    /// `State.writes` are applied back to `env` (strongly when every matching closure
    /// statically must-writes the variable) and propagated to this node's `State.writes`.
    ///
    /// ```text
    /// star    values passed through `*args`, or None when the call has no `*` argument
    /// kwstar  values passed through `**kwargs`, or None when the call has no `**` argument
    /// ```
    #[allow(clippy::too_many_lines)]
    pub fn apply(
        &mut self,
        callee: Vals,
        pos: &[Vals],
        star: Option<Vals>,
        kws: &Kws,
        kwstar: Option<Vals>,
        site: Site,
    ) -> Vals {
        let mut out = EMPTY;
        let toks = self.toks(callee);
        let all_closures =
            !toks.is_empty() && toks.iter().all(|&t| matches!(self.state.values.get(t), Token::Closure { .. }));
        let can_kill = self.flow
            && !self.readonly
            && self.weak_depth == 0
            && self.node.is_some_and(|n| !self.state.ctx_of(n).merged);
        let mut collected_writes = Writes::default();
        let mut kills: Option<NameSet> = None;
        let guard_poly = !all_closures && toks.len() > 1;
        if guard_poly {
            self.weak_depth += 1;
        }
        for &tok in toks.iter() {
            match self.tok(tok) {
                Token::Builtin { name, bound, bound_kw } => {
                    let Some(f) = STUBS.get(name) else {
                        continue;
                    };
                    // stubs read their arguments as elements
                    let mut args = Args::default();
                    for &v in bound.iter().chain(pos) {
                        let d = self.state.deref(v);
                        args.pos.push(d);
                    }
                    args.star = star.map(|s| self.state.deref(s));
                    for &(k, v) in bound_kw.iter() {
                        let d = self.state.deref(v);
                        args.kws.insert(k, d);
                    }
                    for (&k, &v) in kws {
                        let d = self.state.deref(v);
                        args.kws.insert(k, d);
                    }
                    args.kwstar = kwstar.map(|s| self.state.deref(s));
                    let r = self.weakly(|it| f(it, &args, site));
                    out = self.u(out, r);
                }
                Token::BoundMethod { func, receiver } => {
                    let f = self.single(func);
                    let mut args = vec![self.single(receiver)];
                    args.extend_from_slice(pos);
                    let r = self.apply(f, &args, star, kws, kwstar, site);
                    out = self.u(out, r);
                }
                Token::ClassObj(cls) => {
                    let metas = self.state.get_metaclasses(cls);
                    let mut meta_calls: Vec<TokId> = Vec::new();
                    for m in metas {
                        let call_fns = self.state.lookup_class_attr(m, name!("__call__"), None);
                        for &f in self.toks(call_fns).iter() {
                            let b = if matches!(self.state.values.get(f), Token::Closure { .. }) {
                                self.state.values.tok(Token::BoundMethod { func: f, receiver: tok })
                            } else {
                                bind_attr(&mut self.state.values, f, m, tok)
                            };
                            meta_calls.push(b);
                        }
                    }
                    let r = if meta_calls.is_empty() {
                        self.instantiate(tok, pos, star, kws, kwstar, site)
                    } else {
                        let mc = self.state.values.of(meta_calls);
                        self.apply(mc, pos, star, kws, kwstar, site)
                    };
                    out = self.u(out, r);
                }
                Token::StaticMethod(f) => {
                    let fv = self.single(f);
                    let r = self.apply(fv, pos, star, kws, kwstar, site);
                    out = self.u(out, r);
                }
                Token::Instance { cls, .. } => {
                    let call_fn = self.state.lookup_class_attr(cls, name!("__call__"), None);
                    if call_fn != EMPTY {
                        let others: Vec<TokId> = self.toks(call_fn).iter().copied().filter(|&t| t != tok).collect();
                        let bound: Vec<TokId> =
                            others.into_iter().map(|t| bind_attr(&mut self.state.values, t, cls, tok)).collect();
                        let bv = self.state.values.of(bound);
                        let r = self.apply(bv, pos, star, kws, kwstar, site);
                        out = self.u(out, r);
                    }
                }
                Token::ClassMethod(_)
                | Token::Super { .. }
                | Token::Property { .. }
                | Token::Box { .. }
                | Token::Module(_) => {}
                Token::Closure { func, parent } => {
                    let Some(params) = self.bind_args(func, parent, pos, star, kws, kwstar) else {
                        continue;
                    };
                    let target = if self.readonly {
                        self.state.node_of(func, &params, parent, false)
                    } else {
                        let target = self.state.resolve(func, &params, parent);
                        self.calls.push((site, target));
                        if self.flow {
                            self.state.read(DepKey::Writes(target));
                            if let Some(tw) = self.state.writes.get(target).cloned() {
                                for (owner, env) in tw {
                                    let dst = collected_writes.entry(owner).or_default();
                                    for (name, vals) in env {
                                        State::join_into(&mut self.state.values, dst, name, vals);
                                    }
                                }
                            }
                            if can_kill && all_closures {
                                let tk = self.must_kills_for_closure(func, parent);
                                kills = Some(match kills {
                                    None => tk,
                                    Some(k) => k.into_iter().filter(|n| tk.contains(n)).collect(),
                                });
                            }
                        }
                        target
                    };
                    self.state.read(DepKey::Ret(target));
                    let r = self.state.ret.get(target).copied().unwrap_or(EMPTY);
                    out = self.u(out, r);
                }
            }
        }
        if guard_poly {
            self.weak_depth -= 1;
        }
        let kills = kills.unwrap_or_default();
        if !collected_writes.is_empty() || !kills.is_empty() {
            self.apply_writes(&collected_writes, &kills);
        }
        out
    }
}
