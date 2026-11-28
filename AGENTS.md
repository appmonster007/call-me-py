# AGENTS.md

call-me-py builds context-sensitive call graphs for Python programs by abstract
interpretation, following imports from the entry files into every project module. The package is `callme/`, it uses only the standard library, and it needs
Python ≥ 3.12. Read [README.md](README.md) for an overview and `docs/` for details. The docs
are written so that you rarely need to read the interpreter in full, and every function's
docstring states its contract, so reading a definition's first lines is usually enough.

## Commands (run from the repo root)

```sh
pytest                                    # the families selected in pytest.ini
pytest --families all -k sound -x         # every family, soundness checks only
python -m callme FILE -o out.json         # nothing is printed without -o
python -m callme FILE --format dot --contexts --id-style short -o /dev/stdout
python -m callme examples/project/main.py --id-style module -o out.json  # a multi-file program
python info/build.py                      # after changing callme/: regenerate info/generated/data.js
```

## Map

| Package | What it holds |
|---|---|
| `callme/model/` | every data type: `ids.py` (ids, `Site`), `values.py` (tokens, `Vals`, `Ctx`, `Node`), `graphs.py` (`Edge`, CFG types) |
| `callme/frontend/` | loading files into a `Program`: `loader.py` (finding files, module names), `builder.py` (ids, star imports), `scopes.py`, `sites.py`, `cfg.py` (one CFG per function), `program.py` (the types) |
| `callme/stubs/` | models of builtin and stdlib functions: `registry.py` (`@stub`, `Args`, `Host`), then one module per library (`builtins.py`, `functools.py`, `asyncio.py`, ...) |
| `callme/analysis/` | `state.py` (`State`, the global tables), `interp/` (`Interp`, the abstract interpreter, one layer per module), `pipeline.py` (multi-pass driver), `solver.py` (the call graph pass, `solve`) |
| `callme/output/` | `callgraph.py` (the `CallGraph` result), `icfg.py`, `export.py` (JSON / DOT), `persist.py` (saving and loading state) |
| `callme/__init__.py`, `__main__.py` | public API, CLI |
| `callme-rs/` | the Rust port, module for module (see `callme-rs/README.md`); after a behaviour change in `callme/`, port it and run `python3 callme-rs/tools/parity.py` |

`Interp` is a chain of classes, each subclassing the one below: `base.py` → `names.py` →
`modules.py` → `calls.py` → `attributes.py` → `expressions.py` → `operators.py` →
`statements.py` → `definitions.py` → `flow.py` → `Interp` in `interp/__init__.py`. A layer
may call any method of the layers below it. `eval` and `apply` are also called from layers
below the ones that define them, so `BaseLayer` declares them as abstract methods.

| Need | Read |
|---|---|
| where a feature lives, types, `State` fields | [docs/architecture.md](docs/architecture.md) ("Where to look" table) |
| why a call resolves the way it does | [docs/analysis.md](docs/analysis.md) |
| CLI flags, JSON / DOT / state layouts | [docs/output.md](docs/output.md) |
| adding a stub, syntax, a pass or a test case | [docs/extending.md](docs/extending.md) |
| test case docstring format | [examples/cases/README.md](examples/cases/README.md) |

## Rules

- `State` only grows: every write is a join. Re-solving a fixpoint must not change it
  (`State.same_result`), and a pass's `init` is idempotent.
- Every syntactic call, meaning an `ast.Call` or a decorator, is both a CFG call point
  (`cfg.Builder.call_point`) and a `Site` from `frontend.make_site`, with the same line, column
  and kind. `icfg.build_icfg` raises if the two drift apart.
- Builtin and stdlib behaviour belongs in `stubs/`, not in `analysis/interp/`.
- Keep abstract values finite: anything that can nest needs a depth or widening bound.
- `EXPECTED` edges in a case docstring are ground truth from a traced run, so change the
  analyzer to make a case pass, not the expected edges.
- Modules: finding and loading files, module names and `__all__` belong in `frontend/`
  (`loader._Loader`, `builder.ProgramBuilder`); what an import binds at analysis time belongs
  in `analysis/interp/modules.py`. Running an imported module registers its body as a node but
  records no call edge.
- Imports: a package imports only the packages before it in `model` → `frontend` → `stubs` →
  `analysis` → `output`. Import another package through its `__init__`
  (`from ..model import Vals`), and a module of your own package directly
  (`from .scopes import scope_locals`), never through your own package's `__init__`.
- Keep files small: a new concern gets its own module in the right package, and an `Interp`
  method goes into the layer that matches it. `stubs/__init__.py` imports the stub modules in
  registration order; `from m import *` depends on that order, so add new ones at the end.
- `info/generated/data.js` is generated: rerun `python info/build.py` instead of editing it.
  `tests/test_info.py` runs `--check`.
- Style: `from __future__ import annotations`, full type hints (`pyrightconfig.json`), frozen
  dataclasses with cached hashes for values, and no runtime dependencies.
- Docstrings: every function and class has one (trivial dunders such as `__hash__` excepted),
  written like those in `callme/__init__.py`: an imperative summary of what it does and
  returns, a short paragraph on side effects and guarantees, and an aligned table of the
  parameters that are not obvious.
