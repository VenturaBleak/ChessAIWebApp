// ab_engine_rust/src/lib.rs

pub mod types;
pub mod eval;
pub mod ordering;
pub mod tt;
pub mod search;

// (Optional) nice re-exports so main.rs can `use engine::search::Search;` etc.
pub use types::*;
pub use eval::ClassicalEval;
pub use ordering::{Ordering as MoveOrdering, Killers, History};
pub use tt::TT;
pub use search::Search;
