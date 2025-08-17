use chess::ChessMove;
use crate::types::{pack_move, unpack_move, EXACT, ALPHA, BETA, MAX_AB_DEPTH, MATE};

#[derive(Clone, Copy)]
pub struct TTEntry {
    pub key: u64,
    pub depth: i16,
    pub score: i32,
    pub flag: i8,
    pub age: u8,
    pub best: u16,
}
impl Default for TTEntry {
    fn default() -> Self {
        Self { key: 0, depth: -32768, score: 0, flag: EXACT, age: 0, best: 0 }
    }
}

const TT_ASSOC: usize = 4;

pub struct TT {
    buckets: Vec<[TTEntry; TT_ASSOC]>,
    mask: usize,
    pub age: u8,
}
impl TT {
    pub fn new_from_mb(tt_mb: usize) -> Self {
        use std::mem::size_of;
        let entry_sz = size_of::<TTEntry>().max(1);
        let bytes = tt_mb.saturating_mul(1024 * 1024);
        let total_entries = (bytes / entry_sz).max(TT_ASSOC);
        let mut buckets = (total_entries / TT_ASSOC).max(1);

        let mut pow2 = 1usize;
        while (pow2 << 1) <= buckets { pow2 <<= 1; }
        buckets = pow2;

        let mask = buckets - 1;
        let mut vec = Vec::with_capacity(buckets);
        vec.resize(buckets, [TTEntry::default(); TT_ASSOC]);
        Self { buckets: vec, mask, age: 0 }
    }
    #[inline] fn idx(&self, key: u64) -> usize { (key as usize) & self.mask }

    pub fn probe(&self, key: u64) -> Option<TTEntry> {
        let bucket = &self.buckets[self.idx(key)];
        let mut best: Option<TTEntry> = None;
        for &e in bucket.iter() {
            if e.key == key && e.depth > -32768 {
                if best.map_or(true, |b| e.depth > b.depth) { best = Some(e); }
            }
        }
        best
    }

    pub fn store(&mut self, key: u64, depth: i32, score: i32, flag: i8, best: Option<ChessMove>) {
        let i = self.idx(key);
        let bucket = &mut self.buckets[i];

        for e in bucket.iter_mut() {
            if e.key == key {
                *e = TTEntry { key, depth: depth as i16, score, flag, age: self.age,
                               best: best.map(pack_move).unwrap_or(0) };
                return;
            }
        }

        // Prefer evicting shallower, then *older* on tie (quality > speed).
        let mut replace_at = 0usize;
        for (j, e) in bucket.iter().enumerate() {
            let r = &bucket[replace_at];
            let worse_depth = e.depth < r.depth;
            let same_depth_older = e.depth == r.depth && r.age.wrapping_sub(e.age) > 0;
            if worse_depth || same_depth_older { replace_at = j; }
        }
        bucket[replace_at] = TTEntry {
            key, depth: depth as i16, score, flag, age: self.age,
            best: best.map(pack_move).unwrap_or(0),
        };
    }
}

// Mate score normalization
#[inline]
pub fn to_tt(score: i32, ply: i32) -> i32 {
    if score >= MATE - MAX_AB_DEPTH { score + ply }
    else if score <= -MATE + MAX_AB_DEPTH { score - ply }
    else { score }
}
#[inline]
pub fn from_tt(score: i32, ply: i32) -> i32 {
    if score >= MATE - MAX_AB_DEPTH { score - ply }
    else if score <= -MATE + MAX_AB_DEPTH { score + ply }
    else { score }
}
