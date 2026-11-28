# callme-rs

A Rust port of [call-me-py](../README.md): context-sensitive call graphs for Python programs, by
abstract interpretation. It reads the same input, takes the same command-line options, writes
the same JSON, DOT and saved-state files, and gives the same results as the Python package in
`../callme/`. It is 15 to 160 times faster (see [Performance](#performance)).

## Build and run

```sh
cargo build --release
target/release/callme prog.py -o graph.json                          # same flags as python -m callme
target/release/callme prog.py --format dot --contexts --id-style short -o /dev/stdout
target/release/callme ../examples/project/main.py --id-style module -o graph.json
target/release/callme prog.py --save-state s.json -o a.json           # saved states work across
target/release/callme prog.py --load-state s.json --include-unreachable -o b.json   # both versions
target/release/callme prog.py --solver worklist -o graph.json         # the only Rust-only option
```

Every option of `python -m callme` is supported, parsed as `argparse` does (long options may be
abbreviated, `--opt=value` works). `--solver worklist` re-analyses only the nodes whose inputs
changed, instead of every node in every round (see [Design](#design)).

As a library:

```rust
use callme::{analyze_file, Options, Style};

let cg = analyze_file("prog.py", Options { id_style: Style::Short, ..Options::default() })?;
for (caller, callee) in cg.function_edges() {
    println!("{caller} -> {callee}");
}
for site in &cg.sites {
    println!("{} {} {:?}", site.line, site.text, cg.targets(site));
}
println!("{}", callme::to_dot(&cg, true, false));
```

`analyze_source`, `analyze_files`, `solve`, `run_passes` with the `Pass` trait, and
`save_state` / `load_state` mirror the Python API. `cargo doc --open` shows every item.

## Layout

Each Rust module keeps the name and the contents of its Python counterpart, so the two can be
read side by side. Packages only use the packages before them:
`syntax` ← `model` ← `frontend` ← `stubs` ← `analysis` ← `output`.

| Rust (`src/`) | Python (`callme/`) | Holds |
|---|---|---|
| `syntax/` | (the `ast` module) | CPython-3.13-shaped syntax trees: `lower` turns Ruff's parse tree into them with CPython's positions, `walk` traverses them in CPython's order, `unparse/` is a port of `ast.unparse` (statements, expressions, literals), `name` interns identifiers, `tables` is generated from CPython |
| `model/` | `model/` | `ids` (`FuncId`, `ClassId`, `Site`), `values` (tokens, `Vals`, contexts, nodes, and the `Values` table that interns them), `graphs` (`Edge`, CFG types) |
| `frontend/` | `frontend/` | `program`, `scopes`, `sites`, `cfg`, `builder`, `loader` |
| `stubs/` | `stubs/` | `registry` (`Registry`, `Args`, the `Host` and `HostState` traits), then one module per library, registered in the Python order |
| `analysis/` | `analysis/` | `state`, `interp/` (one `impl Interp` block per Python layer: `names`, `modules`, `calls`, `attributes`, `expressions`, `operators`, `statements`, `definitions`, `flow`), `pipeline`, `solver`; Rust only: `worklist` and its dependency keys `deps`, and `compare` (`State::same_result`) |
| `output/` | `output/` | `callgraph`, `icfg`, `export`, `persist`, and `json` (a reader and writer that match Python's `json` byte for byte) |
| `lib.rs`, `main.rs` | `__init__.py`, `__main__.py` | the public API and the CLI |
| `bin/callme-dump.rs` | | development dumps for the tools below |

## Design

- **Parsing.** Ruff's parser (`ruff_python_parser`, pinned to an exact version because its API
  is internal to Ruff) parses Python 3.13, the version the Python implementation runs on. Only
  `syntax/lower.rs` touches it. Lowering rebuilds CPython's tree: decorated definitions are
  positioned at their `def` or `class` keyword, a bare generator argument starts at the call's
  `(`, `elif` becomes a nested `If`, f-strings become `JoinedStr` / `FormattedValue`, and big
  integers become decimal digits. Ids and call sites contain line and column numbers and source
  text, so all of this has to match CPython exactly.
- **Interning.** Every token, value (a set of tokens), context and node is interned in a
  `Values` table held by the `State`, and handled as a `u32`. Two values are equal exactly when
  their handles are, so comparing environments, the fixpoint test, costs almost nothing.
  A value is a sorted token list, and unions take fast paths when one side is empty or contains
  the other. Identifiers are interned process-wide (`Name`), so comparing two is one pointer
  comparison.
- **Tables.** Per-node, per-function and per-class tables are dense vectors indexed by handle.
  Environments are insertion-ordered maps, so the Rust results are deterministic run to run.
  The `Site` of each expression is computed once and cached in the `Program`. `deref` results
  are cached until the heap grows.
- **The interpreter.** Python's `Interp` is a chain of layer classes. Here it is one struct
  with one `impl` block per layer, in files of the same names. Dispatch on node kinds is a
  `match`. Stubs are plain functions `fn(&mut dyn Host, &Args, Site) -> Vals`. `Host` is a
  trait in `stubs/` that `Interp` implements, so `stubs` never depends on `analysis`.
- **The worklist solver** (`analysis/worklist.rs`, `--solver worklist`):
  - While a node is analysed, `State` records which keys it reads (`DepKey`): return values,
    summaries, a function's contexts, class and instance attributes, the heap, the class
    hierarchy.
  - A join that grows a key queues the nodes that read it.
  - When the queue is empty, unresolved decorators are confirmed as the identity, and one full
    round confirms the fixpoint. So the solver stops on the same condition as the round
    solver.
  - It gives the round solver's results on every input checked. They can differ only where a
    function runs out of contexts, because the visiting order then decides which contexts are
    created first.

## Tests and parity

```sh
cargo test --release                             # unit tests, the example cases, the CLI
cargo test --release --test cases -- --include-ignored   # the families pytest.ini leaves out too
CALLME_FAMILIES=hof,stdlib cargo test --release --test cases
```

`tests/cases.rs` ports `tests/test_examples.py` and `tests/test_cases.py`. It checks every case
in `../examples/cases` against its docstring (`sound`, `precise`, `sites`, `flow_refines`), and
picks families from `../pytest.ini` as pytest does. On all families it passes and fails the
same cases as pytest, and it runs in about 10 ms. `CALLME_CASES` points it at another cases
directory.

The tools in `tools/` compare the port with the Python implementation (they need only Python):

| Tool | Checks |
|---|---|
| `parity.py` | both CLIs on every example entry (and harvested snippets) in six modes: JSON with the ICFG, DOT, ICFG DOT, module ids, flow-insensitive with unreachable code, and saved states (including resuming each implementation's state with the other); `wl-json` / `wl-fi` compare the worklist solver with the round solver |
| `harvest.py` | a pytest plugin that records every source the Python unit tests analyse, for `parity.py --snippets`, so new Python tests check the port too |
| `ast_parity.py` | positions, `ast.walk` order and `ast.unparse` text of every node, against CPython, on any set of files |
| `dump_program.py` | the whole `Program` (functions, classes, modules, signatures, scopes, CFGs, call sites) against the Python frontend |
| `gen_tables.py` | regenerates `src/syntax/tables.rs` from CPython |
| `bench.py` | times both implementations |

The Python version orders some lists by iterating frozensets, so their order changes with the
hash seed (`PYTHONHASHSEED`). `parity.py` therefore compares the `contexts`, `context_edges` and
ICFG `edges` lists as sorted multisets (without the `#1` / `#2` suffixes of colliding labels,
which are numbered in the same order), and every other list in order. Results at the time of
writing:

| Check | Result |
|---|---|
| `ast_parity.py`: the examples, CPython 3.13's `Lib/`, Python 3.14's `Lib/` and site-packages | 8 236 files; the only differences are 3 files with lone surrogate escapes (below) |
| `dump_program.py`: every example entry, callme itself, 150 stdlib modules | identical |
| `parity.py`: 53 example entries + 127 snippets from the Python tests × 6 modes, plus worklist vs rounds × 2 | 1440 / 1440 identical |
| `--print-graph`, 53 entries × 3 id styles | byte-identical |
| `cases.rs` vs `pytest --families all` | the same 177 passed and the same 6 failed |
| 10 PyPI packages (requests, jinja2, click, attrs, ...) | identical, except context-level details of 4 flow-insensitive runs; Python's own contexts change with the hash seed there, and the function-level results are identical |

To re-check after changing either implementation: `cargo build --release` and
`python3 tools/parity.py`. `--ref-commit REV` compares against a commit instead of the working
tree.

## Performance

Wall time, best of several runs, process start-up included (Python 3.13). Measured on the
examples and on PyPI packages, run from site-packages:

| Entry | Python | Rust (rounds) | Rust (worklist) |
|---|---|---|---|
| `examples/project/main.py` | 90 ms | 3 ms | 3 ms |
| `callme/analysis/__init__.py` (the analyzer's own package) | 697 ms | 46 ms | 45 ms |
| `requests/__init__.py` (75 modules) | 2.27 s | 119 ms | 114 ms |
| `jinja2/__init__.py` | 1.61 s | 87 ms | 82 ms |
| `attr/__init__.py` | 2.91 s | 90 ms | 70 ms |
| `jinja2`, `--flow-insensitive --include-unreachable` | 15.6 s | 344 ms | 179 ms |
| `requests`, `--flow-insensitive --include-unreachable` | 3 min 38 s | 1.99 s | 1.38 s |

## Known differences

- **Order.** Rust's output order is deterministic. Python's order of `contexts`,
  `context_edges` and the ICFG edges, and with it the `#1` / `#2` suffixes of labels that
  collide, depends on the hash seed. The order of every other list is the same.
- **Lone surrogates.** A string literal with a lone surrogate escape (`"\ud800"`) cannot be a
  Rust string, so it becomes U+FFFD, and call-site texts that show it differ.
- **Context widening.** On large programs where functions use up their context budget, which
  contexts exist depends on the visiting order. That order differs between Python's hash seeds,
  between Python and Rust, and between the two Rust solvers. The function-level graph does not
  change.
- **Errors.** Python raises an exception; Rust returns `Error`. The CLI prints the error and
  exits with status 1, or 2 for a usage error.

## Upgrading the parser

Bump the three `ruff_*` versions in `Cargo.toml`, fix `src/syntax/lower.rs` where Ruff's tree
changed, then run `tools/ast_parity.py` on a large corpus and `tools/parity.py`.
