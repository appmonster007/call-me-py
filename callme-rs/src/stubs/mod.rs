//! Models of builtin and stdlib callables.
//!
//! A stub stands for a callable the analysis cannot read the source of. Each one says which
//! callables it calls back and what it returns, as values. `registry` holds the machinery
//! (`Registry`, `Args`, `Host`); every other module models one library and registers its stubs
//! with a `register` function.
//!
//! The registry keeps registration order, and `from m import *` binds a stub module's names in
//! that order (`Interp::s_import_from`), so the modules are registered in a fixed order; add
//! new ones at the end.

pub mod asyncio;
pub mod builtins;
pub mod collections;
pub mod containers;
pub mod contextlib;
pub mod dataclasses;
pub mod functools;
pub mod futures;
pub mod itertools;
pub mod objects;
pub mod registry;
pub mod threading;

use std::sync::LazyLock;

use indexmap::IndexSet;
use rustc_hash::FxBuildHasher;

use crate::syntax::Name;

pub use registry::{
    Args, Host, HostState, Kws, MAX_PARTIAL_ARGS, MAX_PARTIAL_DEPTH, METHOD_STUBS, Registry, STUB_MODULES,
    STUB_PACKAGES, StubFn, boxes_of, builtin, builtin_val, call, fold, shallow,
};

/// Every stub, by name, in registration order.
pub static STUBS: LazyLock<Registry> = LazyLock::new(|| {
    let mut r = Registry::default();
    builtins::register(&mut r);
    objects::register(&mut r);
    dataclasses::register(&mut r);
    containers::register(&mut r);
    functools::register(&mut r);
    itertools::register(&mut r);
    contextlib::register(&mut r);
    collections::register(&mut r);
    threading::register(&mut r);
    futures::register(&mut r);
    asyncio::register(&mut r);
    r
});

/// The builtin names: stubs without a dot that are not internal (`<...>`), in registration order.
pub static BUILTIN_NAMES: LazyLock<IndexSet<Name, FxBuildHasher>> =
    LazyLock::new(|| STUBS.names().filter(|n| !n.contains('.') && !n.starts_with('<')).collect());
