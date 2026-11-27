# Extending callme

Recipes for the usual changes. If you do not know the module layout yet, read
[architecture.md](architecture.md) first.

## Add a stub for a builtin or library function

Write a stub when a builtin or stdlib function should reach the callbacks it receives, or
should return something callable. Stubs live in `callme/stubs.py`:

```python
@stub("itertools.starmap")
def _starmap(it: Host, a: Args, site: Site) -> Vals:
    """Model `itertools.starmap(f, xs)`: call `f` with each element unpacked as its arguments."""
    return call(it, a.get(0), [], site, star=a.get(1))
```

- **Names.**
  - A name without a dot is a builtin. It is found by name unless the user shadows it, and is
    added to `BUILTIN_NAMES` automatically.
  - `module.name` is reached via `import module` or `from module import name`. A new module
    must also be added to `STUB_MODULES`. A project file with the same module name wins over
    the stub module, as it would in Python.
  - `<name>` is an internal token that user code cannot name.
  - One function may register several names.
- **Arguments.** `a: Args` holds the abstract arguments, with bound arguments prepended and
  containers already dereferenced, so "the elements of `xs`" is just `a.get(i)`. The accessors:
  - `a.get(i, kw)`: positional `i` or keyword `kw`, falling back to `*` / `**`;
  - `a.kw(name)`: one keyword argument;
  - `a.rest(i)`: positional arguments from `i` on;
  - `a.all()`: every argument.
- **Calling back.**
  - `call(it, f, [args...], site)` invokes the callbacks from the current node at the stub's
    call site.
  - `fold(...)` iterates an accumulator to a fixpoint, as `functools.reduce` does.
  - `it.invoke_dunder(vals, name, args, site)` and `it.iterate(vals, site)` trigger dunders.
  - `Host` (a Protocol) lists everything a stub may use from the interpreter.
- **Result.** Return the abstract result, or `EMPTY` if nothing callable comes out. To return
  a new callable, return `Builtin("<name>", bound=(...))` and give `<name>` its own stub, as
  `functools.partial` does.
- **Methods of builtin values.** Add `"attr": "stub.name"` to `METHOD_STUBS`; the receiver is
  bound as the first argument.
- **Stay finite.** Drop or widen tokens that could nest without bound (`_shallow`,
  `MAX_PARTIAL_DEPTH`).

## Support a new piece of syntax

Two places need the change, and both dispatch on the AST node's type name:

1. **CFG** (`callme/cfg.py`). Statements use `Builder.s_<Stmt>`; the fallback evaluates the
   child expressions and emits one `stmt` point. Expressions use `Builder.expr`. Every
   `ast.Call` and every decorator must become a call point, in evaluation order, through
   `call_point(node, make_site(...))`.
2. **Analysis** (`callme/transfer.py`). Expressions use `Interp.e_<Expr>`, which returns
   `Vals`; the fallback `generic` evaluates the children and returns `EMPTY`. Statements use
   `Interp.s_<Stmt>`, with `generic_stmt` as the fallback. In flow mode, alternative paths go
   through `_path` and `_meet`, and loops through `_loop`.

Rules:

- **Sites.** Build sites with `extract.make_site(caller, node, kind)`, so that the
  interpreter's `Site` equals the CFG's. Implicit calls use one of the non-syntactic kinds
  (`op`, `attr`, `iter`, `with`, `class`), because they have no CFG point.
- **Writes.** Write state only through the `State` helpers (`add_*`, `register`, `resolve`),
  and never when `self.readonly` is set. Read-only mode is used to evaluate default arguments.
- **Tests.** `tests/test_cfg.py` checks every case for well-formed CFGs and for edge sites
  that match the listed sites.

## Add an analysis pass

A pass computes its own facts and may read the call graph or other passes.
`pipeline.run_passes` steps all passes in rounds until none of them changes, so passes that
feed each other reach a joint fixpoint.

```python
from dataclasses import dataclass, field
from callme import CallGraphPass, run_passes
from callme.extract import parse_file
from callme.transfer import State

@dataclass
class Reached:                       # a pass state needs copy() and same_result()
    funcs: set[str] = field(default_factory=set)
    def copy(self): return Reached(set(self.funcs))
    def same_result(self, other): return self.funcs == other.funcs

class ReachedPass:
    name = "reached"                 # key of this pass's state in Facts
    def init(self, prog, facts, state):
        return state if state is not None else Reached()   # idempotent
    def step(self, prog, facts):                           # one round; True if it grew
        cg, mine = facts.get("callgraph", State), facts.get(self.name, Reached)
        new = {n.func.qualname for n in cg.order} - mine.funcs
        mine.funcs |= new
        return bool(new)

facts = run_passes(parse_file("app.py"), [CallGraphPass(), ReachedPass()])
```

Rules, from the docstring of `pipeline.py`:

- `init` is idempotent: given an existing state, it only validates it and adds roots.
- `step` only joins, so results never shrink.
- States implement `copy()` and `same_result()`.

For a dataflow pass, `icfg.icfg_from_state(prog, state)` gives the ICFG, whose `succ` / `pred`
work over `(Node, point)` pairs. To make the pass's state saveable, register an
`(encode, decode)` pair under its name in `persist.CODECS`.

## Add a test case

Add `examples/cases/<family>/NN_name.py`, or a directory with a `main.py` for a multi-file
case; its other modules are loaded by following `main.py`'s imports, and names carry the
module (`pkg.core.run`). Its docstring lists the ground truth under the headers `EXPECTED`, `STATIC_ONLY`,
`SPAWN`, `SITES` and so on; [examples/cases/README.md](../examples/cases/README.md) describes
the format. Case numbers are unique across families. `tests/test_examples.py` picks the case
up automatically, provided its family is selected in `pytest.ini` or with `--families`.

## Keep the explainer in sync

`info/` shows real analyzer output. After changing `callme/`, run `python info/build.py`;
`tests/test_info.py` fails until you do. If a chapter's scene changed, open that chapter with
`?selftest` and update its captions, as described in [info/README.md](../info/README.md).
