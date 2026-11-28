//! Check callme against the ground truth in `examples/cases` (see `examples/cases/README.md`).
//!
//! This is the Rust counterpart of `tests/test_examples.py` and `tests/test_cases.py`. Each
//! case's docstring lists `caller -> callee` edges under headers such as `EXPECTED`,
//! `STATIC_ONLY` and `SPAWN`, and optionally `caller:line -> callee` lines under `SITES`. Per
//! case:
//!
//! ```text
//!   family/case::sound          every EXPECTED edge is found
//!   family/case::precise        nothing is found outside EXPECTED | STATIC_ONLY | SPAWN
//!   family/case::sites          (cases with SITES) each listed call line resolves to exactly
//!                               the listed callees
//!   family/case::flow_refines   the flow-sensitive edges are a subset of the flow-insensitive
//!   core/case::expected, core/case::no_spurious
//!                               the `test_cases.py` checks (with unreachable code analysed)
//!   example/<file>::runs        the loose examples analyse without error
//! ```
//!
//! Configuration, by environment variable:
//!
//! ```text
//!   CALLME_CASES             the cases directory (default `../examples/cases`); its parent
//!                            holds the loose examples
//!   CALLME_PYTEST_INI        where `families` / `exclude_families` are read (default
//!                            `../pytest.ini`)
//!   CALLME_FAMILIES          families to test, comma separated, or `all`; together with
//!   CALLME_EXCLUDE_FAMILIES  these replace the pytest.ini pair, as `--families` does
//! ```
//!
//! Tests of families that are not selected are ignored; `cargo test -- --include-ignored`
//! runs them too. Tests run in parallel.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use libtest_mimic::{Arguments, Failed, Trial};

use callme::analysis::SolveOptions;
use callme::model::Style;
use callme::syntax::ast::Stmt;
use callme::syntax::lines::normalize_newlines;
use callme::{CallGraph, Options, analyze_file};

/// A (caller, callee) function edge.
type Edge = (String, String);

/// One check of a case.
type Check = fn(&Case) -> Result<(), Failed>;

/// The cases of `test_cases.py` (the supported core), by number.
const CORE: &[&str] = &["01", "02", "03", "04", "05", "06", "07", "08", "09", "12", "13", "25", "26", "27", "49"];

/// The headers whose edges `precise` allows besides EXPECTED.
const ALLOWED_EXTRA: &[&str] = &["STATIC_ONLY", "SPAWN"];

/// Return the directory of the crate.
fn crate_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// Return the cases directory.
fn cases_dir() -> PathBuf {
    std::env::var_os("CALLME_CASES").map_or_else(|| crate_dir().join("../examples/cases"), PathBuf::from)
}

/// Return Python's `inspect.cleandoc` of a docstring: common indentation and blank edge lines
/// removed.
fn cleandoc(doc: &str) -> String {
    let doc = doc.replace('\t', "        ");
    let lines: Vec<&str> = doc.split('\n').collect();
    let margin =
        lines[1..].iter().filter(|l| !l.trim().is_empty()).map(|l| l.len() - l.trim_start().len()).min().unwrap_or(0);
    let mut out: Vec<String> = Vec::new();
    out.push(lines[0].trim_start().to_owned());
    for l in &lines[1..] {
        out.push(if l.len() >= margin { l[margin..].to_owned() } else { l.trim_start().to_owned() });
    }
    while out.first().is_some_and(|l| l.trim().is_empty()) {
        out.remove(0);
    }
    while out.last().is_some_and(|l| l.trim().is_empty()) {
        out.pop();
    }
    out.join("\n")
}

/// Return the module docstring of a Python file (`ast.get_docstring`).
fn docstring(path: &Path) -> String {
    let text = normalize_newlines(&std::fs::read_to_string(path).unwrap_or_default());
    let Ok(module) = callme::syntax::parse_module(&text) else {
        return String::new();
    };
    match module.body.first() {
        Some(Stmt::Expr(e)) => e.value.as_str_constant().map(cleandoc).unwrap_or_default(),
        _ => String::new(),
    }
}

/// Return True if `line` is a block header such as `EXPECTED:` or `SITES (flow):`.
fn header(line: &str) -> Option<String> {
    let name: String = line.chars().take_while(|c| c.is_ascii_uppercase() || *c == '_').collect();
    if name.is_empty() {
        return None;
    }
    let rest = &line[name.len()..];
    // `\b` after the name: the next character is not a word character
    if rest.chars().next().is_some_and(|c| c.is_alphanumeric() || c == '_') {
        return None;
    }
    let colon = rest.find(':')?;
    rest[colon + 1..].trim().is_empty().then_some(name)
}

/// Map each docstring header (EXPECTED, STATIC_ONLY, ...) to its indented edge lines.
fn parse_blocks(doc: &str) -> BTreeMap<String, BTreeSet<Edge>> {
    let mut out: BTreeMap<String, BTreeSet<Edge>> = BTreeMap::new();
    let mut key: Option<String> = None;
    for line in doc.lines() {
        if let Some(name) = header(line) {
            out.insert(name.clone(), BTreeSet::new());
            key = Some(name);
        } else if let Some(k) = &key
            && line.starts_with([' ', '\t'])
            && line.contains(" -> ")
        {
            let (c, d) = line.trim().split_once(" -> ").unwrap_or_default();
            out.get_mut(k).expect("the block exists").insert((c.trim().to_owned(), d.trim().to_owned()));
        } else if !line.starts_with([' ', '\t']) {
            key = None;
        }
    }
    out
}

/// One test case: a single-file case (`<case>.py`) or a multi-file one (`<case>/main.py`).
#[derive(Clone)]
struct Case {
    /// the family folder
    family: String,
    /// the case file or directory
    path: PathBuf,
}

impl Case {
    /// Return the case's name: the file stem or the directory name.
    fn name(&self) -> String {
        let n = self.path.file_name().unwrap_or_default().to_string_lossy();
        n.strip_suffix(".py").unwrap_or(&n).to_owned()
    }

    /// Return the entry file.
    fn entry(&self) -> PathBuf {
        if self.path.is_dir() { self.path.join("main.py") } else { self.path.clone() }
    }

    /// Return the docstring blocks of the entry file.
    fn blocks(&self) -> BTreeMap<String, BTreeSet<Edge>> {
        parse_blocks(&docstring(&self.entry()))
    }

    /// Analyse the case, with module-qualified names for a multi-file case.
    fn callgraph(&self, flow: bool, include_unreachable: bool) -> Result<CallGraph, Failed> {
        let style = if self.path.is_dir() { Style::Module } else { Style::Short };
        let opts = Options {
            id_style: style,
            solve: SolveOptions { flow_sensitive: Some(flow), include_unreachable, ..SolveOptions::default() },
            ..Options::default()
        };
        analyze_file(&self.entry().to_string_lossy(), opts).map_err(|e| Failed::from(format!("analysis failed: {e}")))
    }

    /// Return the function edges of the analysis.
    fn edges(&self, flow: bool) -> Result<BTreeSet<Edge>, Failed> {
        Ok(self.callgraph(flow, false)?.function_edges())
    }
}

/// Return every case, by family folder and name.
fn cases(dir: &Path) -> Vec<Case> {
    let mut out = Vec::new();
    let Ok(families) = std::fs::read_dir(dir) else {
        return out;
    };
    let mut fams: Vec<PathBuf> = families.flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect();
    fams.sort();
    for fam in fams {
        let fname = fam.file_name().unwrap_or_default().to_string_lossy().into_owned();
        if fname.starts_with(['.', '_']) {
            continue;
        }
        let mut items: Vec<PathBuf> =
            std::fs::read_dir(&fam).map(|r| r.flatten().map(|e| e.path()).collect()).unwrap_or_default();
        items.sort();
        for p in items {
            let is_case =
                if p.is_file() { p.extension().is_some_and(|x| x == "py") } else { p.join("main.py").is_file() };
            if is_case {
                out.push(Case { family: fname.clone(), path: p });
            }
        }
    }
    out
}

/// Return the selected families from pytest.ini and the environment; None means all.
fn selection(known: &[String]) -> BTreeSet<String> {
    let names = |s: &str| s.split([',', ' ', '\t']).filter(|x| !x.is_empty()).map(str::to_owned).collect::<Vec<_>>();
    let cli_inc = std::env::var("CALLME_FAMILIES").ok();
    let cli_exc = std::env::var("CALLME_EXCLUDE_FAMILIES").ok();
    let (include, exclude) = if cli_inc.is_some() || cli_exc.is_some() {
        (names(cli_inc.as_deref().unwrap_or("all")), names(cli_exc.as_deref().unwrap_or("")))
    } else {
        let ini =
            std::env::var_os("CALLME_PYTEST_INI").map_or_else(|| crate_dir().join("../pytest.ini"), PathBuf::from);
        let text = std::fs::read_to_string(ini).unwrap_or_default();
        let value = |key: &str| {
            text.lines()
                .filter_map(|l| l.split_once('='))
                .find(|(k, _)| k.trim() == key)
                .map(|(_, v)| names(v))
                .unwrap_or_default()
        };
        let inc = value("families");
        (if inc.is_empty() { vec!["all".into()] } else { inc }, value("exclude_families"))
    };
    let inc: BTreeSet<String> = if include.iter().any(|n| n == "all") {
        known.iter().cloned().collect()
    } else {
        include.into_iter().collect()
    };
    let exc: BTreeSet<String> = if exclude.iter().any(|n| n == "all") {
        known.iter().cloned().collect()
    } else {
        exclude.into_iter().collect()
    };
    inc.difference(&exc).cloned().collect()
}

/// Return a block of edges for a failure message.
fn fmt(title: &str, edges: &BTreeSet<Edge>) -> String {
    let lines: Vec<String> = edges.iter().map(|(c, d)| format!("  {c} -> {d}")).collect();
    format!("{title}:\n{}", if lines.is_empty() { "  (none)".into() } else { lines.join("\n") })
}

/// Check that every EXPECTED edge is found.
fn sound(case: &Case) -> Result<(), Failed> {
    let expected = case.blocks().remove("EXPECTED").unwrap_or_default();
    let got = case.edges(true)?;
    let missing: BTreeSet<Edge> = expected.difference(&got).cloned().collect();
    if missing.is_empty() {
        return Ok(());
    }
    Err(format!(
        "{}: {} of {} expected edges missing\n{}\n{}",
        case.name(),
        missing.len(),
        expected.len(),
        fmt("missing", &missing),
        fmt("found", &got)
    )
    .into())
}

/// Check that nothing is found outside EXPECTED | STATIC_ONLY | SPAWN.
fn precise(case: &Case) -> Result<(), Failed> {
    let blocks = case.blocks();
    let mut allowed = blocks.get("EXPECTED").cloned().unwrap_or_default();
    for k in ALLOWED_EXTRA {
        allowed.extend(blocks.get(*k).cloned().unwrap_or_default());
    }
    let got = case.edges(true)?;
    let extra: BTreeSet<Edge> = got.difference(&allowed).cloned().collect();
    if extra.is_empty() {
        return Ok(());
    }
    Err(format!(
        "{}: {} edges outside EXPECTED | {}\n{}\n{}",
        case.name(),
        extra.len(),
        ALLOWED_EXTRA.join(" | "),
        fmt("extra", &extra),
        fmt("allowed", &allowed)
    )
    .into())
}

/// Check that each SITES call line resolves to exactly the listed callees.
fn sites(case: &Case) -> Result<(), Failed> {
    let mut want: BTreeMap<(String, u32), BTreeSet<String>> = BTreeMap::new();
    for (at, callee) in case.blocks().remove("SITES").unwrap_or_default() {
        let (caller, line) = at.rsplit_once(':').ok_or_else(|| Failed::from(format!("bad SITES line {at}")))?;
        let line: u32 = line.parse().map_err(|_| Failed::from(format!("bad SITES line {at}")))?;
        want.entry((caller.to_owned(), line)).or_default().insert(callee);
    }
    let cg = case.callgraph(true, false)?;
    let mut wrong = Vec::new();
    for ((caller, line), callees) in &want {
        let mut got = BTreeSet::new();
        for s in &cg.sites {
            if cg.name(s.caller) == caller && s.line == *line {
                got.extend(cg.targets(s));
            }
        }
        if &got != callees {
            wrong.push(format!("  {caller}:{line}  expected {callees:?}, found {got:?}"));
        }
    }
    if wrong.is_empty() {
        return Ok(());
    }
    Err(format!("{}: {} call lines resolve differently\n{}", case.name(), wrong.len(), wrong.join("\n")).into())
}

/// Check that flow sensitivity only removes edges.
fn flow_refines(case: &Case) -> Result<(), Failed> {
    let fs = case.edges(true)?;
    let fi = case.edges(false)?;
    let only: BTreeSet<Edge> = fs.difference(&fi).cloned().collect();
    if only.is_empty() {
        return Ok(());
    }
    Err(format!(
        "{}: flow-sensitive edges missing from the flow-insensitive graph\n{}",
        case.name(),
        fmt("only flow-sensitive", &only)
    )
    .into())
}

/// Return the `test_cases.py` expectation of a core case: EXPECTED | STATIC_ONLY.
fn core_allowed(case: &Case) -> BTreeSet<Edge> {
    let blocks = case.blocks();
    let mut allowed = blocks.get("EXPECTED").cloned().unwrap_or_default();
    allowed.extend(blocks.get("STATIC_ONLY").cloned().unwrap_or_default());
    allowed
}

/// `test_expected_edges_are_found`: with unreachable code analysed, every allowed edge is found.
fn core_expected(case: &Case) -> Result<(), Failed> {
    let expected = core_allowed(case);
    let got = case.callgraph(true, true)?.function_edges();
    let missing: Vec<&Edge> = expected.difference(&got).collect();
    if missing.is_empty() { Ok(()) } else { Err(format!("missing: {missing:?}").into()) }
}

/// `test_no_spurious_edges_beyond_static_only`: nothing beyond EXPECTED | STATIC_ONLY.
fn core_no_spurious(case: &Case) -> Result<(), Failed> {
    let allowed = core_allowed(case);
    let got = case.callgraph(true, true)?.function_edges();
    let extra: Vec<&Edge> = got.difference(&allowed).collect();
    if extra.is_empty() { Ok(()) } else { Err(format!("extra: {extra:?}").into()) }
}

fn main() {
    let args = Arguments::from_args();
    let dir = cases_dir();
    let all = cases(&dir);
    let known: Vec<String> = all.iter().map(|c| c.family.clone()).collect::<BTreeSet<_>>().into_iter().collect();
    let selected = selection(&known);
    let mut trials: Vec<Trial> = Vec::new();
    for case in &all {
        let off = !selected.contains(&case.family);
        let base = format!("{}/{}", case.family, case.name());
        let blocks = case.blocks();
        let has_expected = blocks.contains_key("EXPECTED");
        let checks: Vec<(&str, Check)> = if has_expected {
            let mut v: Vec<(&str, Check)> = vec![("sound", sound), ("precise", precise)];
            if blocks.contains_key("SITES") {
                v.push(("sites", sites));
            }
            v.push(("flow_refines", flow_refines));
            v
        } else {
            Vec::new()
        };
        for (check, f) in checks {
            let c = case.clone();
            trials.push(
                Trial::test(format!("{base}::{check}"), move || f(&c))
                    .with_kind(case.family.clone())
                    .with_ignored_flag(off),
            );
        }
        let number = case.name().split('_').next().unwrap_or_default().to_owned();
        if CORE.contains(&number.as_str()) && case.path.is_file() {
            for (check, f) in [("expected", core_expected as Check), ("no_spurious", core_no_spurious)] {
                let c = case.clone();
                trials.push(Trial::test(format!("core/{}::{check}", case.name()), move || f(&c)));
            }
        }
    }
    let examples = dir.parent().map(Path::to_path_buf).unwrap_or_default();
    let mut loose: Vec<PathBuf> = std::fs::read_dir(&examples)
        .map(|r| r.flatten().map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "py")).collect())
        .unwrap_or_default();
    let mut mains: Vec<PathBuf> = std::fs::read_dir(&examples)
        .map(|r| {
            r.flatten()
                .map(|e| e.path())
                .filter(|p| p.is_dir() && *p != dir && p.join("main.py").is_file())
                .map(|p| p.join("main.py"))
                .collect()
        })
        .unwrap_or_default();
    loose.sort();
    mains.sort();
    loose.extend(mains);
    for p in loose {
        let name = p.strip_prefix(&examples).unwrap_or(&p).to_string_lossy().into_owned();
        trials.push(Trial::test(format!("example/{name}::runs"), move || {
            analyze_file(&p.to_string_lossy(), Options::default()).map(|_| ()).map_err(|e| Failed::from(e.to_string()))
        }));
    }
    libtest_mimic::run(&args, trials).exit();
}
