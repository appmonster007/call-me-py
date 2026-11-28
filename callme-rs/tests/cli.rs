//! Tests of the `callme` command line: options, output files, errors and saved states.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

/// Return the path of the built `callme` binary.
fn bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_callme"))
}

/// Return a fresh temporary directory for one test, under the target directory.
fn tmp(name: &str) -> PathBuf {
    let d = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join(name);
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).expect("create the test directory");
    d
}

/// Run `callme` with `args` in `dir`.
fn run(dir: &Path, args: &[&str]) -> Output {
    Command::new(bin()).args(args).current_dir(dir).output().expect("run callme")
}

/// A small program with a call, a lambda and a class.
const PROGRAM: &str = "\
def leaf():
    return 1

def mid(f):
    return f()

class C:
    def m(self):
        return mid(leaf)

mid(lambda: leaf())
C().m()
";

/// Write `PROGRAM` as `main.py` in a new directory and return it.
fn program(name: &str) -> PathBuf {
    let d = tmp(name);
    std::fs::write(d.join("main.py"), PROGRAM).expect("write main.py");
    d
}

#[test]
fn nothing_is_written_without_output() {
    let d = program("no_output");
    let out = run(&d, &["main.py"]);
    assert!(out.status.success());
    assert!(out.stdout.is_empty());
    assert!(out.stderr.is_empty());
}

#[test]
fn json_output_lists_the_edges() {
    let d = program("json");
    let out = run(&d, &["main.py", "--short-ids", "-o", "g.json"]);
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let text = std::fs::read_to_string(d.join("g.json")).expect("the graph was written");
    let doc = callme::output::json::parse(&text).expect("valid JSON");
    let edges: Vec<(String, String)> = doc
        .get("edges")
        .map(|e| {
            e.items()
                .iter()
                .map(|x| {
                    (
                        x.get("caller").and_then(|c| c.as_str()).unwrap_or_default().to_owned(),
                        x.get("callee").and_then(|c| c.as_str()).unwrap_or_default().to_owned(),
                    )
                })
                .collect()
        })
        .unwrap_or_default();
    for want in [
        ("<module>", "mid"),
        ("mid", "leaf"),
        ("mid", "<lambda>@11"),
        ("<lambda>@11", "leaf"),
        ("<module>", "C.m"),
        ("C.m", "mid"),
    ] {
        assert!(edges.contains(&(want.0.to_owned(), want.1.to_owned())), "missing {want:?} in {edges:?}");
    }
}

#[test]
fn options_can_be_abbreviated_and_take_equals() {
    let d = program("abbrev");
    let out = run(&d, &["main.py", "--form=dot", "--cont", "--output=g.dot"]);
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let text = std::fs::read_to_string(d.join("g.dot")).expect("the graph was written");
    assert!(text.starts_with("digraph callgraph {"));
}

#[test]
fn bad_choices_and_missing_files_are_usage_errors() {
    let d = program("usage");
    let out = run(&d, &["main.py", "--format", "svg"]);
    assert_eq!(out.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&out.stderr).contains("invalid choice: 'svg'"));
    let out = run(&d, &[]);
    assert_eq!(out.status.code(), Some(2));
    let out = run(&d, &["main.py", "--co"]); // --contexts or --cont... is ambiguous? no: only --contexts
    assert!(out.status.success());
}

#[test]
fn syntax_errors_fail_with_a_message() {
    let d = tmp("syntax");
    std::fs::write(d.join("bad.py"), "def f(:\n").expect("write bad.py");
    let out = run(&d, &["bad.py", "-o", "g.json"]);
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("syntax error"));
}

#[test]
fn print_graph_writes_the_structure_to_stderr() {
    let d = program("print_graph");
    let out = run(&d, &["main.py", "--print-graph", "--id-style", "short"]);
    assert!(out.status.success());
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(err.starts_with("# function identifier graph (lexical parent -> child)\n"));
    assert!(err.contains("<module> -> mid(leaf)") || err.contains("C.m -> mid(leaf) @9:15 [call]"), "{err}");
}

#[test]
fn a_saved_state_resumes_to_the_same_graph() {
    let d = program("state");
    let first = run(&d, &["main.py", "--save-state", "s.json", "-o", "a.json"]);
    assert!(first.status.success(), "{}", String::from_utf8_lossy(&first.stderr));
    let resumed = run(&d, &["main.py", "--load-state", "s.json", "-o", "b.json"]);
    assert!(resumed.status.success(), "{}", String::from_utf8_lossy(&resumed.stderr));
    let a = std::fs::read_to_string(d.join("a.json")).expect("first graph");
    let b = std::fs::read_to_string(d.join("b.json")).expect("resumed graph");
    assert_eq!(a, b);
    // a state cannot be resumed on edited source
    std::fs::write(d.join("main.py"), format!("{PROGRAM}\nleaf()\n")).expect("edit main.py");
    let stale = run(&d, &["main.py", "--load-state", "s.json", "-o", "c.json"]);
    assert_eq!(stale.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&stale.stderr).contains("different program"));
}

#[test]
fn a_second_pass_adds_unreachable_code() {
    let d = tmp("passes");
    std::fs::write(d.join("main.py"), "def used():\n    pass\n\ndef unused():\n    used()\n\nused()\n")
        .expect("write main.py");
    assert!(run(&d, &["main.py", "--save-state", "s.json", "--short-ids", "-o", "a.json"]).status.success());
    let out = run(&d, &["main.py", "--load-state", "s.json", "--include-unreachable", "--short-ids", "-o", "b.json"]);
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let a = std::fs::read_to_string(d.join("a.json")).expect("first pass");
    let b = std::fs::read_to_string(d.join("b.json")).expect("second pass");
    assert!(!a.contains("\"caller\": \"unused\""));
    assert!(b.contains("\"caller\": \"unused\""));
}

#[test]
fn the_worklist_solver_finds_the_same_graph() {
    let d = program("worklist");
    assert!(run(&d, &["main.py", "-o", "a.json"]).status.success());
    assert!(run(&d, &["main.py", "--solver", "worklist", "-o", "b.json"]).status.success());
    let a = std::fs::read_to_string(d.join("a.json")).expect("rounds");
    let b = std::fs::read_to_string(d.join("b.json")).expect("worklist");
    assert_eq!(a, b);
}
