//! Worklist solver: re-analyse only the nodes whose inputs changed.
//!
//! The round solver (`solver::step`) analyses every node in every round, so a program whose
//! graph settled long ago still pays for all its nodes each time one value grows. This solver
//! keeps a queue instead. While a node is analysed, the `State` records which parts of the
//! state it reads (`DepKey`: return values, summaries, class and instance attributes, the
//! heap, ...). When a join grows one of those parts, the nodes that read it are queued again,
//! and newly registered nodes are queued too.
//!
//! When the queue is empty, the decorator sites that are still unresolved are confirmed as the
//! identity (as `solver::step` does after a quiet round), which queues their nodes. Last, one
//! full round (`solver::step`) confirms the fixpoint: if it still changes something, a read was
//! not tracked, which is reported in debug builds, and solving continues with every node
//! queued. The solver therefore stops on the same condition as the round solver: a full round
//! that changes nothing.
//!
//! Results equal the round solver's whenever no function runs out of contexts: the visiting
//! order only matters for which contexts are created before a function's budget is used up.

use std::collections::VecDeque;

use rustc_hash::FxHashSet;

use crate::frontend::Program;
use crate::model::NodeId;

use super::solver::{step, visit};
use super::state::{Deps, State};

/// Queue `n` unless it is queued already.
fn enqueue(queue: &mut VecDeque<NodeId>, queued: &mut FxHashSet<NodeId>, n: NodeId) {
    if queued.insert(n) {
        queue.push_back(n);
    }
}

/// Run `state` to its fixpoint, visiting only nodes whose inputs changed.
///
/// `state.changed` is true afterwards if anything grew. A second call on the result changes
/// nothing (it amounts to one full round).
pub fn solve_worklist(prog: &Program, state: &mut State) {
    let mut any_change = false;
    state.deps = Some(Box::<Deps>::default());
    let mut queue: VecDeque<NodeId> = VecDeque::new();
    let mut queued: FxHashSet<NodeId> = FxHashSet::default();
    for &n in &state.order {
        enqueue(&mut queue, &mut queued, n);
    }
    loop {
        while let Some(n) = queue.pop_front() {
            queued.remove(&n);
            // only the decorator sites of the node's latest run are pending
            state.pending_decos.retain(|(m, _)| *m != n);
            state.changed = false;
            if let Some(d) = state.deps.as_deref_mut() {
                d.current = Some(n);
            }
            visit(prog, state, n);
            any_change |= state.changed;
            let Some(d) = state.deps.as_deref_mut() else {
                break;
            };
            d.current = None;
            for key in std::mem::take(&mut d.dirty) {
                if let Some(readers) = d.readers.get(&key) {
                    for &r in readers {
                        enqueue(&mut queue, &mut queued, r);
                    }
                }
            }
            for r in std::mem::take(&mut d.registered) {
                enqueue(&mut queue, &mut queued, r);
            }
        }
        // the queue is empty: confirm the decorator sites that are still unresolved
        let new: Vec<_> = state.pending_decos.iter().filter(|d| !state.unknown_decos.contains(*d)).copied().collect();
        if !new.is_empty() {
            for &(n, _) in &new {
                enqueue(&mut queue, &mut queued, n);
            }
            state.unknown_decos.extend(new);
            any_change = true;
            continue;
        }
        // one full round confirms the fixpoint
        step(prog, state);
        if let Some(d) = state.deps.as_deref_mut() {
            d.dirty.clear();
            d.registered.clear();
        }
        if !state.changed {
            break;
        }
        any_change = true;
        if cfg!(debug_assertions) {
            eprintln!("callme: worklist missed a dependency; continuing with every node queued");
        }
        for &n in &state.order {
            enqueue(&mut queue, &mut queued, n);
        }
    }
    state.deps = None;
    state.pending_decos.clear();
    state.changed = any_change;
}
