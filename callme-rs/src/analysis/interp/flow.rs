//! Layer 9 of the interpreter: control flow. Branches, loops, `break` / `continue`, `try`,
//! `match` and `with`, each joining the environments of its paths in flow mode.

use crate::frontend::pattern_names;
use crate::model::{EMPTY, SiteKind, Vals};
use crate::name;
use crate::syntax::ast::{Expr, For, If, Match, Stmt, Try, While, With};
use crate::syntax::walk::{NodeRef, walk};

use super::base::{End, Interp, LoopFrame};
use crate::analysis::state::{Env, join_envs};

impl Interp<'_> {
    /// Run `stmts` from a copy of `start` and return how the path ends: the environment at the
    /// end and whether a live path gets there.
    fn path(&mut self, start: &Env, stmts: &[Stmt]) -> End {
        self.env = start.clone();
        self.live = true;
        self.block(stmts);
        (std::mem::take(&mut self.env), self.live)
    }

    /// Continue after alternative paths meet, from the join of the ends that are live.
    ///
    /// If none is live, the code that follows is dead: it is analysed from the join of all the
    /// ends, and the current path stays dead.
    fn meet(&mut self, ends: Vec<End>) {
        let live: Vec<&Env> = ends.iter().filter(|(_, ok)| *ok).map(|(e, _)| e).collect();
        self.live = !live.is_empty();
        let pick: Vec<&Env> = if live.is_empty() { ends.iter().map(|(e, _)| e).collect() } else { live };
        self.env = if pick.len() == 1 { pick[0].clone() } else { join_envs(&mut self.state.values, pick) };
    }

    /// Run `if`: in flow mode both branches start from the same state, then meet.
    pub fn s_if(&mut self, s: &Stmt, x: &If) {
        if !self.flow {
            return self.generic_stmt(s);
        }
        self.eval(&x.test);
        let start = std::mem::take(&mut self.env);
        let a = self.path(&start, &x.body);
        let b = self.path(&start, &x.orelse);
        self.meet(vec![a, b]);
    }

    /// Run a loop body from the loop head until the head stops growing, then the `else` block.
    ///
    /// `enter` runs at the head before each iteration: the `while` test, or binding the `for`
    /// target. The loop is left from the final head, after the `else` block, joined with the
    /// state at every `break`; `continue` states join the head.
    ///
    /// while_test  the test of a `while`, evaluated once more before the `else` block
    fn run_loop(&mut self, body: &[Stmt], orelse: &[Stmt], while_test: Option<&Expr>, enter: &dyn Fn(&mut Self)) {
        self.loops.push(LoopFrame::default());
        let mut head = std::mem::take(&mut self.env);
        loop {
            self.env = head.clone();
            self.live = true;
            enter(self);
            self.block(body);
            let frame = self.loops.last().expect("the loop frame is on top");
            let mut parts: Vec<&Env> = vec![&head];
            if self.live {
                parts.push(&self.env);
            }
            parts.extend(frame.continues.iter());
            let grown = join_envs(&mut self.state.values, parts);
            if grown == head {
                break;
            }
            head = grown;
        }
        let frame = self.loops.pop().expect("the loop frame is on top");
        self.env = head;
        self.live = true;
        if let Some(t) = while_test {
            self.eval(t);
        }
        self.block(orelse);
        let mut ends = vec![(std::mem::take(&mut self.env), self.live)];
        ends.extend(frame.breaks.into_iter().map(|b| (b, true)));
        self.meet(ends);
    }

    /// Run `while` as a loop whose head evaluates the test.
    pub fn s_while(&mut self, s: &Stmt, x: &While) {
        if !self.flow {
            return self.generic_stmt(s);
        }
        self.run_loop(&x.body, &x.orelse, Some(&x.test), &|it: &mut Self| {
            it.eval(&x.test);
        });
    }

    /// End the current path at `break`, saving its state for the end of the loop.
    pub fn s_break(&mut self) {
        if self.flow && !self.loops.is_empty() {
            let env = self.env.clone();
            self.loops.last_mut().expect("a loop").breaks.push(env);
            self.live = false;
        }
    }

    /// End the current path at `continue`, saving its state for the loop head.
    pub fn s_continue(&mut self) {
        if self.flow && !self.loops.is_empty() {
            let env = self.env.clone();
            self.loops.last_mut().expect("a loop").continues.push(env);
            self.live = false;
        }
    }

    /// Run `try` or `try*` with its handlers, `else` block and finally block, in flow mode.
    ///
    /// Any point of the body may raise, so each handler starts from the join of every state the
    /// body passed through. The `else` block runs only after the body completes. The finally
    /// block starts from every normal end joined with every state seen in the body, the
    /// handlers and the `else` block; a `break` or `continue` inside the `try` passes through
    /// it before leaving.
    pub fn s_try(&mut self, s: &Stmt, x: &Try) {
        if !self.flow {
            return self.generic_stmt(s);
        }
        let marks = self.loops.last().map_or((0, 0), |l| (l.breaks.len(), l.continues.len()));
        let has_fin = !x.finalbody.is_empty();
        if has_fin {
            self.tries.push(self.env.clone());
        }
        self.tries.push(self.env.clone());
        self.block(&x.body);
        let caught = self.tries.pop().expect("the try accumulator is on top");
        let mut ends: Vec<End> = Vec::new();
        let body_live = self.live;
        let start = std::mem::take(&mut self.env);
        let (env, ok) = self.path(&start, &x.orelse); // else: only after the body completes
        ends.push((env, ok && body_live));
        for h in &x.handlers {
            self.env = caught.clone();
            self.live = true;
            self.handler(h);
            ends.push((std::mem::take(&mut self.env), self.live));
        }
        if !has_fin {
            self.meet(ends);
            return;
        }
        let fin = self.tries.pop().expect("the finally accumulator is on top");
        let normal: Vec<&Env> = ends.iter().filter(|(_, ok)| *ok).map(|(e, _)| e).collect();
        let has_normal = !normal.is_empty();
        let mut parts = normal;
        parts.push(&fin);
        self.env = join_envs(&mut self.state.values, parts);
        self.live = true;
        self.block(&x.finalbody);
        if self.live
            && let Some(l) = self.loops.last_mut()
        {
            // a break / continue in the try ran this finally first
            if l.breaks.len() > marks.0 {
                l.breaks.push(self.env.clone());
            }
            if l.continues.len() > marks.1 {
                l.continues.push(self.env.clone());
            }
        }
        self.live = self.live && has_normal;
    }

    /// Run `match`, binding each case's captures to the subject's value.
    ///
    /// In flow mode the cases are alternatives. A case that does not match falls through to the
    /// next one with whatever its pattern and guard already bound. The path where no case
    /// matches meets the ends of the cases.
    pub fn s_match(&mut self, x: &Match) {
        let subject = self.eval(&x.subject);
        let mut tried = self.env.clone(); // state when the next case is tried
        let mut ends: Vec<End> = Vec::new();
        for case in &x.cases {
            if self.flow {
                self.env = tried.clone();
                self.live = true;
            }
            for n in walk(NodeRef::Pattern(&case.pattern)) {
                if let NodeRef::Expr(e) = n {
                    self.eval(e); // value patterns, class names
                }
                for name in pattern_names(n) {
                    self.bind(name, subject, false); // captures: some part of the subject
                }
            }
            self.eval_opt(case.guard.as_ref());
            if self.flow {
                tried = join_envs(&mut self.state.values, [&tried, &self.env]);
            }
            self.block(&case.body);
            if self.flow {
                ends.push((std::mem::take(&mut self.env), self.live));
            }
        }
        if self.flow {
            ends.push((tried, true)); // no case matched
            self.meet(ends);
        }
    }

    /// Run `for` or `async for` over the iterable's elements, as a loop in flow mode.
    pub fn s_for(&mut self, x: &For) {
        let elems = self.iter_expr(&x.iter, x.is_async);
        if self.flow {
            self.run_loop(&x.body, &x.orelse, None, &|it: &mut Self| it.assign(&x.target, elems));
            return;
        }
        self.assign(&x.target, elems);
        self.block(&x.body);
        self.block(&x.orelse);
    }

    /// Run `with` or `async with`: call `__enter__`, run the body, then call `__exit__`.
    ///
    /// The `as` target is bound to what `__enter__` returns, or to the context manager itself
    /// when no class defines `__enter__`. In flow mode `__exit__` may swallow an exception from
    /// any point of the body, so the state after the statement also joins every state of the
    /// body.
    pub fn s_with(&mut self, x: &With) {
        let (enter_name, exit_name) = if x.is_async {
            (name!("__aenter__"), name!("__aexit__"))
        } else {
            (name!("__enter__"), name!("__exit__"))
        };
        let mut exits: Vec<(Vals, crate::model::Site)> = Vec::new();
        for item in &x.items {
            let v = self.eval(&item.context_expr);
            let site = self.site(&item.context_expr, SiteKind::With);
            let (found, entered) = self.invoke_dunder(v, enter_name, &[], site);
            exits.push((v, site));
            if let Some(target) = &item.optional_vars {
                self.assign(target, if found { entered } else { v });
            }
        }
        if self.flow {
            self.tries.push(self.env.clone());
        }
        self.block(&x.body);
        let suppressed = if self.flow { self.tries.pop() } else { None };
        for &(v, site) in exits.iter().rev() {
            self.invoke_dunder(v, exit_name, &[EMPTY, EMPTY, EMPTY], site);
        }
        if let Some(suppressed) = suppressed {
            // __exit__ may swallow an exception from anywhere in the body
            let cur = (std::mem::take(&mut self.env), self.live);
            self.meet(vec![cur, (suppressed, true)]);
        }
    }
}
