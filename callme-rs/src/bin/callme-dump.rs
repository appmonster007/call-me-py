//! `callme-dump`: development dumps that `tools/*.py` compare with the Python implementation.
//!
//! Usage:
//!
//! ```text
//!   callme-dump ast FILE...       every node's type and position (in `ast.walk` order), the
//!                                 first line of every statement's unparse, and the unparse
//!                                 of every call and decorator; each file's dump starts with
//!                                 a line `== FILE`, and a file that does not parse dumps `E`
//!   callme-dump trace ENTRY [ROUNDS]
//!                                 per round of the fixpoint: nodes, edges, interned tokens and
//!                                 values, and the table sizes, to see what keeps growing
//!   callme-dump program ENTRY [--path DIR]...
//!                                 the `Program` of an entry file: modules, functions,
//!                                 classes, CFGs and call sites (`tools/dump_program.py`)
//! ```

use std::io::{self, BufWriter, Write};
use std::process::ExitCode;

use callme::frontend::{Program, call_sites, parse_files};
use callme::syntax::Name;
use callme::syntax::ast::{Expr, Stmt};
use callme::syntax::lines::normalize_newlines;
use callme::syntax::walk::{NodeRef, walk};
use callme::syntax::{parse_module, unparse};

/// Escape backslashes and newlines so that every dumped text fits on one line.
fn one_line(s: &str) -> String {
    s.replace('\\', "\\\\").replace('\n', "\\n")
}

/// Write the `ast` dump of the file at `path`.
fn dump_ast(path: &str, out: &mut impl Write) -> io::Result<()> {
    writeln!(out, "== {path}")?;
    let text = match std::fs::read_to_string(path) {
        Ok(t) => normalize_newlines(&t),
        Err(_) => return writeln!(out, "E"),
    };
    let Ok(module) = parse_module(&text) else {
        return writeln!(out, "E");
    };
    let nodes = walk(NodeRef::Module(&module));
    for n in &nodes {
        match n.pos() {
            Some(p) => writeln!(out, "N {} {}:{}", n.type_name(), p.line, p.col)?,
            None => writeln!(out, "N {}", n.type_name())?,
        }
    }
    for n in &nodes {
        match n {
            NodeRef::Stmt(s) => {
                let p = s.pos();
                writeln!(out, "S {}:{} {}", p.line, p.col, one_line(&unparse::stmt_first_line(s, None)))?;
                let decorators = match s {
                    Stmt::FunctionDef(f) => &f.decorator_list[..],
                    Stmt::ClassDef(c) => &c.decorator_list[..],
                    _ => &[],
                };
                for d in decorators {
                    let p = d.pos();
                    writeln!(out, "D {}:{} {}", p.line, p.col, one_line(&unparse::expr(d)))?;
                }
            }
            NodeRef::Expr(e @ Expr::Call(c)) => {
                writeln!(out, "C {}:{} {}", c.pos.line, c.pos.col, one_line(&unparse::expr(e)))?;
            }
            _ => {}
        }
    }
    Ok(())
}

/// Return the names sorted and joined with commas.
fn sorted<'a>(names: impl IntoIterator<Item = &'a Name>) -> String {
    let mut v: Vec<&str> = names.into_iter().map(|n| n.as_str()).collect();
    v.sort_unstable();
    v.join(",")
}

/// Return a Python-style optional: the value, or "None".
fn opt(s: Option<&str>) -> &str {
    s.unwrap_or("None")
}

/// Return "True" or "False".
fn py_bool(b: bool) -> &'static str {
    if b { "True" } else { "False" }
}

/// Write the `program` dump of `prog`.
fn dump_program(prog: &Program, out: &mut impl Write) -> io::Result<()> {
    let uid = |f: callme::model::FuncId| prog.func(f).sym.uid().to_owned();
    writeln!(out, "fingerprint {}", prog.fingerprint)?;
    writeln!(out, "file {}", prog.file)?;
    writeln!(out, "module {}", uid(prog.module))?;
    let entries: Vec<String> = prog.entries.iter().map(|&e| uid(e)).collect();
    writeln!(out, "entries {}", entries.join(" "))?;
    for (name, m) in prog.modules_by_name() {
        writeln!(
            out,
            "M {name} file={} func={} package={} exports={}",
            opt(m.file.as_deref()),
            opt(m.func.map(uid).as_deref()),
            py_bool(m.package),
            sorted(&m.exports)
        )?;
    }
    for f in &prog.funcs {
        let sig = &f.sig;
        let join = |v: &[Name]| v.iter().map(|n| n.as_str()).collect::<Vec<_>>().join(",");
        writeln!(
            out,
            "F {} module={} kind={} parent={} owner={}",
            f.sym.uid(),
            f.sym.module,
            f.kind.as_str(),
            opt(f.parent.map(uid).as_deref()),
            opt(f.owner.map(|c| prog.class(c).sym.uid().to_owned()).as_deref())
        )?;
        let defaults: Vec<Name> = sig.defaults.iter().map(|(n, _)| *n).collect();
        writeln!(
            out,
            "  sig pos={} kwonly={} vararg={} kwarg={} defaults={} required={}",
            join(&sig.pos),
            join(&sig.kwonly),
            opt(sig.vararg.map(|n| n.as_str())),
            opt(sig.kwarg.map(|n| n.as_str())),
            join(&defaults),
            join(&sig.required)
        )?;
        writeln!(
            out,
            "  locals={} globals={} nonlocals={} must={}",
            sorted(&f.locals),
            sorted(&f.globals),
            sorted(&f.nonlocals),
            sorted(&f.must_writes)
        )?;
        let cfg = prog.cfg(f.id);
        for n in &cfg.nodes {
            let site = n.site.map(|s| format!(" site={}|{}", s.text, s.kind.as_str())).unwrap_or_default();
            writeln!(
                out,
                "  P {} {} {}:{} {}{site}",
                n.idx,
                n.kind.as_str(),
                n.line,
                n.col,
                unparse::str_repr(&n.text)
            )?;
        }
        for e in &cfg.edges {
            writeln!(out, "  E {}->{} {}", e.src, e.dst, e.kind.as_str())?;
        }
        for (site, (c, r)) in &cfg.calls {
            writeln!(
                out,
                "  K {}:{} {} {} {c} {r}",
                site.line,
                site.col,
                site.kind.as_str(),
                unparse::str_repr(&site.text)
            )?;
        }
        for site in call_sites(prog, f.id) {
            writeln!(out, "  S {}:{} {} {}", site.line, site.col, site.kind.as_str(), unparse::str_repr(&site.text))?;
        }
    }
    for c in &prog.classes {
        writeln!(
            out,
            "C {} module={} parent={} known={}",
            c.sym.uid(),
            c.sym.module,
            uid(c.parent),
            sorted(&c.known_attrs)
        )?;
    }
    Ok(())
}

/// Run `callme-dump program ENTRY [--path DIR]...`.
fn program(args: &[String], out: &mut impl Write) -> io::Result<()> {
    let mut entries = Vec::new();
    let mut path = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        if a == "--path" {
            path.extend(it.next().cloned());
        } else {
            entries.push(a.clone());
        }
    }
    for e in entries {
        match parse_files(&[e], &path) {
            Ok(prog) => dump_program(&prog, out)?,
            Err(callme::Error::Syntax { .. }) => writeln!(out, "E")?,
            Err(err) => writeln!(out, "ERROR {err}")?,
        }
    }
    Ok(())
}

/// Run `callme-dump trace ENTRY [ROUNDS]`.
fn trace(args: &[String], out: &mut impl Write) -> io::Result<()> {
    let Some(entry) = args.first() else {
        return writeln!(out, "usage: callme-dump trace ENTRY [ROUNDS]");
    };
    let rounds: usize = args.get(1).and_then(|r| r.parse().ok()).unwrap_or(200);
    let prog = match parse_files(std::slice::from_ref(entry), &[]) {
        Ok(p) => p,
        Err(e) => return writeln!(out, "ERROR {e}"),
    };
    let opts = callme::analysis::SolveOptions { max_rounds: rounds, ..Default::default() };
    let mut lines: Vec<String> = Vec::new();
    let start = std::time::Instant::now();
    let mut hook = |rnd: usize, s: &callme::State| {
        let heap: usize = s.heap.iter().map(|(_, &v)| s.values.len(v)).sum();
        let inst: usize = s.inst_attrs.iter().map(|(_, e)| e.len()).sum();
        let cls: usize = s.class_attrs.iter().map(|(_, e)| e.len()).sum();
        let env_size: usize = s.envs.iter().map(|(_, e)| e.values().map(|&v| s.values.len(v)).sum::<usize>()).sum();
        let line = format!(
            "{:8.2}s round {rnd}: nodes {} edges {} tokens {} vals {} heap-toks {heap} inst-attrs {inst} class-attrs {cls} env-toks {env_size}",
            start.elapsed().as_secs_f64(),
            s.order.len(),
            s.edges.len(),
            s.values.token_count(),
            s.values.vals_count()
        );
        eprintln!("{line}");
        lines.push(line);
    };
    let mut last: Option<callme::State> = None;
    let r = callme::solve(
        &prog,
        None,
        &opts,
        Some(&mut |rnd: usize, s: &callme::State| {
            hook(rnd, s);
            if rnd + 1 == rounds {
                last = Some(s.clone());
            }
        }),
    );
    for l in lines {
        writeln!(out, "{l}")?;
    }
    if let Some(s) = last {
        let n = s.values.token_count();
        for i in n.saturating_sub(6)..n {
            let t = callme::model::TokId(u32::try_from(i).unwrap_or(0));
            writeln!(out, "token {i} depth {}: {}", s.values.depth(t), s.values.label(&prog, t, callme::Style::Short))?;
        }
    }
    writeln!(out, "{}", if r.is_ok() { "converged" } else { "did not converge" })
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let result = match args.split_first() {
        Some((cmd, files)) if cmd == "ast" => files.iter().try_for_each(|f| dump_ast(f, &mut out)),
        Some((cmd, rest)) if cmd == "program" => program(rest, &mut out),
        Some((cmd, rest)) if cmd == "trace" => trace(rest, &mut out),
        _ => {
            eprintln!("usage: callme-dump ast FILE... | callme-dump program ENTRY [--path DIR]...");
            return ExitCode::from(2);
        }
    };
    match result.and_then(|()| out.flush()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("callme-dump: {e}");
            ExitCode::FAILURE
        }
    }
}
