//! Multi-pass driver: run several analyses over one program to a joint fixpoint.
//!
//! A *pass* owns one state in a shared `Facts` store and may read the states of other passes
//! (e.g. a flow-sensitive pass reads the call graph). `run_passes` steps every pass once per
//! round and stops when no pass changed anything, so passes that feed each other (call graph
//! <-> data flow) are alternated until both are stable.
//!
//! Consistency rules every pass must follow:
//!
//!   * `init` is idempotent: given an existing state it only validates and adds roots;
//!   * `step` only joins into states (results grow monotonically, never shrink);
//!   * states implement `copy_state` and `same_result` (the `PassState` trait).
//!
//! Under these rules running the passes again on their own output returns the same facts,
//! and any pass's output can be fed back as its input.

use std::any::Any;

use indexmap::IndexMap;

use crate::error::{Error, Result};
use crate::frontend::Program;

/// What the driver needs from the state of a pass: a copy, and a comparison of results.
pub trait PassState: Any {
    /// Return an independent copy, which later steps can change without affecting this one.
    fn copy_state(&self) -> Box<dyn PassState>;
    /// Return True if `other` holds the same results as this state.
    fn same_result(&self, other: &dyn PassState) -> bool;
    /// Return the state as `Any`, to downcast it.
    fn as_any(&self) -> &dyn Any;
    /// Return the state as `Any` for writing, to downcast it.
    fn as_any_mut(&mut self) -> &mut dyn Any;
    /// Return the name of the state's type, for error messages.
    fn type_name(&self) -> &'static str;
}

/// An analysis that `run_passes` steps to a joint fixpoint with other passes.
pub trait Pass {
    /// Return the key of the pass's state in `Facts`.
    fn name(&self) -> &str;

    /// Return the pass's state for `prog`: a fresh one, or `state` validated and extended.
    ///
    /// `state` is None on a first run. Given an existing state, `init` only validates it and
    /// adds roots, so calling it again changes nothing. Return an error if the state does not
    /// fit.
    fn init(&self, prog: &Program, facts: &Facts, state: Option<Box<dyn PassState>>) -> Result<Box<dyn PassState>>;

    /// Run one round, joining new results into `facts[self.name()]`; return True if it changed.
    ///
    /// A step may read the states of other passes in `facts`, and it only joins, so results
    /// never shrink.
    fn step(&self, prog: &Program, facts: &mut Facts) -> Result<bool>;
}

/// The states of all passes for one program version, keyed by pass name.
pub struct Facts {
    /// `Program.fingerprint`
    pub program: String,
    /// state by pass name
    pub states: IndexMap<String, Box<dyn PassState>>,
}

impl Facts {
    /// Create an empty store for the program with fingerprint `program`.
    pub fn new(program: String) -> Facts {
        Facts { program, states: IndexMap::new() }
    }

    /// Return True if pass `name` has a state.
    pub fn contains(&self, name: &str) -> bool {
        self.states.contains_key(name)
    }

    /// Return the state of pass `name`, or an error if it is missing or not an `S`.
    pub fn get<S: PassState>(&self, name: &str) -> Result<&S> {
        let state = self.states.get(name).ok_or_else(|| Error::Value(format!("no state for pass {name:?}")))?;
        let tn = state.type_name();
        state
            .as_any()
            .downcast_ref::<S>()
            .ok_or_else(|| Error::Type(format!("state of pass {name:?} is a {tn}, not a {}", short_type::<S>())))
    }

    /// Return the state of pass `name` for writing, or an error if it is missing or not an `S`.
    pub fn get_mut<S: PassState>(&mut self, name: &str) -> Result<&mut S> {
        let state = self.states.get_mut(name).ok_or_else(|| Error::Value(format!("no state for pass {name:?}")))?;
        let tn = state.type_name();
        state
            .as_any_mut()
            .downcast_mut::<S>()
            .ok_or_else(|| Error::Type(format!("state of pass {name:?} is a {tn}, not a {}", short_type::<S>())))
    }

    /// Return a copy holding a copy of every pass state.
    pub fn copy(&self) -> Facts {
        Facts {
            program: self.program.clone(),
            states: self.states.iter().map(|(k, v)| (k.clone(), v.copy_state())).collect(),
        }
    }

    /// Return True if both stores are for the same program and hold the same results.
    pub fn same_result(&self, other: &Facts) -> bool {
        self.program == other.program
            && self.states.len() == other.states.len()
            && self.states.iter().all(|(k, v)| other.states.get(k).is_some_and(|o| v.same_result(o.as_ref())))
    }
}

/// A callback run after each round: `on_round(round, facts)`.
pub type RoundHook<'h> = &'h mut dyn FnMut(usize, &Facts);

/// Return the last path segment of a type's name, e.g. "State".
pub fn short_type<T>() -> &'static str {
    let full = std::any::type_name::<T>();
    full.rsplit("::").next().unwrap_or(full)
}

/// Step all `passes` in rounds until none of them changes, and return the facts.
///
/// Each pass is initialised first (`Pass::init`), then every round steps each pass once, in
/// order. States of passes that are not listed are kept as they are.
///
/// ```text
/// facts       earlier results for the same program to resume from (taken by value; pass a
///             `copy` to keep the original); an error if they were produced for another
///             program or version of it
/// max_rounds  maximum number of rounds before returning `Error::Runtime`
/// on_round    called as `on_round(round, facts)` after each round, the last one included
/// ```
pub fn run_passes(
    prog: &Program,
    passes: &[&dyn Pass],
    facts: Option<Facts>,
    max_rounds: usize,
    mut on_round: Option<RoundHook<'_>>,
) -> Result<Facts> {
    let mut facts = match facts {
        None => Facts::new(prog.fingerprint.clone()),
        Some(f) => {
            if f.program != prog.fingerprint {
                return Err(Error::Value(
                    "facts were produced for a different program (or another version of it)".into(),
                ));
            }
            f
        }
    };
    for p in passes {
        let name = p.name().to_owned();
        let at = facts.states.get_index_of(&name);
        let old = facts.states.shift_remove(&name);
        let new = p.init(prog, &facts, old)?;
        match at {
            Some(i) => {
                facts.states.shift_insert(i, name, new);
            }
            None => {
                facts.states.insert(name, new);
            }
        }
    }
    for rnd in 0..max_rounds {
        let mut changed = false;
        for p in passes {
            changed = p.step(prog, &mut facts)? || changed;
        }
        if let Some(f) = on_round.as_mut() {
            f(rnd, &facts);
        }
        if !changed {
            return Ok(facts);
        }
    }
    let names: Vec<String> = passes.iter().map(|p| format!("'{}'", p.name())).collect();
    Err(Error::Runtime(format!("passes did not converge: [{}]", names.join(", "))))
}
