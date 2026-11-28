//! Command-line interface for callme (`callme FILE...`), the counterpart of `python -m callme`.
//!
//! Parses the options, runs the call graph analysis on one or more Python entry files and the
//! project modules they import, and writes the result as JSON, Graphviz DOT, or an
//! interprocedural CFG (ICFG) DOT graph. Nothing is written unless `-o`/`--output` is given
//! (except `--print-graph` to stderr).
//!
//! The options are those of the Python version, parsed the way `argparse` does (long options
//! may be abbreviated to a unique prefix, and take their value as `--opt V` or `--opt=V`).
//! `--solver worklist` is the only option the Python version does not have.

use std::io::Write;
use std::process::ExitCode;

use callme::analysis::{SolveOptions, Solver};
use callme::frontend::{call_sites, parse_files};
use callme::model::Style;
use callme::{CallGraph, Options, analyze_files, load_state, save_state, to_dot, to_icfg_dot, to_json};

/// The usage line.
const USAGE: &str = "usage: callme [-h] [--path DIR] [--format {json,dot,icfg}] [--icfg] [--contexts] [--returns]
              [--include-unreachable] [--id-style {full,module,short}] [--short-ids]
              [--print-graph] [--max-contexts MAX_CONTEXTS] [--flow-insensitive]
              [--load-state PATH] [--save-state PATH] [--solver {rounds,worklist}]
              [-o OUTPUT]
              FILE [FILE ...]";

/// The help text after the usage line.
const HELP: &str = "
Context-sensitive call graphs for Python programs. Imported modules are looked up in each
FILE's root directory, then in every `--path DIR`. Multi-pass from the shell: `--save-state`
writes the solver state to JSON and `--load-state` resumes it for the same, unchanged files
(e.g. a second pass with `--include-unreachable`).

positional arguments:
  FILE                  entry file; the project modules it imports are analysed too

options:
  -h, --help            show this help message and exit
  --path DIR            also search DIR for imported modules (repeatable, like PYTHONPATH)
  --format {json,dot,icfg}
                        icfg = DOT of the interprocedural CFG (one cluster per context)
  --icfg                JSON: include CFGs and call/return edges
  --contexts            DOT: one node per (function, context)
  --returns             DOT: add dotted return edges
  --include-unreachable
                        also analyse functions never called and modules never imported
  --id-style {full,module,short}
                        function id text: file:line:col:qualname | module.qualname | qualname
  --short-ids           same as --id-style short
  --print-graph         print the intermediate graph (nesting + call-site edges, source ->
                        target) to stderr
  --max-contexts MAX_CONTEXTS
                        context budget per function (default 16, or the loaded state's)
  --flow-insensitive    treat each local as the union of all its bindings (less precise;
                        default: flow-sensitive, or the loaded state's mode)
  --load-state PATH     resume from a state saved for these files
  --save-state PATH     save the solver state after this pass
  --solver {rounds,worklist}
                        fixpoint algorithm: rounds (as the Python version) or worklist
                        (re-analyse only nodes whose inputs changed)
  -o, --output OUTPUT   write the graph here; without it nothing is printed";

/// The parsed command line.
#[derive(Default)]
struct Cli {
    /// entry files
    files: Vec<String>,
    /// extra module search directories
    path: Vec<String>,
    /// output format: json, dot or icfg
    format: String,
    /// JSON: include the ICFG
    icfg: bool,
    /// DOT: one node per context
    contexts: bool,
    /// DOT: add return edges
    returns: bool,
    /// analyse never-called code too
    include_unreachable: bool,
    /// id style
    id_style: Style,
    /// print the program structure to stderr
    print_graph: bool,
    /// context budget
    max_contexts: Option<usize>,
    /// flow mode, if given
    flow_sensitive: Option<bool>,
    /// state to resume
    load_state: Option<String>,
    /// where to save the final state
    save_state: Option<String>,
    /// fixpoint algorithm
    solver: Solver,
    /// output file
    output: Option<String>,
}

/// The long options: (name, takes a value).
const LONG: &[(&str, bool)] = &[
    ("--help", false),
    ("--path", true),
    ("--format", true),
    ("--icfg", false),
    ("--contexts", false),
    ("--returns", false),
    ("--include-unreachable", false),
    ("--id-style", true),
    ("--short-ids", false),
    ("--print-graph", false),
    ("--max-contexts", true),
    ("--flow-insensitive", false),
    ("--load-state", true),
    ("--save-state", true),
    ("--solver", true),
    ("--output", true),
];

/// A command-line error: print usage and the message, exit with status 2.
struct Usage(String);

/// Return the full name of long option `arg`, which may be a unique prefix (as in argparse).
fn resolve_long(arg: &str) -> Result<(&'static str, bool), Usage> {
    if let Some(&(n, v)) = LONG.iter().find(|(n, _)| *n == arg) {
        return Ok((n, v));
    }
    let matches: Vec<&(&str, bool)> = LONG.iter().filter(|(n, _)| n.starts_with(arg)).collect();
    match matches[..] {
        [&(n, v)] => Ok((n, v)),
        [] => Err(Usage(format!("unrecognized arguments: {arg}"))),
        _ => {
            let names: Vec<&str> = matches.iter().map(|(n, _)| *n).collect();
            Err(Usage(format!("ambiguous option: {arg} could match {}", names.join(", "))))
        }
    }
}

/// Check that `value` is one of `choices` for option `opt`.
fn choice(opt: &str, value: &str, choices: &[&str]) -> Result<(), Usage> {
    if choices.contains(&value) {
        return Ok(());
    }
    let quoted: Vec<String> = choices.iter().map(|c| format!("'{c}'")).collect();
    Err(Usage(format!("argument {opt}: invalid choice: '{value}' (choose from {})", quoted.join(", "))))
}

/// Parse the arguments after the program name.
fn parse_args(args: &[String]) -> Result<Option<Cli>, Usage> {
    let mut cli = Cli { format: "json".into(), ..Cli::default() };
    let mut i = 0;
    let mut only_files = false;
    while i < args.len() {
        let arg = &args[i];
        i += 1;
        if only_files || !arg.starts_with('-') || arg == "-" {
            cli.files.push(arg.clone());
            continue;
        }
        if arg == "--" {
            only_files = true;
            continue;
        }
        let (name, inline): (&str, Option<String>) = if arg == "-h" {
            ("--help", None)
        } else if let Some(rest) = arg.strip_prefix("-o") {
            if rest.is_empty() {
                ("--output", None)
            } else {
                ("--output", Some(rest.trim_start_matches('=').to_owned()))
            }
        } else {
            let (opt, val) = match arg.split_once('=') {
                Some((o, v)) => (o, Some(v.to_owned())),
                None => (arg.as_str(), None),
            };
            let (n, takes) = resolve_long(opt)?;
            if !takes && val.is_some() {
                return Err(Usage(format!("argument {n}: ignored explicit argument '{}'", val.unwrap_or_default())));
            }
            (n, val)
        };
        let takes = LONG.iter().any(|(n, v)| *n == name && *v);
        let value = if takes {
            match inline {
                Some(v) => v,
                None if i < args.len() => {
                    i += 1;
                    args[i - 1].clone()
                }
                None => return Err(Usage(format!("argument {name}: expected one argument"))),
            }
        } else {
            String::new()
        };
        match name {
            "--help" => return Ok(None),
            "--path" => cli.path.push(value),
            "--format" => {
                choice(name, &value, &["json", "dot", "icfg"])?;
                cli.format = value;
            }
            "--icfg" => cli.icfg = true,
            "--contexts" => cli.contexts = true,
            "--returns" => cli.returns = true,
            "--include-unreachable" => cli.include_unreachable = true,
            "--id-style" => {
                choice(name, &value, &["full", "module", "short"])?;
                cli.id_style = Style::parse(&value).unwrap_or_default();
            }
            "--short-ids" => cli.id_style = Style::Short,
            "--print-graph" => cli.print_graph = true,
            "--max-contexts" => {
                cli.max_contexts = Some(
                    value
                        .parse()
                        .map_err(|_| Usage(format!("argument --max-contexts: invalid int value: '{value}'")))?,
                );
            }
            "--flow-insensitive" => cli.flow_sensitive = Some(false),
            "--load-state" => cli.load_state = Some(value),
            "--save-state" => cli.save_state = Some(value),
            "--solver" => {
                choice(name, &value, &["rounds", "worklist"])?;
                cli.solver = if value == "worklist" { Solver::Worklist } else { Solver::Rounds };
            }
            "--output" => cli.output = Some(value),
            _ => unreachable!("every long option is handled"),
        }
    }
    if cli.files.is_empty() {
        return Err(Usage("the following arguments are required: FILE".into()));
    }
    Ok(Some(cli))
}

/// Write the program's structure, as `cg` names it, to stderr.
///
/// Prints two lists in `cg`'s id style: the lexical nesting of functions (`parent -> child`),
/// then every syntactic call site (`caller -> text @line:col [kind]`).
fn print_graph(cg: &CallGraph) {
    let mut err = std::io::stderr().lock();
    let prog = &cg.prog;
    let _ = writeln!(err, "# function identifier graph (lexical parent -> child)");
    for (parent, child) in prog.nesting_edges() {
        let _ = writeln!(err, "{} -> {}", cg.name(parent), cg.name(child));
    }
    let _ = writeln!(err, "# call sites (caller -> call site @line:col)");
    for f in &prog.funcs {
        for s in call_sites(prog, f.id) {
            let _ = writeln!(err, "{} -> {} @{}:{} [{}]", cg.name(f.id), s.text, s.line, s.col, s.kind.as_str());
        }
    }
}

/// Analyse the files and write the requested outputs.
fn run(cli: Cli) -> callme::Result<()> {
    let opts = SolveOptions {
        include_unreachable: cli.include_unreachable,
        max_contexts: cli.max_contexts,
        flow_sensitive: cli.flow_sensitive,
        solver: cli.solver,
        ..SolveOptions::default()
    };
    let cg = if let Some(path) = &cli.load_state {
        let prog = parse_files(&cli.files, &cli.path)?;
        let state = load_state(&prog, path)?;
        let state = callme::solve(&prog, Some(state), &opts, None)?;
        callme::output::build_callgraph(prog, state, cli.id_style)
    } else {
        analyze_files(
            &cli.files,
            Options { id_style: cli.id_style, search_path: cli.path.clone(), state: None, solve: opts },
        )?
    };
    if let Some(path) = &cli.save_state {
        save_state(&cg.state, &cg.prog, path)?;
    }
    if cli.print_graph {
        print_graph(&cg);
    }
    if let Some(out) = &cli.output {
        // without -o nothing is written or printed (except --print-graph)
        let text = match cli.format.as_str() {
            "json" => to_json(&cg, 2, cli.icfg),
            "icfg" => to_icfg_dot(&cg),
            _ => to_dot(&cg, cli.contexts, cli.returns),
        };
        std::fs::write(out, text).map_err(|err| callme::Error::Io { path: out.clone(), err })?;
    }
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match parse_args(&args) {
        Ok(None) => {
            println!("{USAGE}\n{HELP}");
            ExitCode::SUCCESS
        }
        Ok(Some(cli)) => match run(cli) {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("callme: error: {e}");
                ExitCode::FAILURE
            }
        },
        Err(Usage(msg)) => {
            eprintln!("{USAGE}\ncallme: error: {msg}");
            ExitCode::from(2)
        }
    }
}
