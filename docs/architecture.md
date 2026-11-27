# Architecture

This is the map of `callme/`: what each package and module does, the types passed between
them, the solver state, and where each feature is implemented. For how the analysis decides
things, read [analysis.md](analysis.md).

References such as `frontend.make_site` name a package (`callme/frontend/`) and a name it
exports; `solver.step` names a module inside a package (`callme/analysis/solver.py`). Methods
are named by their class, as in `Interp.apply` or `State.resolve`; the tables below give the
file each one is in.

## Pipeline

```
source text
  │  frontend.build_program(source, filename)  /  frontend.parse_files(paths, search_path)
  ▼
Program    funcs     FuncId  -> FuncInfo   kind, lexical parent, Sig, locals, globals, nonlocals, owner class
           classes   ClassId -> ClassInfo  enclosing function, body, known_attrs
           cfgs      FuncId  -> CFG        frontend.build_cfg, one per function
           modules   name    -> ModuleInfo file, module body, package flag, star exports
           entries                         module bodies of the entry files (the roots)
           fingerprint                     sha256 of every file name + source
  │  analysis.solve(prog, state=None, ...)   (pipeline.run_passes with one CallGraphPass)
  ▼
State      contexts, local envs, return values, call edges, heap, class tables  (analysis.State)
  │  output.build_callgraph(prog, state, style)
  ▼
CallGraph  nodes, edges, sites, labels, state   ──  .icfg() -> output.ICFG
  │  output.to_json / to_dot / to_icfg_dot
  ▼
JSON / DOT
```

`analyze_source`, `analyze_file` and `analyze_files` in `__init__.py` chain these three
calls. `build_program` makes a one-module program from a string; `parse_files` (and
`parse_file`) load the entry files and every project module they import. `output.persist`
saves a `State` and resumes it. `pipeline.run_passes` runs the call graph pass alongside
other passes.

## Packages

```
callme/
  __init__.py  __main__.py   public API and command line
  model/       data types only
  frontend/    step 1: source -> Program
  stubs/       models of builtin and stdlib callables
  analysis/    step 2: the interpreter and the fixpoint
    interp/    the interpreter, one layer per module
  output/      step 3: the call graph, the ICFG, JSON / DOT, saved states
```

Each package imports only the packages above it in this list: `model` ← `frontend` ←
`stubs` ← `analysis` ← `output` ← `__init__` / `__main__`. A module imports another package
through its `__init__` (`from ..model import Vals`), and a module of its own package directly
(`from .scopes import scope_locals`), never through its own package's `__init__`, which may
not be initialised yet. Each package's `__init__` re-exports its public names.

**`model/`**: data types (hashing, labels).

| Module | Holds |
|---|---|
| `ids.py` | `module_name`, `FuncId`, `ClassId`, `Site` |
| `values.py` | the 11 token classes, `Alloc`, `Token`, `token_label`, `Vals`, `EMPTY`, `ParamState`, `Ctx`, `Node` (a closure holds its defining node and a context holds tokens, so they share a module) |
| `graphs.py` | `Edge`, CFG kind constants, `CFGNode`, `CFGEdge`, `CFG`, `IEdge` |

**`frontend/`**: loading modules, ids, scopes, signatures, CFGs.

| Module | Holds |
|---|---|
| `program.py` | `Program`, `ModuleInfo`, `FuncInfo`, `ClassInfo`, `Sig`, `MODULE_NAME` |
| `scopes.py` | `walk_scope`, `scope_locals`, `scope_decls`, `scope_must_writes`, `make_sig`, `pattern_names`, `class_known_attrs` |
| `sites.py` | `make_site`, `call_sites` |
| `cfg.py` | `Builder` (`s_<Stmt>`, `expr`, `call_point`, `jump`), `build_cfg` |
| `builder.py` | `ProgramBuilder` (`add_module`, `link_star_imports`, `finish`), `build_program`, `module_all`, `absolute_module`, `fingerprint` |
| `loader.py` | `_Loader` (`find`, `imports`), `parse_files`, `parse_file`, `entry_module` |

**`stubs/`**: models of builtin and stdlib callables. `__init__.py` imports every stub
module in registration order and defines `BUILTIN_NAMES`.

| Module | Holds |
|---|---|
| `registry.py` | `@stub`, `STUBS`, `Args`, `Host`, `HostState`, `call`, `fold`, `STUB_MODULES`, `METHOD_STUBS`, `shallow`, `boxes_of` |
| `builtins.py`, `objects.py`, `containers.py` | builtin functions; classmethod, staticmethod, super, property, object, type; tuple / zip / list / set / dict, container methods, `__import__` |
| `dataclasses.py`, `functools.py`, `itertools.py`, `contextlib.py`, `collections.py`, `threading.py`, `futures.py`, `asyncio.py` | one stdlib module each (`futures.py` covers `concurrent.futures` and `asyncio.Future`) |

**`analysis/`**: the global `State`, the interpreter of one function in one context, and
the fixpoint.

| Module | Holds |
|---|---|
| `state.py` | `State`, `Env`, `Writes`, `join_envs`, `mk_closure`, `MAX_DEPTH` |
| `interp/` | `Interp`, `analyze`, and the layers below |
| `pipeline.py` | generic multi-pass fixpoint driver: `Pass`, `PassState`, `Facts`, `run_passes` |
| `solver.py` | the call graph pass: `init_state`, `step`, `CallGraphPass`, `solve`, `DEFAULT_MAX_CONTEXTS` |

**`output/`**: results.

| Module | Holds |
|---|---|
| `callgraph.py` | `CallGraph`, `build_callgraph`, `node_label` |
| `icfg.py` | `ICFG`, `build_icfg`, `icfg_from_state`, `SYNTACTIC_KINDS` |
| `export.py` | `to_dict`, `to_json`, `icfg_dict`, `to_dot`, `to_icfg_dot` |
| `persist.py` | `save_state`, `load_state`, `state_to_dict`, `state_from_dict`, `facts_to_dict`, `facts_from_dict`, `CODECS`, `StateDoc`, `Row` |

`__init__.py` holds `analyze_source`, `analyze_file`, `analyze_files` and the re-exports in
`__all__`; `__main__.py` holds `main` and `print_graph`.

## The interpreter (`analysis/interp/`)

`Interp` is a chain of layers, one class per module, each subclassing the one before it, so
a layer can call every method of the layers below it. Only two methods are called from below
the layer that defines them, `eval` and `apply`, and `BaseLayer` declares both as abstract
methods. `Interp` (in `__init__.py`, with `analyze`) is the final class at the top.
`eval` and `block` dispatch on the node type to `e_<Expr>` and `s_<Stmt>` methods, which
may live in any layer.

| # | Module | Class | Holds |
|---|---|---|---|
| 0 | `base.py` | `BaseLayer` | the fields, `__init__`, abstract `eval` and `apply`; `bind_attr`, `LoopFrame` |
| 1 | `names.py` | `NameLayer` | `bind`, `lookup`, `nonlocal_owner`, `_apply_writes`, `set_attr`, `invoke_data_descriptor_set`, `assign`, `store`, `new_box`, `invoke_dunder`, `iterate` |
| 2 | `modules.py` | `ModuleLayer` | `run_module`, `module_attr`, `import_any`, `s_Import`, `s_ImportFrom` |
| 3 | `calls.py` | `CallLayer` | `default_value`, `bind_args`, `instantiate`, `apply` |
| 4 | `attributes.py` | `AttributeLayer` | `attribute`, `e_Attribute` |
| 5 | `expressions.py` | `ExprLayer` | `eval`, `e_<Expr>` for names, lambdas, calls, literals, `await`, `yield`, comprehensions; `_mutate_container` |
| 6 | `operators.py` | `OperatorLayer` | `e_BinOp`, `e_UnaryOp`, `e_Compare`, `e_Subscript`, `e_FormattedValue`, `s_AugAssign`, `*_DUNDERS` |
| 7 | `statements.py` | `StmtLayer` | `block`, `generic_stmt`, `s_Assign`, `s_Return`, `s_Raise`, `s_Delete`, `s_Assert`, `run` |
| 8 | `definitions.py` | `DefLayer` | `s_FunctionDef`, `s_ClassDef`, `_apply_decorator` |
| 9 | `flow.py` | `FlowLayer` | `_path`, `_meet`, `_loop`, `s_If`, `s_While`, `s_For`, `s_Try`, `s_Match`, `s_With` |

A layer that assigns one of `BaseLayer`'s fields declares it again in its class body, as
basedpyright requires for classes that are not `@final`.

## Data types (`model/`)

**Ids.** `FuncId(qualname, file, lineno, col, module)` identifies one def, async def, lambda or
module body (`<module>`). `ClassId` has the same fields. `module` is the dotted name of the
defining module; it is not part of the identity, and when it is left empty it is derived from
`file` (`model.module_name`). `render(style)` returns `full` (`file:line:col:qualname`, the
unique `uid`), `module` (`pkg.mod.qualname`) or `short` (`qualname`).

**Tokens.** An abstract value `Vals` is a `frozenset` of tokens, and `EMPTY` means nothing
callable is known. Each token has a `depth`, which is not part of its equality, used to bound
nesting.

| Token | Stands for | Created in |
|---|---|---|
| `Closure(func, parent)` | function `func` created while node `parent` ran (`None`: unknown) | `analysis.mk_closure`, from `s_FunctionDef` and `e_Lambda` |
| `Builtin(name, bound, bound_kw)` | a callable modelled in `stubs/`, or a stub module; `bound` holds pre-bound args (receiver, partial args) | `Interp.lookup` (builtin names), `s_Import*`, stubs |
| `ClassObj(cls)` | a class object | `Interp.s_ClassDef` |
| `Instance(cls, alloc)` | objects of `cls` created at allocation site `alloc` | `Interp.instantiate`, `object.__new__` stub, `except ... as e` |
| `Box(kind, alloc)` | a list, dict or set created at `alloc`; what is stored into it later lives in `State.heap` | literals, comprehensions, `list()` / `dict()` / `set()` |
| `BoundMethod(func, receiver)` | `func` with `receiver` bound as first argument | `bind_attr` (`interp/base.py`) on attribute reads |
| `ClassMethod(func)`, `StaticMethod(func)` | the descriptors | their stubs; also implied for `__init_subclass__` and `__new__` in class bodies |
| `Super(start, receiver)` | a `super()` proxy | `super` stub |
| `Property(fget, fset, fdel)` | a property descriptor | `property` stub, `.getter` / `.setter` / `.deleter` |
| `Module(name)` | a module object of the program; its attributes are its globals and submodules | `Interp.s_Import`, `s_ImportFrom`, `import_any` |

`Alloc(func, line, col)` is an allocation site.

**Analysis units.**

- `Ctx(params, parent, merged)` is a context.
  - `params`: sorted `(name, tokens)` pairs, only for parameters whose value is non-empty.
  - `parent`: the node in which a closure was defined.
  - `merged`: marks a widened context; its entry state is stored in `State.entry`.
- `Node(func, ctx)` is one analysed instance of a function. The roots are
  `Node(entry, Ctx())` for each of `prog.entries`, and an imported module's body runs as
  `Node(<module>, Ctx())` too.
- `Site(caller, line, col, text, kind)` is a place where a call happens.
  - `call` and `decorator` are *syntactic*: they are CFG call points, and
    `frontend.call_sites` lists them.
  - The *implicit* kinds are created by the interpreter and appear only in edges and
    `ICFG.implicit`: `attr` (property, descriptor, `__getattr__`, `__setattr__`), `op`
    (operators, subscripts, f-strings), `iter` (for loops and comprehensions), `with`, and
    `class` (`__init_subclass__` and metaclass hooks).
- `Edge(caller: Node, site, callee: Node)` is a context-sensitive call edge.

**Control flow.** `CFG(func, nodes, edges, calls, entry=0, exit=1)`, where `calls` maps
`Site -> (CALL point, AFTER_CALL point)`. Node kinds are `entry`, `exit`, `stmt`, `branch`,
`loop`, `handler`, `call` and `after_call`. Edge kinds are `flow`, `true`, `false`, `exc` and
`call_to_return`. The ICFG adds `call` (call point to the callee's entry) and `return` (the
callee's exit to the after-call point of the same caller context). `IEdge` connects
`(Node, point)` pairs. A CFG belongs to its function and is shared by all of the function's
contexts.

## Solver state (`analysis.State`)

| Field | Type | Holds |
|---|---|---|
| `max_ctx` | `int` | contexts per function before widening (default 16) |
| `program` | `str` | fingerprint of the `Program` this state belongs to |
| `flow` | `bool` | whether locals are flow-sensitive |
| `order`, `by_func` | `list[Node]`, `FuncId -> set[Node]` | every registered node, in registration order and grouped by function |
| `envs` | `Node -> {local -> Vals}` | per node, every value each local takes (its summary) |
| `ret` | `Node -> Vals` | return and yield values |
| `edges` | `set[Edge]` | call edges found so far |
| `entry` | `Node -> Env` | entry states of widened contexts |
| `class_attrs` | `ClassId -> Env` | class namespace: methods, class attributes, attributes set on the class |
| `inst_attrs` | `Instance -> Env` | attributes of each abstract object |
| `heap` | `Box -> Vals` | what was stored into each container after it was created |
| `extern` | `FuncId -> Env` | writes from other scopes: `global` and `nonlocal`, and `module.attr = v` into a module body |
| `writes` | `Node -> (FuncId -> Env)` | per-node `global`, `nonlocal` and module-attribute writes (direct and transitive), applied back at call sites in flow mode |
| `bases`, `metaclasses` | `ClassId -> ...` | direct user bases and metaclasses (`State.mro` computes and caches the MRO) |
| `unknown_decos` | `set[tuple[Node, Site]]` | decorator sites confirmed unresolved after fixpoint convergence, treated as identity |
| `changed` | `bool` | set by any write that grew something during the current round |

Writes go through joining helpers, all of which set `changed`: `register`, `resolve`,
`add_class_attr`, `add_inst_attr`, `add_heap`, `add_extern`, `add_write`, `add_bases`,
`add_metaclass`. `solver.step` joins `envs`, `ret` and `edges`.

## Where to look

Files are relative to `callme/`; `interp/` is `analysis/interp/`.

| Question | Look at |
|---|---|
| Which callees does a call reach, and what does it return? | `Interp.apply` (`interp/calls.py`) |
| How are arguments bound (defaults, `*args`, `**kwargs`, wrong arity)? | `Interp.bind_args`, `Interp.default_value` (`interp/calls.py`) |
| How is a name resolved (local, closure, module, builtin)? | `Interp.lookup`, `Interp.bind`, `Interp.nonlocal_owner` (`interp/names.py`) |
| What value does a `def` or `lambda` produce? | `Interp.s_FunctionDef` (`interp/definitions.py`), `Interp.e_Lambda` (`interp/expressions.py`), `analysis.mk_closure` |
| How is `obj.attr` read (methods, descriptors, property, `__getattr__`, `super`)? | `Interp.attribute` (`interp/attributes.py`), `bind_attr` (`interp/base.py`) |
| How is `obj.attr = v` written? | `Interp.set_attr`, `Interp.invoke_data_descriptor_set` (`interp/names.py`) |
| What happens when a class is called? | `Interp.apply` (the `ClassObj` branch, metaclass `__call__`), `Interp.instantiate` (`interp/calls.py`) |
| What happens at a `class` statement? | `Interp.s_ClassDef` (`interp/definitions.py`) |
| Which dunders does an operator call? | `Interp.e_BinOp`, `e_UnaryOp`, `e_Compare`, `e_Subscript`, `e_FormattedValue`, `s_AugAssign`, `*_DUNDERS` (`interp/operators.py`) |
| How are iteration and `with` handled? | `Interp.iterate`, `_iter_expr` (`interp/names.py`), `s_For`, `s_With` (`interp/flow.py`) |
| How are containers handled (append, `d[k] = v`, unpacking, `*xs`)? | `Interp._mutate_container` (`interp/expressions.py`), `store`, `assign` (`interp/names.py`), `State.deref` |
| How do branches, loops, `try` and `match` work in flow mode? | `Interp.block` (`interp/statements.py`), `_path`, `_meet`, `_loop`, `s_Try`, `s_Match` (`interp/flow.py`) |
| When is a new context made, and when is it widened? | `State.resolve`, `analysis.mk_closure` (`MAX_DEPTH`) (`analysis/state.py`) |
| How are the MRO and class attribute lookups computed? | `State.mro`, `State.lookup_class_attr` (`analysis/state.py`) |
| How does a builtin or stdlib callable behave? | `stubs/`, the module named after its library (the `@stub` registry is in `stubs/registry.py`) |
| What does one round do, and what is the global loop? | `solver.step`, `pipeline.run_passes` |
| What are the roots, and what does `include_unreachable` do? | `solver.init_state` |
| How are locals, signatures and qualnames computed? | `ProgramBuilder.add_module` (`frontend/builder.py`), `scope_locals`, `make_sig` (`frontend/scopes.py`) |
| Which files are loaded, and how is a module name resolved? | `parse_files`, `_Loader.find`, `_Loader.imports`, `entry_module` (`frontend/loader.py`), `absolute_module` (`frontend/builder.py`) |
| What does `import` or `from m import x` bind, and what is `mod.attr`? | `Interp.s_Import`, `s_ImportFrom`, `module_attr`, `run_module` (`interp/modules.py`) |
| Which names does `from m import *` bind? | `module_all`, `ProgramBuilder.link_star_imports` (`frontend/builder.py`), `ModuleInfo.exports` |
| What CFG does a statement get? | `Builder.s_<Stmt>`, `Builder.expr` (`frontend/cfg.py`) |
| How are ICFG calls and returns wired? | `ICFG.call_edges`, `return_edges`, `build_icfg` (`output/icfg.py`) |
| How are labels like `f[x={g}]` built? | `node_label` (`output/callgraph.py`), `model.token_label` |
| What do the JSON and DOT outputs look like? | `to_dict`, `to_dot`, `to_icfg_dot` (`output/export.py`) |
| What is the saved state layout? | `StateDoc`, `Row` (`output/persist.py`) |

## Invariants

- **Results only grow.** Every write to `State` is a join. Re-solving a fixpoint therefore
  changes nothing (`State.same_result`), a saved state can be resumed, and passes can be
  chained, for example reachable code first and then `include_unreachable=True`.
- **One program version per state.** `Program.fingerprint` (every file name + source) is checked by
  `solver.init_state`, `pipeline.run_passes` and `persist.state_from_dict`. A resumed state
  keeps its own `max_ctx` and `flow`, and asking for different values raises `ValueError`.
- **The CFG and the interpreter agree on call sites.** `frontend.call_sites` reads `CFG.calls`,
  and the interpreter builds the same `Site` with `frontend.make_site`. `output.build_icfg`
  raises if a `call` or `decorator` edge has no CFG point.
- **Dead code is still analysed.** Statements after a `return` and both branches of every `if`
  are interpreted, so their call sites have edges. Functions that are never called are
  analysed only with `include_unreachable`.
- **Values are immutable.** Tokens, `Ctx` and `Node` are frozen dataclasses with cached
  hashes, so `State.copy()` copies only the containers.
- **The stub registry keeps its order.** `stubs/__init__.py` imports the stub modules in a
  fixed order, because `from m import *` binds a stub module's names in registration order.
