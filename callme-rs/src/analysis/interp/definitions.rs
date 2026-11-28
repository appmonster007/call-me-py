//! Layer 8 of the interpreter: definitions. `def` and `class` statements, and decorators.

use crate::model::{ClassId, EMPTY, Site, SiteKind, Style, TokId, Token, Vals};
use crate::name;
use crate::stubs::Kws;
use crate::syntax::Name;
use crate::syntax::ast::{ClassDef, Expr, FunctionDef};

use super::base::Interp;
use crate::analysis::state::{Env, State, mk_closure};

impl Interp<'_> {
    /// Apply decorator `dec` with evaluated value `callee` to `vals` and return the result.
    ///
    /// When `callee` is non-empty, it is called at the decorator site. When `callee` is empty,
    /// it is treated as the identity only if `(node, site)` has already been confirmed
    /// unresolved in `State.unknown_decos`; otherwise the site is recorded in `empty_decos` and
    /// `EMPTY` is returned, so that decorator factories whose return value has not converged
    /// yet do not leak spurious identity bindings on early rounds.
    fn apply_decorator(&mut self, dec: &Expr, callee: Vals, vals: Vals) -> Vals {
        let site = self.site(dec, SiteKind::Decorator);
        if callee != EMPTY {
            self.empty_decos.shift_remove(&site);
            return self.apply(callee, &[vals], None, &Kws::default(), None, site);
        }
        if let Some(n) = self.node
            && self.state.unknown_decos.contains(&(n, site))
        {
            return vals; // confirmed unresolved decorator: assume identity
        }
        self.empty_decos.insert(site);
        EMPTY
    }

    /// Run a def: evaluate its defaults, create its closure, apply its decorators, bind it.
    ///
    /// Decorators are applied from the bottom one up, each at its own decorator site. A
    /// decorator that remains unresolved after the fixpoint converges is assumed to be the
    /// identity, so the function keeps its own value (`apply_decorator`).
    pub fn s_function_def(&mut self, s: &FunctionDef) {
        let fid = self.prog.by_node[&s.key];
        for d in s.args.defaults.iter().chain(s.args.kw_defaults.iter().flatten()) {
            self.eval(d);
        }
        let c = mk_closure(&mut self.state.values, fid, self.node);
        let mut vals = self.single(c);
        for dec in s.decorator_list.iter().rev() {
            let callee = self.eval(dec);
            vals = self.apply_decorator(dec, callee, vals);
        }
        self.bind(s.name, vals, false);
    }

    /// Run a class statement and bind the name to the class object, as Python does.
    ///
    /// The decorators, bases and keywords are evaluated first; the bases and the `metaclass`
    /// keyword are recorded in `State`. The body then runs with the class namespace on
    /// `class_stack`, so its bindings become class attributes. Then, at an implicit "class"
    /// site, the first `__init_subclass__` of a base is called with the other keywords, and each
    /// metaclass's `__new__` and `__init__` are called with the bases and the namespace values.
    /// Last, the decorators are applied from the bottom one up; an unknown decorator is assumed
    /// to be the identity.
    #[allow(clippy::too_many_lines)]
    pub fn s_class_def(&mut self, s: &ClassDef) {
        let cid = self.prog.class_by_node[&s.key];
        let decs: Vec<Vals> = s.decorator_list.iter().map(|d| self.eval(d)).collect();
        let mut base_ids: Vec<ClassId> = Vec::new();
        let mut bases_val = EMPTY;
        for b in &s.bases {
            let bv = self.eval(b);
            bases_val = self.u(bases_val, bv);
            let mut toks: Vec<TokId> = self.toks(bv).to_vec();
            let prog = self.prog;
            toks.sort_by_cached_key(|&t| self.state.values.label(prog, t, Style::Full));
            for t in toks {
                if let Token::ClassObj(c) = self.tok(t) {
                    base_ids.push(c);
                }
            }
        }
        let mut meta_vals = EMPTY;
        let mut extra_kws = Kws::default();
        let mut extra_kwstar: Option<Vals> = None;
        for k in &s.keywords {
            let kv = self.eval(&k.value);
            match k.arg {
                Some(a) if a == name!("metaclass") => meta_vals = self.u(meta_vals, kv),
                None => extra_kwstar = Some(self.u(extra_kwstar.unwrap_or(EMPTY), kv)),
                Some(a) => {
                    State::join_into(&mut self.state.values, &mut extra_kws, a, kv);
                }
            }
        }
        if !self.readonly {
            if !base_ids.is_empty() {
                self.state.add_bases(cid, &base_ids);
            }
            for &t in self.toks(meta_vals).iter() {
                if let Token::ClassObj(m) = self.tok(t) {
                    self.state.add_metaclass(cid, m);
                }
            }
        }
        self.class_stack.push((cid, Env::default()));
        self.block(&s.body);
        let (_, ns) = self.class_stack.pop().expect("the class frame is on top");
        let ns_val = self.state.values.union_all(ns.values().copied());

        let cls_obj = self.state.values.tok(Token::ClassObj(cid));
        let cls_site = Site {
            caller: self.info.id,
            line: s.pos.line,
            col: s.pos.col,
            text: Name::new(&format!("class {}", s.name)),
            kind: SiteKind::Class,
        };
        let init_sub = self.state.lookup_class_attr(cid, name!("__init_subclass__"), Some(cid));
        if init_sub != EMPTY {
            let bound_sub = self.bind_all(init_sub, cid, cls_obj);
            self.apply(bound_sub, &[], None, &extra_kws, extra_kwstar, cls_site);
        }
        let mut metas = self.state.get_metaclasses(cid);
        let prog = self.prog;
        metas.sort_by(|a, b| prog.class(*a).sym.uid().cmp(prog.class(*b).sym.uid()));
        let cls_val = self.single(cls_obj);
        for meta_cid in metas {
            let meta_new = self.state.lookup_class_attr(meta_cid, name!("__new__"), None);
            if meta_new != EMPTY {
                let meta_obj = self.state.values.tok(Token::ClassObj(meta_cid));
                let bound_new = self.bind_all(meta_new, meta_cid, meta_obj);
                let mv = self.single(meta_obj);
                self.apply(bound_new, &[mv, EMPTY, bases_val, ns_val], None, &extra_kws, extra_kwstar, cls_site);
            }
            let meta_init = self.state.lookup_class_attr(meta_cid, name!("__init__"), None);
            if meta_init != EMPTY {
                self.apply(meta_init, &[cls_val, EMPTY, bases_val, ns_val], None, &extra_kws, extra_kwstar, cls_site);
            }
        }

        let mut vals = cls_val;
        for (dec, callee) in s.decorator_list.iter().zip(decs).rev() {
            vals = self.apply_decorator(dec, callee, vals);
        }
        self.bind(s.name, vals, false);
    }
}
