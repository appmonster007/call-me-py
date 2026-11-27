# How the analysis works

How callme decides which functions a call can reach. Each section opens with a small program.
Symbols refer to `callme/`; [architecture.md](architecture.md) has the map.

## 1. Values are sets of callables

```python
g = square if flag else double
g(3)                        # g = {square, double}  ->  edges to both
```

`transfer.Interp` evaluates every expression to an abstract value (`model.Vals`), a frozenset
of tokens. A token is anything that can be called or can lead to a call: a closure, a builtin
stub, a class, an instance, a container, a bound method, a descriptor (see the
[token table](architecture.md#data-types-modelpy)). Numbers, strings and other data evaluate
to the empty set and are not tracked, so conditions are never decided and both branches of an
`if` always run.

A call adds an edge to every function in the callee's value whose signature accepts the
arguments. `Interp.bind_args` drops callees with the wrong arity. A call whose callee value is
empty, such as `print(...)` or anything imported from an unknown module, adds no edge.

## 2. Contexts: one analysis per entry state

```python
def apply(f, v): return f(v)
apply(square, 1)            # analysed as apply[f={square}]  ->  square
apply(double, 2)            # analysed as apply[f={double}]  ->  double
```

A function is analysed once per **context** (`model.Ctx`). The context holds the values of
the parameters that contain tokens. Parameters with empty values are not recorded, so
`apply(square, 1)` and `apply(square, 2)` share one context. A `Node` is a pair (function,
context). Return values (`State.ret`) and edges are kept per node, so each call of `apply`
returns only what its own `f` returns.

For closures, the context also records the node that created the function:

```python
def make(f):
    def inner(x): return f(x)
    return inner
make(square)(1)             # inner created by make[f={square}]  ->  square only
make(double)(2)
```

`def inner` evaluates to `Closure(inner, parent=<make[f={square}]>)`. When `inner` reads `f`,
`Interp.lookup` follows `ctx.parent` to the environment of the node that created it.

## 3. The global fixpoint

`solver.solve` calls `pipeline.run_passes`, which repeats `solver.step` in rounds:

1. **Roots** (`solver.init_state`). The root is the module body, `Node(<module>, Ctx())`. With
   `include_unreachable`, every other function is also a root, with an empty context (its
   parent is the module node for top-level functions). This way functions that are never
   called still get their edges.
2. **A round** runs `transfer.analyze` on every node in `State.order`. It joins each node's
   environment summary, return value and edges into the `State`.
3. **Calls during a round.** A call with a new entry state registers a new node
   (`State.resolve`). The call evaluates to whatever `State.ret` holds for the callee so far,
   which is nothing on the first visit, so later rounds refine it.
4. **Stopping.** Solving ends after a round in which nothing changed (`State.changed`). After
   `max_rounds` rounds (10 000) it raises `RuntimeError` instead.

Values only grow and the abstract domain is finite (§4), so the loop terminates.

## 4. Keeping it finite

| Bound | Default | Past it |
|---|---|---|
| contexts per function (`max_contexts`, `--max-contexts`) | 16 | further calls go to one widened node `f[*]` (`Ctx(merged=True)`), whose entry state is the union of all calls that reach it (`State.entry`); `0` makes the analysis context-insensitive |
| closure parent depth (`transfer.MAX_DEPTH`) | 3 | the closure forgets its defining node (`parent=None`), and its free variables are read from the union over every context of the enclosing function |
| arguments bound in a partial (`stubs.MAX_PARTIAL_ARGS`) | 4 | the partial is widened to `<partial*>`, which passes every bound argument via `*` |
| token nesting inside partial, property, classmethod and staticmethod arguments (`stubs.MAX_PARTIAL_DEPTH`) | 3 | deeper tokens are dropped |

## 5. Names and scopes

`extract.scope_locals` precomputes each function's locals: every name it assigns, defines,
imports, catches or binds in a pattern, minus names declared `global` or `nonlocal`.
`Interp.lookup` then resolves a name in this order:

1. inside a class body: the class namespace being built;
2. a local of the current function: the current environment;
3. the lexical parents (`FuncInfo.parent`; class bodies are skipped). The first function that
   owns the name answers, with the environment of its node on the `ctx.parent` chain, or with
   the union over all of its nodes when the parent is unknown;
4. a builtin that has a stub (`stubs.BUILTIN_NAMES`) and that nothing shadows;
5. otherwise the empty set.

`global x` writes go to `State.extern[module]` and `nonlocal x` writes to
`State.extern[owner]`. The owner adds `extern` to every read of `x`, because any call may have
written it. Only `functools`, `itertools` and `dataclasses` (`stubs.STUB_MODULES`) bind
anything when imported. Other imports bind names with empty values.

## 6. Flow sensitivity

```python
f = square
f(1)                        # square only
f = double
f(2)                        # double only
```

By default (`State.flow`), a body is interpreted once, top to bottom, on the **AST**. The
solver does not use the CFG.

- **Assignment** replaces a local's value (a strong update). `:=` is weak, because it may sit
  in a branch that does not run. `del x` clears `x`.
- **`if`**: both branches run on copies of the environment, which are joined afterwards
  (`Interp._path`, `Interp._meet`).
- **Loops** re-run the body until the environment at the loop head stops growing. The
  environments at `break` and `continue` are collected per loop (`_LoopFrame`).
- **`return`, `raise`, `break`, `continue`** end the current path (`Interp.live = False`).
  Statements after them are still interpreted, so their call sites get edges, but their state
  never flows into live code.
- **`try`**: a handler starts from the union of every state the body passed through, since any
  point may raise. `finally` starts from the union of every state in the whole statement.
  `with` works the same way, because `__exit__` may swallow an exception.
- **`match`**: the cases are alternatives. A case that does not match falls through with
  whatever its pattern already bound.
- **Comprehension variables** are restored afterwards, because they live in their own scope.

Other scopes, such as closures and default arguments, see a node's **summary**
(`Interp.summary`, stored in `State.envs`): every value each local takes anywhere in the body.
A closure defined before `f = double` therefore sees both values. That is sound, because the
closure may be called later.

With `--flow-insensitive` (`flow_sensitive=False`), each local is the union of all its
bindings, and the body is re-run until that union stops growing. `test_flow_refines` checks
that flow-sensitive edges are a subset of flow-insensitive ones.

## 7. Containers and the heap

```python
handlers = []
handlers.append(on_save)    # stored into the list created on line 1
for h in handlers: h()      # -> on_save
```

A container is collapsed into a single value. A list, set or dict literal, a comprehension,
or a call to `list()` / `set()` / `dict()` evaluates to the union of its elements plus a
`Box(kind, alloc)` token for its allocation site. Tuples are immutable and get no `Box`.

What is stored afterwards goes into `State.heap[box]`. The store operations are:

- `append`, `add`, `extend`, `insert`, `setdefault` and `update` (`Interp._mutate_container`);
- `xs[i] = f` (`Interp.assign`, which calls `store`);
- `xs += ys` and `s |= t`.

Reading elements uses `State.deref`, which transitively adds what was stored into every
reachable `Box`. This covers iteration, subscripts, unpacking, `*xs` and stub arguments. Every
alias of a container therefore sees its stores. Contents are not tracked per index or per
key, so `d["a"]()` may call anything stored in `d`. If the receiver has no `Box`, for example a
parameter that received a tuple, the store widens the target expression itself (the `fallback`
case of `Interp.store`).

## 8. Classes and objects

```python
class Dog:
    def speak(self): ...
class Cat:
    def speak(self): ...
for a in [Dog(), Cat()]:
    a.speak()               # Dog.speak and Cat.speak, each bound to its own receiver
```

- **`class` statement** (`Interp.s_ClassDef`). The body runs in a class namespace, and the
  names it binds go into `State.class_attrs[cls]`. Evaluating the bases and the `metaclass=`
  keyword fills `State.bases` and `State.metaclasses`. `State.mro` computes the C3 MRO, with a
  depth-first fallback for inconsistent hierarchies.
- **Calling a class.** If a user metaclass defines `__call__`, that is called. Otherwise
  `Interp.instantiate` runs `__new__`, or creates `Instance(cls, alloc=call site)`, and then
  runs `__init__` on the instances that belong to the class. Instances are kept per allocation
  site, so two `Dog()` calls in different places have separate fields (`State.inst_attrs`).
- **Reading `obj.attr`** (`Interp.attribute`):
  - Instance attributes come first, then the class attribute found along the MRO, bound by
    descriptor rules (`transfer._bind_attr`). A function becomes a `BoundMethod`,
    `classmethod` binds the class, and `staticmethod` returns the plain function.
  - A `property` calls its getter, and a user descriptor has its `__get__` called.
  - If the attribute is known neither statically (`ClassInfo.known_attrs`) nor from the
    analysis, `__getattr__` is called.
  - When `obj` is a class, its own attributes and its metaclass's attributes are found.
- **Writing `obj.attr = v`.** A user `__setattr__` is called if there is one. Otherwise data
  descriptors are tried (`__set__`, a property setter), and then the instance table. Writing
  to a class object updates the class table, so monkey patching `C.m = f` works.
- **Deleting.** `del obj.attr` calls a property deleter or `__delete__`. `del x[k]` calls
  `__delitem__`.
- **`super()`.** The zero-argument form uses the enclosing class and the first parameter, and
  the two-argument form takes both explicitly. Either way, the receiver's MRO is searched after
  the start class. This also works through metaclasses, falling back to `type.__call__`,
  `object.__new__` and `object.__setattr__`.
- **Class creation hooks.** A base's `__init_subclass__`, and a metaclass's `__new__` and
  `__init__`, are called at the class statement (site kind `class`).
- **Other cases.** `@dataclass` adds a synthetic `__init__` that calls `__post_init__`.
  Calling an instance calls its `__call__`.

## 9. Implicit calls

`Interp.invoke_dunder` calls a dunder on every `Instance` token whose class (via its MRO)
defines it.

| Syntax | Dunders called | Site kind |
|---|---|---|
| `a + b` and the other 12 binary operators | `__add__`, else the reflected `__radd__` on `b` | `op` |
| `a += b` | `__iadd__`, else `__add__` | `op` |
| `-a`, `+a`, `~a` | `__neg__`, `__pos__`, `__invert__` | `op` |
| `not a` | `__bool__`, else `__len__` | `op` |
| `a < b`, `==`, `!=`, `<=`, `>`, `>=` | `__lt__`, else the reflected `__gt__` on `b` | `op` |
| `x in c` | `__contains__` on `c` | `op` |
| `a[k]`, `a[k] = v`, `del a[k]` | `__getitem__`, `__setitem__`, `__delitem__` | `op` |
| `f"{a}"`, `f"{a!s}"`, `f"{a!r}"` | `__format__`, then `__str__`, then `__repr__`; for `!s`, `__str__` then `__repr__`; for `!r`, `__repr__` | `op` |
| `for x in a`, comprehensions | `__iter__` then `__next__` (`__aiter__` / `__anext__` when async) | `iter` |
| `with a as x` | `__enter__`, `__exit__` (`__aenter__` / `__aexit__` when async) | `with` |
| reading, writing or deleting `obj.attr` | property `fget` / `fset` / `fdel`, `__get__`, `__set__`, `__delete__`, `__getattr__`, `__setattr__` | `attr` |
| `obj(...)` | `__call__` | `call` |
| `class C(Base, metaclass=M)` | `__init_subclass__`, `M.__new__`, `M.__init__` | `class` |
| `len`, `str`, `repr`, `bool`, `hash`, `iter`, `next`, `reversed`, `sorted`, `min`, `max` | the matching dunder (`__len__`, `__str__`, ..., `__lt__`) | `call` (via the stub) |

## 10. Stubs for builtins and the standard library

No C code is analysed. Instead, `stubs.py` replays on abstract values what a builtin does with
its arguments. It invokes callbacks through `Interp.apply` *at the call site of the
higher-order function*, so `map(f, xs)` inside `main` gives the edge `main -> f`. This matches
a traced run, where C frames are invisible.

| Group | Names |
|---|---|
| higher-order | `map`, `filter`, `sorted`, `min`, `max`, `list.sort` (`key=`), `functools.reduce`, `itertools.accumulate` / `starmap` / `groupby` / `filterfalse` / `takewhile` / `dropwhile` |
| wrappers | `functools.partial` (flattened and bounded), `functools.cache` / `lru_cache` / `wraps` / `update_wrapper` |
| containers, pass-through | `list`, `set`, `dict` (each makes a new `Box`), `tuple`, `frozenset`, `enumerate`, `zip`, `itertools.chain` / `cycle` / `islice`, `dict.get`, and the `.values` / `.items` / `.keys` / `.copy` / `.pop` / `.popitem` methods |
| dunder triggers | `len`, `str`, `repr`, `bool`, `hash`, `iter`, `next`, `reversed` |
| classes | `classmethod`, `staticmethod`, `property`, `super`, `object`, `type`, `dataclasses.dataclass` |
| internal (`<...>` and dotted) | `<partial>`, `<partial*>`, `<identity>`, `<dataclass>`, `<dataclass_init>`, `<property_getter>` / `_setter` / `_deleter`, `type.__call__`, `object.__new__`, `object.__setattr__` |

A builtin name resolves to its stub only when no user binding shadows it. A method stub
(`stubs.METHOD_STUBS`) applies when nothing else on the receiver matched the attribute.

## 11. What is not modelled

- **Other files.** Project imports are not followed, so the graph covers only the code in the
  analysed file.
- **Builtins without a stub**, such as `getattr`, `setattr`, `eval`, `exec`, three-argument
  `type()`, `threading`, `asyncio` and `concurrent.futures`. Calls through them get no edges.
- **Implicit exceptions.** Only explicit `raise` and `assert` and points inside a `try` body
  can raise. In a handler, `except E as e` binds `e` to an instance of the caught classes.
- **Coroutines and generators.** `await e` has the value of `e`, and a generator's `yield`
  values are added to its return value.
- **Order dependence.** Context choice depends on argument values that may still be growing,
  so in rare cases the fixpoint depends on the order of iteration. `ORDER_DEPENDENT` in
  `tests/test_multipass.py` lists them, for example `hof/19_decorators`.
