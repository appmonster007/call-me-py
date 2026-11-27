# Output and file formats

This covers the CLI flags, how functions, contexts and tokens are named, the JSON and DOT
layouts, and saved state files.

## Command line (`callme/__main__.py`)

```
python -m callme FILE [FILE ...] [-o OUT] [options]   # run from the repo root; nothing is written without -o
```

Each `FILE` is an entry: its module body is a root of the analysis, and every project module
it imports is loaded too, transitively. Modules are looked up in each entry's root directory
(the entry's own directory, or the directory above its package), then in each `--path`.

| Flag | Effect |
|---|---|
| `-o`, `--output PATH` | write the graph to `PATH` (`/dev/stdout` prints it) |
| `--path DIR` | also search `DIR` for imported modules; repeatable, like `PYTHONPATH` |
| `--format json\|dot\|icfg` | JSON (the default), a call graph in DOT, or the interprocedural CFG in DOT |
| `--icfg` | JSON only: add the `icfg` key (CFGs plus call and return edges) |
| `--contexts` | DOT only: one node per (function, context) instead of per function |
| `--returns` | DOT only: add dotted return edges from callee to caller |
| `--include-unreachable` | also analyse functions that are never called, and modules that are never imported |
| `--id-style full\|module\|short`, `--short-ids` | how functions are named (see below); `--short-ids` is `--id-style short` |
| `--max-contexts N` | contexts per function before widening (default 16) |
| `--flow-insensitive` | treat each local as the union of all its bindings |
| `--print-graph` | write the lexical nesting edges and every call site to stderr |
| `--save-state PATH` | save the solver state after this run |
| `--load-state PATH` | resume from a state saved for the same, unchanged files |

A two-pass run from the shell, with reachable code first and then everything else:

```sh
python -m callme app.py --save-state s.json -o reachable.json
python -m callme app.py --load-state s.json --include-unreachable -o all.json
```

`--print-graph` output (shown with `--id-style short`):

```
# function identifier graph (lexical parent -> child)
<module> -> square
<module> -> apply
# call sites (caller -> call site @line:col)
<module> -> apply(square, 2) @9:0 [call]
apply -> f(v) @6:11 [call]
```

## Names

**Function ids** come from `FuncId.render`, and `--id-style` / `id_style=` picks the form:

| Style | Form | Example |
|---|---|---|
| `full` (default) | `file:line:col:qualname`, always unique | `app.py:12:4:Outer.make.inner` |
| `module` | `module.qualname` | `app.Outer.make.inner` |
| `short` | `qualname` | `Outer.make.inner` |

`file` is the path exactly as it was given, or for an imported module the path it was found
under. `module` is the module's dotted name relative to its import root: `main.py` is `main`,
and `app/core.py` in package `app` is `app.core`. `analyze_source` and `build_program` have no
import root, so they derive the name from the file name as given (`pkg/m.py` is `pkg.m`).
Qualname rules:

- the module body is `<module>`;
- a nested def is `outer.inner`, and a method is `Class.method`;
- a lambda is `<lambda>@<line>`, prefixed by its enclosing scope;
- a lambda in a def's default arguments is prefixed by that def (`f.<lambda>@3`).

Short ids can collide, for example two lambdas on one line.

**Context labels** (`callgraph.node_label`):

- `f`: no function-valued parameters;
- `f[p={a,b}, q={c}]`: the tokens of each parameter, sorted;
- `f[*]`: a widened context.

Nodes that would get the same label (they differ only in their closure parent) get `#1`,
`#2`, ... appended.

**Token labels** (`model.token_label`), as they appear in contexts:

| Token | Label |
|---|---|
| `Closure` | the function id |
| `Builtin` | the stub name (`map`, `functools.partial`); a partial is `<partial:f>` |
| `ClassObj` | `<class C>` |
| `Instance` | `C()@line:col` (its allocation site) |
| `Box` | `<list@line:col>` |
| `BoundMethod` | `C.m@C()@12:4` (function@receiver) |
| `ClassMethod`, `StaticMethod` | `classmethod(f)`, `staticmethod(f)` |
| `Super` | `super(C, <receiver>)` |
| `Property` | `property(<fget>)` |
| `Module` | `<module pkg.core>` |

## JSON (`export.to_dict`)

Here is the output for this program, trimmed to one entry per list. It was produced with
`--id-style short`.

```python
def square(x):
    return x * x


def apply(f, v):
    return f(v)


apply(square, 2)
```

```json
{
  "file": "tiny.py",
  "modules": [{"name": "tiny", "file": "tiny.py"}],
  "functions": [{"id": "apply", "kind": "def", "module": "tiny", "file": "tiny.py", "line": 5,
                 "col": 0, "name": "apply", "parent": "<module>", "reachable": true}],
  "sites": [{"caller": "apply", "line": 6, "col": 11, "text": "f(v)", "kind": "call",
             "targets": ["square"]}],
  "edges": [{"caller": "apply", "callee": "square"}],
  "contexts": [{"id": "apply[f={square}]", "function": "apply", "context": {"f": ["square"]}}],
  "context_edges": [{"caller": "apply[f={square}]", "callee": "square",
                     "site": {"line": 6, "col": 11, "text": "f(v)", "kind": "call"}}]
}
```

| Key | One entry per | Fields |
|---|---|---|
| `modules` | loaded module, in load order: the entry files (each after its parent packages), then what they import | `name` (dotted), `file` (`null` for a namespace package) |
| `functions` | function of any loaded module, reachable or not | `id`, `kind` (`module` / `def` / `lambda`), `module`, `file`, `line`, `col`, `name` (the qualname), `parent` (id of the lexical parent, or `null`), `reachable` |
| `sites` | syntactic call site of a reachable function, then each implicit site that got an edge | `caller`, `line`, `col`, `text` (at most 60 characters; decorators start with `@`), `kind` (see [architecture.md](architecture.md#data-types-modelpy)), `targets` (callee ids) |
| `edges` | distinct (caller, callee) pair of functions | `caller`, `callee` |
| `contexts` | analysed (function, context) node | `id` (its context label), `function`, `context` (`{param: [token labels]}`, or `{"*": []}` when widened) |
| `context_edges` | context-level call edge | `caller`, `callee` (context labels), `site` (`line`, `col`, `text`, `kind`) |
| `icfg` (with `--icfg`) | | `cfgs`: `{function id: {entry, exit, nodes: [{id, kind, line, col, text}], edges: [{src, dst, kind}]}}` for reachable functions. `edges`: call and return edges `[{kind, src: {context, point}, dst: {context, point}, site}]` |

A site with empty `targets` is a call the analysis could not resolve, such as `print(...)` or
a call into an unknown import. Listing those sites is a quick way to find blind spots.

## DOT

- **`--format dot`** draws one box per function. Unreachable functions are dashed and gray,
  and each edge label lists its call lines (`L12,L15`).
- **`--contexts`** draws one box per context node and one edge per context edge.
- **`--returns`** adds a dotted edge from callee to caller for each call edge.
- **`--format icfg`** (`export.to_icfg_dot`) draws one cluster per context node, holding its
  function's CFG.
  - Node shapes: `entry` / `exit` are ovals, `branch` is a diamond, `loop` a hexagon, `call`
    a light blue box, `after_call` a honeydew box, and everything else a plain box.
  - Edges: `T` / `F` labels on branches; `exc` red dashed; `call_to_return` dotted; `call`
    blue; `return` dark green dashed.
  - Edges at implicit sites have no CFG point, so they are not drawn.

To render: `dot -Tsvg graph.dot -o graph.svg` (Graphviz).

## Saved state (`callme/persist.py`)

`--save-state` and `persist.save_state` write the solver `State` as JSON (`persist.StateDoc`):

- **Header:** `format: "callme-state"`, `version: 5`, `program` (the fingerprint), `max_ctx`,
  `flow_sensitive`. Version 5 added `writes` (per-node side-effect writes); older documents are rejected.
- **`values`:** one table holding every `Node` and token. Children come before parents, and
  each row is tagged by kind: `node`, `clo`, `builtin`, `class`, `inst`, `box`, `bound`,
  `classmethod`, `staticmethod`, `super`, `property`, `module`. `persist.Row` gives each
  row's layout.
  Everything else in the document refers to rows by index.
- **The rest:** `order`, `envs`, `ret`, `entry`, `edges`, `class_attrs`, `inst_attrs`,
  `heap`, `extern`, `writes`, `bases` and `metaclasses` mirror the [`State` fields](architecture.md#solver-state-transferstate).
  Function and class ids are stored as full uids, and loading resolves them against the
  `Program`.

`persist.load_state(prog, path)` and `--load-state` require the same file names and sources,
imported modules included (the fingerprint), and they keep the state's `max_ctx` and flow mode. Asking for a different
value raises `ValueError: max_contexts=4 conflicts with state.max_ctx=16`.

A multi-pass `Facts` store is saved by `persist.facts_to_dict` as
`{"format": "callme-facts", "version", "program", "states": {pass name: document}}`. Each
pass needs a codec in `persist.CODECS`.
