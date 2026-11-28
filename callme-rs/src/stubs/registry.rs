//! The stub registry: `Registry`, the arguments a stub gets, and what it may call.
//!
//! A stub is a function `(it: &mut dyn Host, a: &Args, site: Site) -> Vals` registered under
//! one or more names. `Host` and `HostState` are traits for the parts of the interpreter and
//! of the solver state that stubs use, so this package never depends on `analysis`. `call`
//! and `fold` make calls back into the program; `shallow` and `boxes_of` are helpers shared by
//! several stub modules.

use std::sync::{Arc, LazyLock};

use indexmap::{IndexMap, IndexSet};
use rustc_hash::FxBuildHasher;

use crate::frontend::FuncInfo;
use crate::model::{ClassId, EMPTY, Site, TokId, Token, Vals, Values};
use crate::syntax::Name;

/// Keyword arguments by name, in the order they were given.
pub type Kws = IndexMap<Name, Vals, FxBuildHasher>;

/// The stdlib modules that have stubs.
pub static STUB_MODULES: LazyLock<IndexSet<Name, FxBuildHasher>> = LazyLock::new(|| {
    [
        "asyncio",
        "builtins",
        "collections",
        "concurrent.futures",
        "contextlib",
        "dataclasses",
        "functools",
        "importlib",
        "itertools",
        "threading",
    ]
    .into_iter()
    .map(Name::new)
    .collect()
});

/// The parent packages of dotted stub modules, e.g. `concurrent`.
pub static STUB_PACKAGES: LazyLock<IndexSet<Name, FxBuildHasher>> = LazyLock::new(|| {
    let mut out = IndexSet::default();
    for m in STUB_MODULES.iter() {
        let parts: Vec<&str> = m.split('.').collect();
        for i in 1..parts.len() {
            out.insert(Name::new(&parts[..i].join(".")));
        }
    }
    out
});

/// Attribute name -> stub, for methods of builtin containers (the receiver is bound first).
pub static METHOD_STUBS: LazyLock<IndexMap<Name, Name, FxBuildHasher>> = LazyLock::new(|| {
    let mut m = IndexMap::default();
    m.insert(Name::new("sort"), Name::new("list.sort"));
    m.insert(Name::new("get"), Name::new("dict.get"));
    for n in ["values", "items", "keys", "copy", "pop", "popleft", "popitem"] {
        m.insert(Name::new(n), Name::new("<elements>"));
    }
    m
});

/// Bound arguments before a partial is widened to `<partial*>`.
pub const MAX_PARTIAL_ARGS: usize = 4;
/// Nesting of tokens inside a partial's arguments.
pub const MAX_PARTIAL_DEPTH: u32 = 3;

/// Abstract arguments of one stub call, with the token's bound arguments prepended.
///
/// Containers are already dereferenced, so an argument's value includes everything stored
/// into the containers it holds; "the elements of `xs`" is just the value of `xs`.
///
/// ```text
/// pos     values of the positional arguments, in order
/// star    values passed through `*args`, or None
/// kws     values of the keyword arguments, by name
/// kwstar  values passed through `**kwargs`, or None
/// ```
#[derive(Clone, Debug, Default)]
#[allow(missing_docs)]
pub struct Args {
    pub pos: Vec<Vals>,
    pub star: Option<Vals>,
    pub kws: Kws,
    pub kwstar: Option<Vals>,
}

impl Args {
    /// Return the value of positional argument `i`, or of keyword `kw` if it is not given.
    ///
    /// If neither is given, return what `*args` and `**kwargs` may supply, which is `EMPTY`
    /// when there are none.
    pub fn get(&self, v: &mut Values, i: usize, kw: Option<Name>) -> Vals {
        if let Some(&x) = self.pos.get(i) {
            return x;
        }
        if let Some(k) = kw
            && let Some(&x) = self.kws.get(&k)
        {
            return x;
        }
        v.union(self.star.unwrap_or(EMPTY), self.kwstar.unwrap_or(EMPTY))
    }

    /// Return the value of keyword argument `name`, plus what `**kwargs` may supply.
    pub fn kw(&self, v: &mut Values, name: Name) -> Vals {
        v.union(self.kws.get(&name).copied().unwrap_or(EMPTY), self.kwstar.unwrap_or(EMPTY))
    }

    /// Return the union of the positional arguments from index `i` on, `*args` included.
    pub fn rest(&self, v: &mut Values, i: usize) -> Vals {
        let u = v.union_all(self.pos.iter().skip(i).copied());
        v.union(u, self.star.unwrap_or(EMPTY))
    }

    /// Return the union of every argument: positional, keyword, `*args` and `**kwargs`.
    pub fn all(&self, v: &mut Values) -> Vals {
        let r = self.rest(v, 0);
        let k = v.union_all(self.kws.values().copied());
        let rk = v.union(r, k);
        v.union(rk, self.kwstar.unwrap_or(EMPTY))
    }

    /// Return positional argument `i` (`a.pos[i]`), or `EMPTY` if there are fewer.
    ///
    /// Stubs use this for arguments their token always binds; Python would raise
    /// `IndexError` where this returns `EMPTY`.
    pub fn p(&self, i: usize) -> Vals {
        self.pos.get(i).copied().unwrap_or(EMPTY)
    }

    /// Return the positional arguments from index `i` on (`a.pos[i:]`).
    pub fn from(&self, i: usize) -> &[Vals] {
        self.pos.get(i..).unwrap_or(&[])
    }
}

/// The parts of `analysis::State` that stubs use.
pub trait HostState {
    /// Return the value table.
    fn values(&mut self) -> &mut Values;
    /// Return the attributes defined on class `cls` itself, in definition order.
    fn class_attr_items(&mut self, cls: ClassId) -> Vec<(Name, Vals)>;
    /// Return True if class `cls` itself defines attribute `name`.
    fn has_own_class_attr(&mut self, cls: ClassId, name: Name) -> bool;
    /// Return the method resolution order of `cls`, starting with `cls` itself.
    fn mro(&mut self, cls: ClassId) -> Arc<[ClassId]>;
    /// Join `vals` into attribute `name` of class `cls`.
    fn add_class_attr(&mut self, cls: ClassId, name: Name, vals: Vals);
    /// Join `vals` into attribute `name` of instance token `inst`.
    fn add_inst_attr(&mut self, inst: TokId, name: Name, vals: Vals);
    /// Join `vals` into what is stored in container token `b`.
    fn add_heap(&mut self, b: TokId, vals: Vals);
    /// Return attribute `name` of the first class in `cls`'s MRO (after `after`) that has it.
    fn lookup_class_attr(&mut self, cls: ClassId, name: Name, after: Option<ClassId>) -> Vals;
}

/// The parts of `analysis::Interp` that stubs use.
///
/// A trait keeps this package free of dependencies on `analysis`, which depends on it. Calls
/// made through it come from the node whose code called the stub.
pub trait Host {
    /// Return the `FuncInfo` of the function being analysed.
    fn info(&self) -> &FuncInfo;
    /// Return True while default values are evaluated, when no state may be written.
    fn readonly(&self) -> bool;
    /// Return the global analysis state.
    fn state(&mut self) -> &mut dyn HostState;
    /// Return the value table.
    fn values(&mut self) -> &mut Values {
        self.state().values()
    }
    /// Return the value of variable `name` in the current scope.
    fn lookup(&mut self, name: Name) -> Vals;
    /// Call every callable in `callee` with these arguments at `site`; return the results.
    fn apply(
        &mut self,
        callee: Vals,
        pos: &[Vals],
        star: Option<Vals>,
        kws: &Kws,
        kwstar: Option<Vals>,
        site: Site,
    ) -> Vals;
    /// Create instances of class token `cls` as `type.__call__` does, and return them.
    fn instantiate(
        &mut self,
        cls: TokId,
        pos: &[Vals],
        star: Option<Vals>,
        kws: &Kws,
        kwstar: Option<Vals>,
        site: Site,
    ) -> Vals;
    /// Call special method `name` on the instances in `recv`; return (found, results).
    fn invoke_dunder(&mut self, recv: Vals, name: Name, pos: &[Vals], site: Site) -> (bool, Vals);
    /// Call the data descriptor setters in `cls_vals` for `obj`; return True if any ran.
    fn invoke_data_descriptor_set(&mut self, obj: TokId, cls_vals: Vals, vals: Vals, site: Site) -> bool;
    /// Return the values that iterating `iterable` yields, calling `__iter__` and `__next__`.
    fn iterate(&mut self, iterable: Vals, site: Site, is_async: bool) -> Vals;
    /// Return every module of the program, and run each, as an import of an unknown name may.
    fn import_any(&mut self) -> Vals;
}

/// A stub function.
pub type StubFn = fn(&mut dyn Host, &Args, Site) -> Vals;

/// Stub functions by name, in registration order.
///
/// A name without a dot is a builtin; it is also in `BUILTIN_NAMES`, and found by name
/// unless user code shadows it. `module.name` is reached through `import module` or
/// `from module import name`, and its module must be in `STUB_MODULES`. A name in angle
/// brackets, such as `<partial>`, is an internal token that user code cannot name.
#[derive(Default)]
pub struct Registry {
    /// stub by name
    map: IndexMap<Name, StubFn, FxBuildHasher>,
}

impl Registry {
    /// Register `f` under every name in `names`.
    pub fn add(&mut self, names: &[&str], f: StubFn) {
        for n in names {
            self.map.insert(Name::new(n), f);
        }
    }

    /// Return the stub registered as `name`.
    pub fn get(&self, name: Name) -> Option<StubFn> {
        self.map.get(&name).copied()
    }

    /// Return True if a stub is registered as `name`.
    pub fn contains(&self, name: Name) -> bool {
        self.map.contains_key(&name)
    }

    /// Return every registered name, in registration order.
    pub fn names(&self) -> impl Iterator<Item = Name> + '_ {
        self.map.keys().copied()
    }
}

/// Return a `Builtin` token with these bound arguments.
pub fn builtin(v: &mut Values, name: Name, bound: Vec<Vals>, bound_kw: Vec<(Name, Vals)>) -> TokId {
    v.tok(Token::Builtin { name, bound: Arc::from(bound), bound_kw: Arc::from(bound_kw) })
}

/// Return the value holding only a `Builtin` token with these bound arguments.
pub fn builtin_val(v: &mut Values, name: Name, bound: Vec<Vals>) -> Vals {
    let t = builtin(v, name, bound, Vec::new());
    v.single(t)
}

/// Call every callable in `f` with positional `args` at `site`, and return their results.
///
/// The call is made from the node that called the stub, so its edges go from the Python
/// function that called the builtin, as in a traced run where C frames are invisible.
/// `star` is passed as `*args`. With an empty `f` nothing is called and `EMPTY` is returned.
pub fn call(it: &mut dyn Host, f: Vals, args: &[Vals], site: Site, star: Option<Vals>) -> Vals {
    if f == EMPTY {
        return EMPTY;
    }
    it.apply(f, args, star, &Kws::default(), None, site)
}

/// Return the accumulator of `acc = f(acc, element)`, iterated until it stops growing.
///
/// Each round calls `f` with the accumulator so far and the elements, and joins the result
/// into it; this models `functools.reduce` and `itertools.accumulate`.
pub fn fold(it: &mut dyn Host, f: Vals, mut acc: Vals, elems: Vals, site: Site) -> Vals {
    loop {
        let r = call(it, f, &[acc, elems], site, None);
        if it.values().subset(r, acc) {
            return acc;
        }
        acc = it.values().union(acc, r);
    }
}

/// Drop tokens nested `MAX_PARTIAL_DEPTH` deep from `vals`, so nested tokens stay finite.
pub fn shallow(v: &mut Values, vals: Vals) -> Vals {
    v.filter(vals, |v, t| v.depth(t) < MAX_PARTIAL_DEPTH)
}

/// Return every `Box` token of `kind` present in `vals`.
pub fn boxes_of(v: &Values, vals: Vals, kind: Name) -> Vec<TokId> {
    v.slice(vals).iter().copied().filter(|&t| matches!(v.get(t), Token::Box { kind: k, .. } if *k == kind)).collect()
}

/// Return the value holding `toks`.
pub fn vals_of(v: &mut Values, toks: Vec<TokId>) -> Vals {
    v.of(toks)
}
