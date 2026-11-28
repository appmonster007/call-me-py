//! Dependency tracking for the worklist solver: the keys of the state a node's analysis
//! reads, and who read them.

use rustc_hash::{FxHashMap, FxHashSet};

use crate::model::{ClassId, FuncId, NodeId, TokId};

/// A part of the state that a node's analysis may read.
///
/// The worklist solver records, while a node is analysed, which keys it reads; when a join
/// grows a key, the nodes that read it are analysed again.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum DepKey {
    /// the return value of a node
    Ret(NodeId),
    /// the summary environment of a node
    Env(NodeId),
    /// the entry state of a widened node
    Entry(NodeId),
    /// the side-effect writes of a node
    Writes(NodeId),
    /// the set of contexts (nodes) of a function
    Contexts(FuncId),
    /// the summary environments of all the nodes of a function
    FuncEnv(FuncId),
    /// the writes from other scopes into a function
    Extern(FuncId),
    /// the attributes defined on a class itself
    Class(ClassId),
    /// every base and metaclass relation, which the MROs follow
    Hierarchy,
    /// the attributes of an instance token
    Inst(TokId),
    /// the contents of a container token
    Heap(TokId),
}

/// What the worklist solver tracks while it runs: who read which key, and what grew.
#[derive(Clone, Default, Debug)]
pub struct Deps {
    /// the node being analysed, whose reads are recorded
    pub current: Option<NodeId>,
    /// the nodes that read each key
    pub readers: FxHashMap<DepKey, FxHashSet<NodeId>>,
    /// keys that grew since the solver last looked
    pub dirty: Vec<DepKey>,
    /// nodes registered since the solver last looked
    pub registered: Vec<NodeId>,
}
