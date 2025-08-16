use chess::{Board, BoardStatus, ChessMove, Color, MoveGen, Piece, Square};
use std::cmp::Reverse;
use std::collections::HashMap;
use std::env;
use std::io::{self, BufRead, Write};
use std::str::FromStr;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::thread;
use std::time::{Duration, Instant};

// ---------------------------
// Tunables (kept exactly as specified)
// ---------------------------
const DEFAULT_DEPTH: i32 = 8;
const DEFAULT_ROLLOUTS: i32 = 0;
const MAX_AB_DEPTH: i32 = 64;
const INF: i32 = 60_000;
const MATE: i32 = 30_000;

// Quiescence
const Q_INCLUDE_CHECKS: bool = true;
const Q_FUTILITY_MARGIN: i32 = 150;

// LMR
const LMR_MIN_DEPTH: i32 = 3;
const LMR_BASE_REDUCTION: i32 = 1;

// Null-move pruning (DISABLED: crate lacks safe null move)
// Kept for config completeness, but unused to avoid bad cutoffs.
const NMP_MIN_DEPTH: i32 = 3;
const NMP_R: i32 = 2;

// Frontier futility (depth==1)
const FUTILITY_MARGIN_BASE: i32 = 200;

// Move-Count Pruning
const MCP_MIN_DEPTH: i32 = 3;
const MCP_START_AT: usize = 6;

// Aspiration windows
const ASP_WINDOW: i32 = 24;
const ASP_MAX_WIDEN: i32 = 2048;

// piece values
const P: i32 = 100;
const N: i32 = 320;
const B: i32 = 330;
const R_: i32 = 500; // avoid name clash
const Q_: i32 = 900;

// ---------------------------
// PSTs (present but zeroed; structure kept for future tuning)
// ---------------------------
const PST_PAWN: [i32; 64] = [0; 64];
const PST_KNIGHT: [i32; 64] = [0; 64];
const PST_BISHOP: [i32; 64] = [0; 64];
const PST_ROOK: [i32; 64] = [0; 64];
const PST_QUEEN: [i32; 64] = [0; 64];
const PST_KING: [i32; 64] = [0; 64];

#[inline]
fn pst_for(piece: Piece, idx: usize) -> i32 {
    match piece {
        Piece::Pawn => PST_PAWN[idx],
        Piece::Knight => PST_KNIGHT[idx],
        Piece::Bishop => PST_BISHOP[idx],
        Piece::Rook => PST_ROOK[idx],
        Piece::Queen => PST_QUEEN[idx],
        Piece::King => PST_KING[idx],
    }
}

#[inline]
fn pst_index_for(color: Color, sq: Square) -> usize {
    // Mirror ranks for Black so tables are from White POV.
    let i = sq.to_index() as usize; // 0..63
    if color == Color::White { i } else { i ^ 56 }
}

// ---------------------------
// Small helpers
// ---------------------------
fn piece_val(pc: Piece) -> i32 {
    match pc {
        Piece::Pawn => P,
        Piece::Knight => N,
        Piece::Bishop => B,
        Piece::Rook => R_,
        Piece::Queen => Q_,
        Piece::King => 0,
    }
}

#[inline]
fn clamp(v: i32, lo: i32, hi: i32) -> i32 {
    if v < lo { lo } else if v > hi { v } else { v }
}

#[inline]
fn opp(c: Color) -> Color {
    if c == Color::White { Color::Black } else { Color::White }
}

// Spec requires get_hash-based repetition tracking.
#[inline]
fn board_key(b: &Board) -> u64 {
    b.get_hash()
}

// Half-move clock via FEN (field #5) for 50-move rule.
#[inline]
fn halfmove_clock_from_fen(b: &Board) -> u32 {
    let fen = b.to_string();
    let mut it = fen.split_whitespace();
    let _ = it.next(); // board
    let _ = it.next(); // stm
    let _ = it.next(); // castling
    let _ = it.next(); // ep
    it.next().and_then(|s| s.parse::<u32>().ok()).unwrap_or(0)
}

fn count_pieces(b: &Board, piece: Piece, color: Color) -> i32 {
    (b.pieces(piece) & b.color_combined(color)).popcnt() as i32
}

fn insufficient_material(b: &Board) -> bool {
    // no pawns/rooks/queens and at most one minor each
    let no_pawns = (b.pieces(Piece::Pawn)).popcnt() == 0;
    let no_rooks = (b.pieces(Piece::Rook)).popcnt() == 0;
    let no_queens = (b.pieces(Piece::Queen)).popcnt() == 0;
    if no_pawns && no_rooks && no_queens {
        let minors = |c: Color| {
            (b.pieces(Piece::Knight) & b.color_combined(c)).popcnt() as i32
                + (b.pieces(Piece::Bishop) & b.color_combined(c)).popcnt() as i32
        };
        return minors(Color::White) <= 1 && minors(Color::Black) <= 1;
    }
    false
}

// --- NEW: simple endgame detector to relax pruning when material is scarce ---
#[inline]
fn total_material_excl_kings(b: &Board) -> i32 {
    let side = [Color::White, Color::Black];
    let mut s = 0;
    for &c in &side {
        s += P * count_pieces(b, Piece::Pawn, c)
           + N * count_pieces(b, Piece::Knight, c)
           + B * count_pieces(b, Piece::Bishop, c)
           + R_ * count_pieces(b, Piece::Rook, c)
           + Q_ * count_pieces(b, Piece::Queen, c);
    }
    s
}

// Threshold chosen so Q vs K, R vs K, minor+K vs K etc. are "endgame-like".
#[inline]
fn is_endgame_like(b: &Board) -> bool {
    total_material_excl_kings(b) <= 1200
}

fn prefer_cap_check_mvv_uci(b: &Board, m: ChessMove) -> (i32, i32, i32, String) {
    // deterministic fallback ordering (capture, gives_check, MVV-LVA, UCI)
    let to = m.get_dest();
    let them = opp(b.side_to_move());
    let cap = if b.color_on(to) == Some(them) { 1 } else { 0 };
    let gives_check = (b.make_move_new(m).checkers().popcnt() > 0) as i32;
    let mvv = {
        let victim_val = b.piece_on(to).map(piece_val).unwrap_or(P);
        let attacker_val = b.piece_on(m.get_source()).map(piece_val).unwrap_or(P);
        10_000 + victim_val * 10 - attacker_val
    };
    (cap, gives_check, mvv, m.to_string())
}

// ---------------------------
// Move pack/unpack for TT
// ---------------------------
fn pack_move(m: ChessMove) -> u16 {
    let from = m.get_source().to_index() as u16; // 0..63
    let to = m.get_dest().to_index() as u16;     // 0..63
    let promo = match m.get_promotion() {
        Some(Piece::Knight) => 1,
        Some(Piece::Bishop) => 2,
        Some(Piece::Rook)   => 3,
        Some(Piece::Queen)  => 4,
        _ => 0,
    } as u16;
    (from & 63) | ((to & 63) << 6) | ((promo & 7) << 12)
}

fn unpack_move(code: u16) -> Option<ChessMove> {
    if code == 0 { return None; }
    let from_idx = (code & 63) as u8;
    let to_idx = ((code >> 6) & 63) as u8;
    debug_assert!(from_idx < 64 && to_idx < 64);
    // SAFETY: indices masked to 0..=63; Square::new is unsafe because it assumes valid input.
    let from = unsafe { Square::new(from_idx) };
    let to   = unsafe { Square::new(to_idx) };
    let promo = match (code >> 12) & 7 {
        1 => Some(Piece::Knight),
        2 => Some(Piece::Bishop),
        3 => Some(Piece::Rook),
        4 => Some(Piece::Queen),
        _ => None,
    };
    Some(ChessMove::new(from, to, promo))
}

// ---------------------------
// Transposition table
// ---------------------------
const EXACT: i8 = 0;
const ALPHA: i8 = -1;
const BETA: i8  = 1;

#[derive(Clone, Copy)]
struct TTEntry {
    key: u64,
    depth: i16, // remaining depth at store time
    score: i32, // normalized for mates
    flag: i8,
    age: u8,
    best: u16,  // packed move
}
impl Default for TTEntry {
    fn default() -> Self {
        Self { key: 0, depth: -32768, score: 0, flag: EXACT, age: 0, best: 0 }
    }
}

const TT_ASSOC: usize = 4;

struct TT {
    buckets: Vec<[TTEntry; TT_ASSOC]>,
    mask: usize,
    age: u8,
}
impl TT {
    fn new_from_mb(tt_mb: usize) -> Self {
        use std::mem::size_of;
        let entry_sz = size_of::<TTEntry>().max(1);
        let bytes = tt_mb.saturating_mul(1024 * 1024);
        let total_entries = (bytes / entry_sz).max(TT_ASSOC);
        let mut buckets = (total_entries / TT_ASSOC).max(1);

        // round buckets down to power of two
        let mut pow2 = 1usize;
        while (pow2 << 1) <= buckets { pow2 <<= 1; }
        buckets = pow2;

        let mask = buckets - 1;
        let mut vec = Vec::with_capacity(buckets);
        vec.resize(buckets, [TTEntry::default(); TT_ASSOC]);
        Self { buckets: vec, mask, age: 0 }
    }
    #[inline] fn idx(&self, key: u64) -> usize { (key as usize) & self.mask }

    fn probe(&self, key: u64) -> Option<TTEntry> {
        let bucket = &self.buckets[self.idx(key)];
        let mut best: Option<TTEntry> = None;
        for &e in bucket.iter() {
            if e.key == key && e.depth > -32768 {
                if best.map_or(true, |b| e.depth > b.depth) {
                    best = Some(e);
                }
            }
        }
        best
    }

    fn store(&mut self, key: u64, depth: i32, score: i32, flag: i8, best: Option<ChessMove>) {
        let i = self.idx(key);
        let bucket = &mut self.buckets[i];

        // If key exists, replace that slot.
        for e in bucket.iter_mut() {
            if e.key == key {
                *e = TTEntry {
                    key, depth: depth as i16, score, flag, age: self.age,
                    best: best.map(pack_move).unwrap_or(0),
                };
                return;
            }
        }

        // Otherwise replace the "worst" slot: prefer deeper, then newer.
        let mut replace_at = 0usize;
        for (j, e) in bucket.iter().enumerate() {
            let r = &bucket[replace_at];
            let worse_depth = e.depth < r.depth;
            let same_depth_older = e.depth == r.depth && e.age.wrapping_sub(r.age) > 0;
            if worse_depth || same_depth_older { replace_at = j; }
        }
        bucket[replace_at] = TTEntry {
            key,
            depth: depth as i16,
            score,
            flag,
            age: self.age,
            best: best.map(pack_move).unwrap_or(0),
        };
    }
}

// mate score normalize/de-normalize (distance to mate invariant)
#[inline]
fn to_tt(score: i32, ply: i32) -> i32 {
    if score >= MATE - MAX_AB_DEPTH { score + ply }
    else if score <= -MATE + MAX_AB_DEPTH { score - ply }
    else { score }
}
#[inline]
fn from_tt(score: i32, ply: i32) -> i32 {
    if score >= MATE - MAX_AB_DEPTH { score - ply }
    else if score <= -MATE + MAX_AB_DEPTH { score + ply }
    else { score }
}

// ---------------------------
// Search
// ---------------------------
struct Search {
    nodes: u64,
    stop: Arc<AtomicBool>,
    tt: TT,
    killers: HashMap<i32, (Option<ChessMove>, Option<ChessMove>)>,
    history: HashMap<(Color, Square), i32>,
}

impl Search {
    fn new(stop: Arc<AtomicBool>) -> Self {
        let tt_mb = env::var("TT_MB")
            .ok()
            .and_then(|s| s.parse::<usize>().ok())
            .unwrap_or(128);
        Self {
            nodes: 0,
            stop,
            tt: TT::new_from_mb(tt_mb),
            killers: HashMap::new(),
            history: HashMap::new(),
        }
    }

    fn on_new_iter(&mut self) {
        self.nodes = 0;
        self.tt.age = self.tt.age.wrapping_add(1);
    }

    fn evaluate(&self, b: &Board) -> i32 {
        match b.status() {
            BoardStatus::Checkmate => return -MATE,
            BoardStatus::Stalemate => return 0,
            BoardStatus::Ongoing => {}
        }
        if insufficient_material(b) { return 0; }

        // 50-move rule draw
        if halfmove_clock_from_fen(b) as i32 >= 100 { return 0; }

        let mut score: i32 = 0;

        // Material
        for color in [Color::White, Color::Black] {
            let sign = if color == Color::White { 1 } else { -1 };
            score += sign
                * (P * count_pieces(b, Piece::Pawn, color)
                    + N * count_pieces(b, Piece::Knight, color)
                    + B * count_pieces(b, Piece::Bishop, color)
                    + R_ * count_pieces(b, Piece::Rook, color)
                    + Q_ * count_pieces(b, Piece::Queen, color));
        }

        // PST scaffold present (zero)
        let _ = (PST_PAWN[0], pst_for(Piece::Pawn, 0), pst_index_for(b.side_to_move(), Square::A1)); // keep in use

        // Mobility bonus
        let mobility = MoveGen::new_legal(b).len() as i32;
        score += mobility / 4;

        let pov = if b.side_to_move() == Color::White { score } else { -score };
        clamp(pov, -INF + 1, INF - 1)
    }

    #[inline]
    fn is_capture(&self, b: &Board, mv: ChessMove) -> bool {
        let to = mv.get_dest();
        let us = b.side_to_move();
        let them = opp(us);
        if b.color_on(to) == Some(them) { return true; }
        // en passant
        if let Some(ep_sq) = b.en_passant() {
            if to == ep_sq {
                if let Some(piece) = b.piece_on(mv.get_source()) {
                    if piece == Piece::Pawn && mv.get_source().get_file() != to.get_file() && b.piece_on(to).is_none() {
                        return true;
                    }
                }
            }
        }
        false
    }

    #[inline]
    fn mvv_lva(&self, b: &Board, mv: ChessMove) -> i32 {
        if !self.is_capture(b, mv) { return 0; }
        let victim_val = b.piece_on(mv.get_dest()).map(piece_val).unwrap_or(P);
        let attacker_val = b.piece_on(mv.get_source()).map(piece_val).unwrap_or(P);
        10_000 + victim_val * 10 - attacker_val
    }

    fn likely_zugzwang(&self, b: &Board) -> bool {
        let np = |c: Color| {
            320 * count_pieces(b, Piece::Knight, c)
            + 330 * count_pieces(b, Piece::Bishop, c)
            + 500 * count_pieces(b, Piece::Rook,   c)
            + 900 * count_pieces(b, Piece::Queen,  c)
        };
        np(Color::White) + np(Color::Black) <= 1000
    }

    fn ordered_moves(
        &self,
        b: &Board,
        tt_move: Option<ChessMove>,
        killers: (Option<ChessMove>, Option<ChessMove>),
    ) -> Vec<ChessMove> {
        let mut moves: Vec<ChessMove> = MoveGen::new_legal(b).collect();
        let hist = &self.history;
        let us = b.side_to_move();
        moves.sort_by_key(|&m| {
            let mut k = 0i64;
            if let Some(tm) = tt_move { if m == tm { k += 10_000_000; } }
            k += self.mvv_lva(b, m) as i64;
            if let Some(k1) = killers.0 { if m == k1 { k += 5_000_000; } }
            if let Some(k2) = killers.1 { if m == k2 { k += 5_000_000; } }
            // SMALL bump for 'gives check' to avoid biasing toward perpetual checks
            if b.make_move_new(m).checkers().popcnt() > 0 { k += 1_000; }
            k += *hist.get(&(us, m.get_dest())).unwrap_or(&0) as i64;
            if self.is_capture(b, m) { k += 1; } // tiny stabilization
            Reverse(k)
        });
        moves
    }

    fn qsearch(&mut self, b: &Board, mut alpha: i32, beta: i32) -> i32 {
        if self.stop.load(Ordering::Relaxed) { return alpha; }
        self.nodes = self.nodes.wrapping_add(1);

        match b.status() {
            BoardStatus::Checkmate => return -MATE,
            BoardStatus::Stalemate => return 0,
            BoardStatus::Ongoing => {}
        }
        if insufficient_material(b) { return 0; }
        if halfmove_clock_from_fen(b) as i32 >= 100 { return 0; }

        let stand = self.evaluate(b);
        if stand >= beta { return beta; }
        if stand > alpha { alpha = stand; }

        if stand + Q_FUTILITY_MARGIN < alpha { return alpha; }

        let mut noisy = Vec::new();
        for m in MoveGen::new_legal(b) {
            let cap = self.is_capture(b, m);
            let gives_check = if Q_INCLUDE_CHECKS { b.make_move_new(m).checkers().popcnt() > 0 } else { false };
            if cap || gives_check { noisy.push(m); }
        }
        noisy.sort_by_key(|&m| Reverse(self.mvv_lva(b, m)));

        for m in noisy {
            if self.stop.load(Ordering::Relaxed) { break; }
            let nb = b.make_move_new(m);
            let score = -self.qsearch(&nb, -beta, -alpha);
            if score >= beta { return beta; }
            if score > alpha { alpha = score; }
        }
        alpha
    }

    fn negamax(
        &mut self,
        b: &Board,
        depth: i32,
        mut alpha: i32,
        beta: i32,
        ply: i32,
        is_pv: bool,
        rep_stack: &mut Vec<u64>,
    ) -> i32 {
        if self.stop.load(Ordering::Relaxed) { return alpha; }
        self.nodes = self.nodes.wrapping_add(1);

        match b.status() {
            BoardStatus::Checkmate => return -MATE,
            BoardStatus::Stalemate => return 0,
            BoardStatus::Ongoing => {}
        }
        if insufficient_material(b) { return 0; }
        if halfmove_clock_from_fen(b) as i32 >= 100 { return 0; }

        let k = board_key(b);

        // Threefold: if current key already appears twice, this makes 3 -> draw
        let reps = rep_stack.iter().filter(|&&x| x == k).count();
        if reps >= 2 { return 0; }
        rep_stack.push(k);

        // probe TT
        if let Some(tte) = self.tt.probe(k) {
            if tte.depth as i32 >= depth {
                let tt_score = from_tt(tte.score, ply);
                if tte.flag == EXACT { rep_stack.pop(); return tt_score; }
                if tte.flag == ALPHA && tt_score <= alpha { rep_stack.pop(); return tt_score; }
                if tte.flag == BETA  && tt_score >= beta  { rep_stack.pop(); return tt_score; }
            }
        }

        let in_check = b.checkers().popcnt() > 0;
        let mut local_depth = if in_check { depth + 1 } else { depth };
        if local_depth <= 0 {
            let rv = self.qsearch(b, alpha, beta);
            rep_stack.pop();
            return rv;
        }

        // Null-move pruning intentionally disabled (see header).

        let orig_alpha = alpha;
        let mut best_move: Option<ChessMove> = None;
        let mut best_score = -INF;

        let killers = *self.killers.get(&ply).unwrap_or(&(None, None));
        let tt_move = self.tt.probe(k).and_then(|e| unpack_move(e.best));
        let moves = self.ordered_moves(b, tt_move, killers);

        // Endgame-aware pruning guard
        let endgame_like = is_endgame_like(b);

        let mut static_eval: Option<i32> = None;
        if local_depth == 1 { static_eval = Some(self.evaluate(b)); }

        let mut move_index = 0usize;
        for m in moves {
            if self.stop.load(Ordering::Relaxed) { break; }

            let is_cap = self.is_capture(b, m);
            let nb = b.make_move_new(m);
            let gives_chk = nb.checkers().popcnt() > 0;

            // Frontier futility on quiets at depth == 1 (disabled in endgames)
            if !endgame_like && local_depth == 1 && !is_cap && !gives_chk {
                let see = static_eval.unwrap_or_else(|| self.evaluate(b));
                if see + FUTILITY_MARGIN_BASE <= alpha {
                    move_index += 1;
                    continue;
                }
            }

            // Move-count pruning: skip very late quiets (disabled in endgames)
            if !endgame_like && local_depth >= MCP_MIN_DEPTH && move_index >= MCP_START_AT && !is_cap && !gives_chk {
                move_index += 1;
                continue;
            }

            let child_in_check = gives_chk;
            let mut score;

            // PVS + LMR (LMR disabled for quiets in endgames to see king approach)
            let do_lmr = local_depth >= LMR_MIN_DEPTH
                && !is_pv && !is_cap && !gives_chk && !child_in_check
                && !endgame_like;

            if do_lmr {
                let reduce = LMR_BASE_REDUCTION + if move_index >= 4 { 1 } else { 0 };
                let new_depth = (local_depth - 1 - reduce).max(1);
                score = -self.negamax(&nb, new_depth, -alpha - 1, -alpha, ply + 1, false, rep_stack);
                if score > alpha {
                    score = -self.negamax(&nb, local_depth - 1, -beta, -alpha, ply + 1, false, rep_stack);
                }
            } else {
                if move_index == 0 {
                    score = -self.negamax(&nb, local_depth - 1, -beta, -alpha, ply + 1, is_pv, rep_stack);
                } else {
                    score = -self.negamax(&nb, local_depth - 1, -alpha - 1, -alpha, ply + 1, false, rep_stack);
                    if score > alpha && score < beta {
                        score = -self.negamax(&nb, local_depth - 1, -beta, -alpha, ply + 1, true, rep_stack);
                    }
                }
            }

            if score > best_score {
                best_score = score;
                best_move = Some(m);
            }

            if score > alpha {
                alpha = score;
                if alpha >= beta {
                    if !is_cap {
                        // update killers + history
                        let entry = self.killers.entry(ply).or_insert((None, None));
                        let (k0, _k1) = *entry;
                        self.killers.insert(ply, (Some(m), k0));
                        let keyh = (b.side_to_move(), m.get_dest());
                        let e = self.history.get(&keyh).copied().unwrap_or(0) + local_depth * local_depth;
                        self.history.insert(keyh, e);
                    }
                    break;
                }
            }

            move_index += 1;
        }

        // No legal moves
        if best_move.is_none() && MoveGen::new_legal(b).next().is_none() {
            rep_stack.pop();
            return if in_check { -MATE } else { 0 };
        }

        let flag =
            if best_score <= orig_alpha { ALPHA }
            else if best_score >= beta { BETA }
            else { EXACT };

        // Store with actual searched depth (after extensions/reductions)
        self.tt.store(k, local_depth, to_tt(best_score, ply), flag, best_move);

        rep_stack.pop();
        best_score
    }
}

// ---------------------------
// PV from TT (more reliable than root map)
// ---------------------------
fn pv_line_from_tt(mut b: Board, tt: &TT, max_len: usize) -> Vec<ChessMove> {
    let mut pv = Vec::with_capacity(max_len);
    for _ in 0..max_len {
        let k = board_key(&b);
        if let Some(tte) = tt.probe(k) {
            if let Some(m) = unpack_move(tte.best) {
                if !MoveGen::new_legal(&b).any(|lm| lm == m) { break; }
                pv.push(m);
                b = b.make_move_new(m);
                continue;
            }
        }
        break;
    }
    pv
}

// parse UCI move, supports promotions (e7e8q)
fn parse_uci_move(s: &str) -> Option<ChessMove> {
    if s.len() < 4 { return None; }
    let from = Square::from_str(&s[0..2]).ok()?;
    let to = Square::from_str(&s[2..4]).ok()?;
    let promo = if s.len() == 5 {
        match &s[4..5] {
            "q" => Some(Piece::Queen),
            "r" => Some(Piece::Rook),
            "b" => Some(Piece::Bishop),
            "n" => Some(Piece::Knight),
            _ => None,
        }
    } else { None };
    Some(ChessMove::new(from, to, promo))
}

fn current_best_or_default(b: &Board) -> String {
    let mut legal: Vec<ChessMove> = MoveGen::new_legal(b).collect();
    if legal.is_empty() { return "0000".to_string(); }
    legal.sort_by_key(|&m| prefer_cap_check_mvv_uci(b, m));
    legal.reverse(); // descending by (cap, check, mvv, uci)
    legal[0].to_string()
}

fn root_search(
    search: &mut Search,
    b: &Board,
    depth: i32,
    alpha: i32,
    beta: i32,
) -> (Option<ChessMove>, i32) {
    let mut a = alpha;
    let mut best_score = -INF;
    let mut best_move: Option<ChessMove> = None;

    let killers = *search.killers.get(&0).unwrap_or(&(None, None));
    let tt_move = search.tt.probe(board_key(b)).and_then(|e| unpack_move(e.best));
    let mut moves = search.ordered_moves(b, tt_move, killers);

    for (i, m) in moves.drain(..).enumerate() {
        if search.stop.load(Ordering::Relaxed) { break; }
        let nb = b.make_move_new(m);

        let mut rep_stack = vec![board_key(b)];
        let mut score;
        if i == 0 {
            score = -search.negamax(&nb, depth - 1, -beta, -a, 1, true, &mut rep_stack);
        } else {
            score = -search.negamax(&nb, depth - 1, -a - 1, -a, 1, false, &mut rep_stack);
            if score > a && score < beta {
                score = -search.negamax(&nb, depth - 1, -beta, -a, 1, true, &mut rep_stack);
            }
        }

        if score > best_score {
            best_score = score;
            best_move = Some(m);
        }
        if score > a {
            a = score;
            if a >= beta { break; }
        }
    }

    (best_move, best_score)
}

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let mut stdout = io::stdout();

    // initial UCI banner (exact lines)
    println!("id name PyRefEngine (AB-only)");
    println!("id author open-source");
    println!("uciok");
    stdout.flush()?;

    let (tx_cmd, rx_cmd) = std::sync::mpsc::channel::<String>();
    thread::spawn(move || {
        for line in stdin.lock().lines() {
            if let Ok(s) = line {
                let cmd = s.trim().to_string();
                if tx_cmd.send(cmd).is_err() { break; }
            } else { break; }
        }
    });

    let mut board = Board::default();
    let mut search_handle: Option<thread::JoinHandle<()>> = None;
    let stop_flag = Arc::new(AtomicBool::new(false));
    let bestmove_sent = Arc::new(AtomicBool::new(false)); // prevent double-printing on stop

    while let Ok(cmd) = rx_cmd.recv() {
        println!("info string dbg=recv '{}'", cmd);
        stdout.flush()?;

        if cmd == "uci" {
            println!("id name PyRefEngine (AB-only)");
            println!("id author open-source");
            println!("uciok");
            stdout.flush()?;
            continue;
        }
        if cmd == "isready" {
            println!("readyok");
            stdout.flush()?;
            continue;
        }
        if cmd.starts_with("ucinewgame") {
            // stop any running search and reset position
            if let Some(h) = search_handle.take() {
                stop_flag.store(true, Ordering::Relaxed);
                let _ = h.join();
                stop_flag.store(false, Ordering::Relaxed);
                bestmove_sent.store(false, Ordering::Relaxed);
            }
            board = Board::default();
            stdout.flush()?;
            continue;
        }
        if cmd.starts_with("position ") {
            if let Some(after) = cmd.strip_prefix("position ") {
                let parts: Vec<&str> = after.split_whitespace().collect();
                let mut idx = 0;
                if parts.get(0) == Some(&"startpos") {
                    board = Board::default();
                    idx = 1;
                } else if parts.get(0) == Some(&"fen") {
                    if parts.len() >= 7 {
                        let fen = parts[1..7].join(" ");
                        match Board::from_str(&fen) {
                            Ok(b) => board = b,
                            Err(e) => {
                                println!("info string dbg=position-parse-error {}:{}", "FEN", e);
                                board = Board::default();
                                stdout.flush()?;
                                continue;
                            }
                        }
                        idx = 7;
                    } else {
                        println!("info string dbg=position-parse-error {}:{}", "FEN", "expected 6 tokens");
                        board = Board::default();
                        stdout.flush()?;
                        continue;
                    }
                } else {
                    println!("info string dbg=position-parse-error {}:{}", "SYNTAX", "expected startpos or fen");
                    board = Board::default();
                    stdout.flush()?;
                    continue;
                }

                if idx < parts.len() && parts[idx] == "moves" {
                    for mv_str in &parts[idx + 1..] {
                        if let Some(mv) = parse_uci_move(mv_str) {
                            if MoveGen::new_legal(&board).any(|m| m == mv) {
                                board = board.make_move_new(mv);
                            } else {
                                println!("info string dbg=bad-move {}", mv_str);
                            }
                        } else {
                            println!("info string dbg=bad-move {}", mv_str);
                        }
                    }
                }
            }
            stdout.flush()?;
            continue;
        }

        if cmd.starts_with("go ") {
            let mut depth: i32 = DEFAULT_DEPTH;
            let mut rollouts: i32 = DEFAULT_ROLLOUTS;
            let mut movetime_ms: Option<u64> = None;

            let parts: Vec<&str> = cmd.split_whitespace().collect();
            let mut i = 1;
            while i + 1 < parts.len() {
                match parts[i] {
                    "depth" => { if let Ok(d) = parts[i + 1].parse::<i32>() { depth = d; } i += 2; }
                    "rollouts" => { if let Ok(r) = parts[i + 1].parse::<i32>() { rollouts = r; } i += 2; }
                    "movetime" => { if let Ok(ms) = parts[i + 1].parse::<u64>() { movetime_ms = Some(ms); } i += 2; }
                    _ => i += 1,
                }
            }

            println!("info string dbg=go depth={} rollouts={} (rollouts ignored; AB-only)", depth, rollouts);
            stdout.flush()?;

            // stop previous search cleanly
            if let Some(h) = search_handle.take() {
                stop_flag.store(true, Ordering::Relaxed);
                let _ = h.join();
                stop_flag.store(false, Ordering::Relaxed);
                bestmove_sent.store(false, Ordering::Relaxed);
            }

            let b0 = board;
            let stop = Arc::clone(&stop_flag);
            let sent = Arc::clone(&bestmove_sent);

            search_handle = Some(thread::spawn(move || {
                let start = Instant::now();
                let time_limit = movetime_ms.map(Duration::from_millis);

                let mut search = Search::new(Arc::clone(&stop));

                let mut last_score = search.evaluate(&b0);
                let mut root_best: Option<ChessMove> = None;

                let max_depth = depth.max(1).min(MAX_AB_DEPTH);
                for d in 1..=max_depth {
                    if let Some(tl) = time_limit { if start.elapsed() >= tl { break; } }
                    if stop.load(Ordering::Relaxed) { break; }

                    search.on_new_iter();

                    let mut window = ASP_WINDOW;
                    let mut alpha = last_score - window;
                    let mut beta  = last_score + window;

                    let mut score;
                    loop {
                        let (best_move, sc) = root_search(&mut search, &b0, d, alpha, beta);
                        score = sc;
                        if (score <= alpha || score >= beta) && window < ASP_MAX_WIDEN {
                            window = (window * 2).min(ASP_MAX_WIDEN);
                            alpha = score - window;
                            beta  = score + window;
                            continue;
                        } else {
                            if let Some(m) = best_move { root_best = Some(m); }
                            break;
                        }
                    }

                    last_score = clamp(score, -INF + 1, INF - 1);

                    let elapsed = start.elapsed().as_secs_f64().max(1e-6);
                    let nps = (search.nodes as f64 / elapsed) as u64;
                    let pv = pv_line_from_tt(b0, &search.tt, d as usize);
                    let pv_str = pv.iter().map(|m| m.to_string()).collect::<Vec<_>>().join(" ");
                    println!("info depth {} nodes {} nps {} score cp {} pv {}", d, search.nodes, nps, last_score, pv_str);
                    println!("info string dbg=iter depth={}", d);
                    io::stdout().flush().ok();

                    if stop.load(Ordering::Relaxed) { break; }
                    if let Some(tl) = time_limit { if start.elapsed() >= tl { break; } }
                }

                if stop.load(Ordering::Relaxed) { return; }

                if !sent.swap(true, Ordering::Relaxed) {
                    let best_uci = if let Some(m) = root_best { m.to_string() } else { current_best_or_default(&b0) };
                    println!("bestmove {}", best_uci);
                    io::stdout().flush().ok();
                }
            }));

            continue;
        }

        if cmd == "stop" {
            stop_flag.store(true, Ordering::Relaxed);
            if !bestmove_sent.swap(true, Ordering::Relaxed) {
                println!("bestmove {}", current_best_or_default(&board));
                stdout.flush()?;
            }
            continue;
        }

        if cmd == "quit" {
            println!("info string dbg=quit");
            stdout.flush()?;
            if let Some(h) = search_handle.take() {
                stop_flag.store(true, Ordering::Relaxed);
                let _ = h.join();
            }
            break;
        }
        // ignore other commands
    }

    Ok(())
}
