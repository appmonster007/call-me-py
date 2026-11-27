# Architecture

This is the map of `callme/`: what each module does, the types passed between modules, the
solver state, and where each feature is implemented. For how the analysis decides things, read
[analysis.md](analysis.md).

References such as `transfer.Interp.apply` name the module (`callme/transfer.py`) and then
the qualified name inside it.

## Pipeline

```
source text
  │  extract.build_program(source, filename)  /  extract.parse_file(path)
  ▼
Program    funcs     FuncId  -> FuncInfo   kind, lexical parent, Sig, locals, globals, nonlocals, owner class
           classes   ClassId -> ClassInfo  enclosing function, body, known_attrs
           cfgs      FuncId  -> CFG        cfg.build_cfg, one per function
           fingerprint                     sha256 of file name + source
  │  solver.solve(prog, state=None, ...)   (pipeline.run_passes with one CallGraphPass)
  ▼
State      contexts, local envs, return values, call edges, heap, class tables  (transfer.State)
  │  callgraph.build_callgraph(prog, state, style)
  ▼
CallGraph  nodes, edges, sites, labels, state   ──  .icfg() -> icfg.ICFG
  │  export.to_json / to_dot / to_icfg_dot
  ▼
JSON / DOT
```

`analyze_source` and `analyze_file` in `__init__.py` chain these three calls. `persist`
saves a `State` and resumes it. `pipeline.run_passes` runs the call graph pass alongside
other passes.

## Modules

Each module imports only modules listed above it.

| Module | Lines | Role | Key symbols |
|---|---|---|---|
| `model.py` | 532 | data types only (hashing, labels) | `FuncId`, `ClassId`, the 10 token classes, `Token`, `Vals`, `EMPTY`, `Ctx`, `Node`, `Site`, `Edge`, `CFG`, `CFGNode`, `CFGEdge`, `IEdge`, CFG kind constants, `token_label` |
| `cfg.py` | 478 | one CFG per function | `Builder` (`s_<Stmt>`, `expr`, `call_point`, `jump`), `build_cfg` |
| `extract.py` | 342 | step 1: ids, scopes, signatures, CFGs | `build_program`, `parse_file`, `Program`, `FuncInfo`, `ClassInfo`, `Sig`, `scope_locals`, `make_site`, `call_sites`, `fingerprint` |
| `stubs.py` | 676 | models of builtin and stdlib callables | `@stub`, `STUBS`, `BUILTIN_NAMES`, `METHOD_STUBS`, `STUB_MODULES`, `Args`, `Host`, `call`, `fold` |
| `transfer.py` | 1736 | the global `State`, and the interpreter of one function in one context | `State`, `Interp`, `analyze`, `mk_closure`, `_bind_attr`, `join_envs`, `BINOP_DUNDERS` / `UNARYOP_DUNDERS` / `CMP_DUNDERS`, `MAX_DEPTH` |
| `pipeline.py` | 131 | generic multi-pass fixpoint driver | `Pass`, `PassState`, `Facts`, `run_passes` |
| `solver.py` | 135 | the call graph pass | `init_state`, `step`, `CallGraphPass`, `solve`, `DEFAULT_MAX_CONTEXTS` |
| `icfg.py` | 138 | interprocedural CFG over context nodes | `ICFG`, `build_icfg`, `icfg_from_state`, `SYNTACTIC_KINDS` |
| `callgraph.py` | 97 | the result object | `CallGraph`, `build_callgraph`, `node_label` |
| `export.py` | 184 | JSON and Graphviz output | `to_dict`, `to_json`, `icfg_dict`, `to_dot`, `to_icfg_dot` |
| `persist.py` | 384 | `State` / `Facts` to JSON and back | `save_state`, `load_state`, `state_to_dict`, `state_from_dict`, `facts_to_dict`, `facts_from_dict`, `CODECS`, `StateDoc`, `Row` |
| `__init__.py` | 86 | public API | `analyze_source`, `analyze_file`, re-exports in `__all__` |
| `__main__.py` | 98 | command line | `main`, `print_graph` |

**Reading `transfer.py`:** `State` comes first (tables, joins, MRO, `resolve`). `Interp`
follows, split by banner comments:

- `names`: `bind`, `lookup`, attribute writes, `invoke_dunder`, `iterate`
- `calls`: `bind_args`, `instantiate`, `apply`
- `expressions`: `e_<Expr>`, `attribute`
- `statements`: `s_<Stmt>`
- `control flow (flow mode)`: `_path`, `_meet`, `_loop`, `s_If` / `s_While` / `s_Try`

`analyze` is last.

## Data types (`model.py`)

**Ids.** `FuncId(qualname, file, lineno, col)` identifies one def, async def, lambda or module
body (`<module>`). `ClassId` has the same fields. `render(style)` returns `full`
(`file:line:col:qualname`, the unique `uid`), `module` (`pkg.mod.qualname`) or `short`
(`qualname`).

**Tokens.** An abstract value `Vals` is a `frozenset` of tokens, and `EMPTY` means nothing
callable is known. Each token has a `depth`, which is not part of its equality, used to bound
nesting.

| Token | Stands for | Created in |
|---|---|---|
| `Closure(func, parent)` | function `func` created while node `parent` ran (`None`: unknown) | `transfer.mk_closure`, from `s_FunctionDef` and `e_Lambda` |
| `Builtin(name, bound, bound_kw)` | a callable modelled in `stubs.py`, or a stub module; `bound` holds pre-bound args (receiver, partial args) | `Interp.lookup` (builtin names), `s_Import*`, stubs |
| `ClassObj(cls)` | a class object | `Interp.s_ClassDef` |
| `Instance(cls, alloc)` | objects of `cls` created at allocation site `alloc` | `Interp.instantiate`, `object.__new__` stub, `except ... as e` |
| `Box(kind, alloc)` | a list, dict or set created at `alloc`; what is stored into it later lives in `State.heap` | literals, comprehensions, `list()` / `dict()` / `set()` |
| `BoundMethod(func, receiver)` | `func` with `receiver` bound as first argument | `transfer._bind_attr` on attribute reads |
| `ClassMethod(func)`, `StaticMethod(func)` | the descriptors | their stubs; also implied for `__init_subclass__` and `__new__` in class bodies |
| `Super(start, receiver)` | a `super()` proxy | `super` stub |
| `Property(fget, fset, fdel)` | a property descriptor | `property` stub, `.getter` / `.setter` / `.deleter` |

`Alloc(func, line, col)` is an allocation site.

**Analysis units.**

- `Ctx(params, parent, merged)` is a context.
  - `params`: sorted `(name, tokens)` pairs, only for parameters whose value is non-empty.
  - `parent`: the node in which a closure was defined.
  - `merged`: marks a widened context; its entry state is stored in `State.entry`.
- `Node(func, ctx)` is one analysed instance of a function. The root is
  `Node(prog.module, Ctx())`.
- `Site(caller, line, col, text, kind)` is a place where a call happens.
  - `call` and `decorator` are *syntactic*: they are CFG call points, and
    `extract.call_sites` lists them.
  - The *implicit* kinds are created by `transfer` and appear only in edges and
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

## Solver state (`transfer.State`)

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
| `extern` | `FuncId -> Env` | writes to a function's locals from inner scopes (`global`, `nonlocal`) |
| `bases`, `metaclasses` | `ClassId -> ...` | direct user bases and metaclasses (`State.mro` computes and caches the MRO) |
| `changed` | `bool` | set by any write that grew something during the current round |

Writes go through joining helpers, all of which set `changed`: `register`, `resolve`,
`add_class_attr`, `add_inst_attr`, `add_heap`, `add_extern`, `add_bases`, `add_metaclass`.
`solver.step` joins `envs`, `ret` and `edges`.

## Where to look

| Question | Look at |
|---|---|
| Which callees does a call reach, and what does it return? | `transfer.Interp.apply` |
| How are arguments bound (defaults, `*args`, `**kwargs`, wrong arity)? | `Interp.bind_args`, `Interp.default_value` |
| How is a name resolved (local, closure, module, builtin)? | `Interp.lookup`, `Interp.bind`, `Interp.nonlocal_owner` |
| What value does a `def` or `lambda` produce? | `Interp.s_FunctionDef`, `Interp.e_Lambda`, `transfer.mk_closure` |
| How is `obj.attr` read (methods, descriptors, property, `__getattr__`, `super`)? | `Interp.attribute`, `transfer._bind_attr` |
| How is `obj.attr = v` written? | `Interp.set_attr`, `Interp.invoke_data_descriptor_set` |
| What happens when a class is called? | `Interp.apply` (the `ClassObj` branch, metaclass `__call__`), `Interp.instantiate` |
| What happens at a `class` statement? | `Interp.s_ClassDef` |
| Which dunders does an operator call? | `Interp.e_BinOp`, `e_UnaryOp`, `e_Compare`, `e_Subscript`, `e_FormattedValue`, `s_AugAssign`, `*_DUNDERS` |
| How are iteration and `with` handled? | `Interp.iterate`, `_iter_expr`, `s_For`, `s_With` |
| How are containers handled (append, `d[k] = v`, unpacking, `*xs`)? | `Interp._mutate_container`, `store`, `assign`, `State.deref` |
| How do branches, loops, `try` and `match` work in flow mode? | `Interp.block`, `_path`, `_meet`, `_loop`, `s_Try`, `s_Match` |
| When is a new context made, and when is it widened? | `State.resolve`, `transfer.mk_closure` (`MAX_DEPTH`) |
| How are the MRO and class attribute lookups computed? | `State.mro`, `State.lookup_class_attr` |
| How does a builtin or stdlib callable behave? | `stubs.py` (the `@stub` registry) |
| What does one round do, and what is the global loop? | `solver.step`, `pipeline.run_passes` |
| What are the roots, and what does `include_unreachable` do? | `solver.init_state` |
| How are locals, signatures and qualnames computed? | `extract.build_program`, `scope_locals`, `make_sig` |
| What CFG does a statement get? | `cfg.Builder.s_<Stmt>`, `Builder.expr` |
| How are ICFG calls and returns wired? | `icfg.ICFG.call_edges`, `return_edges`, `build_icfg` |
| How are labels like `f[x={g}]` built? | `callgraph.node_label`, `model.token_label` |
| What do the JSON and DOT outputs look like? | `export.to_dict`, `to_dot`, `to_icfg_dot` |
| What is the saved state layout? | `persist.StateDoc`, `persist.Row` |

## Invariants

- **Results only grow.** Every write to `State` is a join. Re-solving a fixpoint therefore
  changes nothing (`State.same_result`), a saved state can be resumed, and passes can be
  chained, for example reachable code first and then `include_unreachable=True`.
- **One program version per state.** `Program.fingerprint` (file name + source) is checked by
  `solver.init_state`, `pipeline.run_passes` and `persist.state_from_dict`. A resumed state
  keeps its own `max_ctx` and `flow`, and asking for different values raises `ValueError`.
- **The CFG and the interpreter agree on call sites.** `extract.call_sites` reads `CFG.calls`,
  and the interpreter builds the same `Site` with `extract.make_site`. `icfg.build_icfg`
  raises if a `call` or `decorator` edge has no CFG point.
- **Dead code is still analysed.** Statements after a `return` and both branches of every `if`
  are interpreted, so their call sites have edges. Functions that are never called are
  analysed only with `include_unreachable`.
- **Values are immutable.** Tokens, `Ctx` and `Node` are frozen dataclasses with cached
  hashes, so `State.copy()` copies only the containers.
