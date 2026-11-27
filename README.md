# call-me-py

A static analyzer that builds the call graph of a Python program, following its imports
across files, including calls that go through *function values*: callbacks, lambdas, closures, decorators, `functools.partial`,
methods reached through objects, and the dunder methods behind operators. It never runs the
program.

## The idea in one example

```python
def square(x): return x * x
def double(x): return x * 2
def apply(f, v): return f(v)    # which function does f(v) call?

apply(square, 1)
apply(double, 2)
```

Tracking which functions flow into `f` tells us `f(v)` may call `square` or `double`. That is
correct, but blurry: each call of `apply` reaches only one of them. callme analyses a function
once per **context**, meaning once per abstract value of its function-valued parameters, so the
two calls stay apart:

```
$ python -m callme examples/cases/hof/08_function_as_argument.py --format dot --contexts --id-style short -o /dev/stdout
  "<module>" -> "apply[f={square}]" [label="L36"];
  "<module>" -> "apply[f={double}]" [label="L37"];
  "apply[f={square}]" -> "square" [label="L25"];
  "apply[f={double}]" -> "double" [label="L25"];
  ...
```

Without `--contexts`, the same result is shown with one node per function.

## Requirements

Python ≥ 3.12 and only the standard library. The tests need pytest:
`pip install -r requirements-dev.txt`. The project is not packaged, so run it from the
repository root (or put the root on `PYTHONPATH`).

## Usage

```sh
python -m callme prog.py -o graph.json                       # JSON (the default format)
python -m callme prog.py --format dot -o graph.dot           # one node per function
python -m callme prog.py --format dot --contexts -o ctx.dot  # one node per (function, context)
python -m callme prog.py --format icfg -o icfg.dot           # interprocedural CFG
python -m callme prog.py --id-style short --include-unreachable -o graph.json
python -m callme examples/project/main.py --id-style module --format dot -o /dev/stdout
python -m callme a.py b.py --path src -o graph.json         # two entry files, imports also from src/
```

Imports of project modules are followed: starting from each entry file, callme loads every
module it imports, looking in the entry's directory (as Python does with `sys.path[0]`) and in
each `--path`. Imports it cannot find there, such as the standard library, stay external.
Nothing is written without `-o` (use `-o /dev/stdout` to print). For every flag and the
output layouts, see [docs/output.md](docs/output.md).

```python
from callme import analyze_file, to_dot

cg = analyze_file("prog.py", id_style="short")
cg.function_edges()                    # {("<module>", "apply"), ("apply", "square"), ...}
for site in cg.sites:                  # every call site with its possible callees
    print(site.line, site.text, sorted(cg.targets(site)))
print(to_dot(cg, contexts=True))

# Second pass on the same, unchanged file: reuse the solver state, add never-called functions.
cg2 = analyze_file("prog.py", state=cg.state, include_unreachable=True)
```

`analyze_files([...], search_path=[...])` takes several entry files, and
`analyze_source(text, filename)` analyses a string as one module, without following imports.

## How it works

```
source ─► frontend ─► Program ─► solver ─► State ─► callgraph ─► CallGraph ─► export
          ids, scopes,           fixpoint over         nodes, edges,          JSON / DOT
          signatures, CFGs       (function, context)   sites, labels
```

1. **frontend** parses the entry files and every project module they import. Every function
   (def, lambda, module body) and class gets an id, a scope (its locals, globals and
   nonlocals), a signature and a control-flow graph.
2. **solver** starts at the entry module bodies and abstractly interprets each (function,
   context) node; an import makes the imported module's body reachable. A value is a set of *tokens*: closures, classes, instances, containers or builtin stubs.
   A call with new argument values creates a new context. Rounds repeat until nothing grows.
3. **callgraph** turns the solver state into context nodes and call edges, plus a view with
   one node per function. **export** writes the result as JSON or DOT.

The details are in [docs/analysis.md](docs/analysis.md).

**Handled:** first-order calls and recursion; lambdas, closures, default arguments,
`*args`/`**kwargs`, and functions passed in or returned; decorators on functions and classes;
functions stored in lists, dicts and object fields; classes with inheritance (C3 MRO),
`super`, `classmethod`/`staticmethod`/`property`, descriptors, metaclasses,
`__init_subclass__` and dataclasses; dunder calls implied by operators, subscripts,
iteration, `with` and f-strings; flow sensitivity (rebinding, branches, loops, `try`, `match`);
builtin and stdlib higher-order functions (`map`, `sorted`, `functools.reduce`, ...) through stubs;
programs spread over several files: absolute and relative imports, packages and namespace
packages, `import *` with or without `__all__`, circular and lazy imports, and modules passed
around as values.

**Not handled:** the source of the standard library and third-party packages (only
`functools`, `itertools`, `dataclasses` and `importlib` are modelled); `eval`/`exec`,
string-based `getattr`/`setattr` and classes created by calling `type(...)`; module names
that are computed at run time (`importlib.import_module(name)` may return any loaded module);
callbacks run by threads or event loops; implicit exceptions outside a `try`. A call whose
target is unknown gets no edge. The test families `implicit`, `concurrency` and `dynamic` are
only partly supported, so `pytest.ini` leaves them out of the default run.

## Repository layout

```
callme/                    the analyzer (map: docs/architecture.md)
  model/                   data types: ids, tokens, contexts, CFGs
  frontend/                source -> Program: loading modules, scopes, call sites, CFGs
  stubs/                   models of builtin and stdlib functions, one module per library
  analysis/                the abstract interpreter (interp/) and the fixpoint (solver)
  output/                  the call graph, JSON / DOT export, saved states
tests/                     pytest suite
examples/cases/            ground-truth programs, one feature each, grouped by family (see its README)
examples/*.py              loose programs (and examples/project/main.py); tests only check they analyse
info/                      interactive explainer: open info/index.html (rebuild: python info/build.py)
docs/                      reference documentation (below)
project_description.md     the original project proposal
```

## Tests

```sh
pytest                                  # the families selected in pytest.ini
pytest --families all                   # every family
pytest --families hof,stdlib -k sound   # a subset; test ids are <family>/<case>
```

`tests/test_examples.py` checks every case in `examples/cases/` against its docstring:

- `test_sound`: every traced `EXPECTED` edge is found.
- `test_precise`: nothing is found outside `EXPECTED`, `STATIC_ONLY` and `SPAWN`.
- `test_sites`: each listed call line resolves to exactly the listed callees.
- `test_flow_refines`: the flow-sensitive edges are a subset of the flow-insensitive ones.

The other test files cover the core cases (`test_cases`, `test_unit`), CFGs and the ICFG
(`test_cfg`), classes (`test_classes`), the heap (`test_heap`), multi-file programs
(`test_modules`), multi-pass runs and saved state (`test_multipass`), and whether the
explainer's data is up to date (`test_info`).

## Documentation

| Read | when you need |
|---|---|
| [docs/architecture.md](docs/architecture.md) | the module map, data types, solver state, and where each feature lives |
| [docs/analysis.md](docs/analysis.md) | how calls are resolved: contexts, fixpoint, flow sensitivity, heap, objects, stubs, limits |
| [docs/output.md](docs/output.md) | CLI flags, id and label formats, JSON and DOT layouts, saved state files |
| [docs/extending.md](docs/extending.md) | how to add a stub, a syntax feature, an analysis pass or a test case |
| [AGENTS.md](AGENTS.md) | a short orientation for coding agents |

## License

MIT, see [LICENSE](LICENSE).
