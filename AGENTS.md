# AGENTS.md

call-me-py builds context-sensitive call graphs for single Python files by abstract
interpretation. The package is `callme/`, it uses only the standard library, and it needs
Python ≥ 3.12. Read [README.md](README.md) for an overview and `docs/` for details. The docs
are written so that you rarely need to read `transfer.py` in full, and every function's
docstring states its contract, so reading a definition's first lines is usually enough.

## Commands (run from the repo root)

```sh
pytest                                    # the families selected in pytest.ini
pytest --families all -k sound -x         # every family, soundness checks only
python -m callme FILE -o out.json         # nothing is printed without -o
python -m callme FILE --format dot --contexts --id-style short -o /dev/stdout
python info/build.py                      # after changing callme/: regenerate info/generated/data.js
```

## Map

| File | Lines | What it holds |
|---|---|---|
| `callme/model.py` | 532 | every data type: ids, tokens, `Ctx` / `Node` / `Site` / `Edge`, CFG types |
| `callme/cfg.py` | 478 | CFG builder, one CFG per function |
| `callme/extract.py` | 342 | parsing into a `Program`: ids, scopes, signatures, call sites |
| `callme/stubs.py` | 676 | models of builtin and stdlib functions (`@stub` registry) |
| `callme/transfer.py` | 1736 | `State` (global tables) and `Interp` (abstract interpreter); read it by banner section |
| `callme/pipeline.py`, `solver.py` | 131, 135 | the multi-pass driver and the call graph pass (`solve`) |
| `callme/icfg.py`, `callgraph.py`, `export.py` | 138, 97, 184 | the ICFG, the `CallGraph` result, JSON / DOT |
| `callme/persist.py` | 384 | saving and loading state |
| `callme/__init__.py`, `__main__.py` | 86, 98 | public API, CLI |

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
  (`cfg.Builder.call_point`) and a `Site` from `extract.make_site`, with the same line, column
  and kind. `icfg.build_icfg` raises if the two drift apart.
- Builtin and stdlib behaviour belongs in `stubs.py`, not in `transfer.py`.
- Keep abstract values finite: anything that can nest needs a depth or widening bound.
- `EXPECTED` edges in a case docstring are ground truth from a traced run, so change the
  analyzer to make a case pass, not the expected edges.
- `info/generated/data.js` is generated: rerun `python info/build.py` instead of editing it.
  `tests/test_docs.py` runs `--check`.
- Style: `from __future__ import annotations`, full type hints (`pyrightconfig.json`), frozen
  dataclasses with cached hashes for values, and no runtime dependencies.
- Docstrings: every function and class has one (trivial dunders such as `__hash__` excepted),
  written like those in `callme/__init__.py`: an imperative summary of what it does and
  returns, a short paragraph on side effects and guarantees, and an aligned table of the
  parameters that are not obvious.
