use std::collections::HashMap;
use std::env;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use chess::{Board, BoardStatus, ChessMove, MoveGen};

use crate::types::*;
use crate::eval::ClassicalEval;
use crate::ordering::{Ordering as MoveOrdering, Killers, History};
use crate::tt::{TT, to_tt, from_tt};
use crate::types::board_key;

// ---------- Local, self-free helpers to avoid overlapping borrows ----------
#[inline]
fn is_capture_quick(b: &Board, mv: ChessMove) -> bool {
    let to = mv.get_dest();
    let us = b.side_to_move();
    let them = opp(us);
    if b.color_on(to) == Some(them) { return true; }
    // en passant
    if let Some(ep_sq) = b.en_passant() {
        if to == ep_sq {
            if let Some(piece) = b.piece_on(mv.get_source()) {
                if piece == chess::Piece::Pawn
                    && mv.get_source().get_file() != to.get_file()
                    && b.piece_on(to).is_none()
                {
                    return true;
                }
            }
        }
    }
    false
}

#[inline]
fn mvv_lva_quick(b: &Board, mv: ChessMove) -> i32 {
    if !is_capture_quick(b, mv) { return 0; }
    let victim_val = b.piece_on(mv.get_dest()).map(piece_val).unwrap_or(P);
    let attacker_val = b.piece_on(mv.get_source()).map(piece_val).unwrap_or(P);
    10_000 + victim_val * 10 - attacker_val
}
// ---------------------------------------------------------------------------

pub struct Search {
    pub nodes: u64,
    pub stop: Arc<AtomicBool>,
    pub tt: TT,
    pub killers: Killers,
    pub history: History,
    eval: ClassicalEval,
}

impl Search {
    pub fn new(stop: Arc<AtomicBool>) -> Self {
        let tt_mb = env::var("TT_MB").ok().and_then(|s| s.parse::<usize>().ok()).unwrap_or(128);
        Self {
            nodes: 0,
            stop,
            tt: TT::new_from_mb(tt_mb),
            killers: HashMap::new(),
            history: HashMap::new(),
            eval: ClassicalEval,
        }
    }

    #[inline] pub fn on_new_iter(&mut self) {
        self.nodes = 0;
        self.tt.age = self.tt.age.wrapping_add(1);
    }

    #[inline] pub fn evaluate(&self, b: &Board) -> i32 { self.eval.eval(b) }

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

        // Build noisy list without borrowing self
        let mut noisy = Vec::new();
        for m in MoveGen::new_legal(b) {
            let cap = is_capture_quick(b, m);
            let promo = m.get_promotion().is_some();
            let gives_check = if Q_INCLUDE_CHECKS {
                b.make_move_new(m).checkers().popcnt() > 0
            } else {
                false
            };
            if cap || gives_check || promo { noisy.push(m); }
        }
        noisy.sort_by_key(|&m| std::cmp::Reverse(mvv_lva_quick(b, m)));

        for m in noisy {
            if self.stop.load(Ordering::Relaxed) { break; }
            let nb = b.make_move_new(m);
            let score = -self.qsearch(&nb, -beta, -alpha);
            if score >= beta { return beta; }
            if score > alpha { alpha = score; }
        }
        alpha
    }

    pub fn negamax(
        &mut self,
        b: &Board,
        depth: i32,
        mut alpha: i32,
        beta: i32,
        ply: i32,
        is_pv: bool,
        parent_eval: Option<i32>,
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
        if rep_stack.iter().filter(|&&x| x == k).count() >= 2 { return 0; }
        rep_stack.push(k);

        // TT probe
        if let Some(tte) = self.tt.probe(k) {
            if tte.depth as i32 >= depth {
                let tt_score = from_tt(tte.score, ply);
                if tte.flag == EXACT { rep_stack.pop(); return tt_score; }
                if tte.flag == ALPHA && tt_score <= alpha { rep_stack.pop(); return tt_score; }
                if tte.flag == BETA  && tt_score >= beta  { rep_stack.pop(); return tt_score; }
            }
        }

        let in_check = b.checkers().popcnt() > 0;
        let local_depth = if in_check { depth + 1 } else { depth };
        if local_depth <= 0 {
            let rv = self.qsearch(b, alpha, beta);
            rep_stack.pop();
            return rv;
        }

        // Node eval, improving heuristic
        let node_eval = self.evaluate(b);
        let improving = parent_eval.map(|pe| node_eval >= pe - 40).unwrap_or(false);

        let orig_alpha = alpha;
        let mut best_move: Option<ChessMove> = None;
        let mut best_score = -INF;

        let killers = *self.killers.get(&ply).unwrap_or(&(None, None));
        let tt_move = self.tt.probe(k).and_then(|e| crate::types::unpack_move(e.best));

        // Order moves in a short scope so &self.history doesn't overlap with &mut self below
        let moves = {
            let ord = MoveOrdering { history: &self.history };
            ord.ordered_moves(b, tt_move, killers)
        };

        // Refined endgame check: hard material threshold OR low phase
        let endgame_like = is_endgame_like(b) || game_phase(b) <= (PHASE_MAX / 3);

        let mut move_index = 0usize;
        for m in moves {
            if self.stop.load(Ordering::Relaxed) { break; }

            let is_cap = is_capture_quick(b, m);
            let nb = b.make_move_new(m);
            let gives_chk = nb.checkers().popcnt() > 0;

            // Frontier futility (disabled in endgames/PV/improving)
            if !endgame_like && !is_pv && !improving && local_depth == 1 && !is_cap && !gives_chk {
                if node_eval + (FUTILITY_MARGIN_BASE / 2) <= alpha {
                    move_index += 1;
                    continue;
                }
            }

            // Move-count pruning (disabled in endgames/PV/improving/near-root)
            if !endgame_like && !is_pv && !improving && ply > 2 && local_depth >= MCP_MIN_DEPTH && !is_cap && !gives_chk {
                let mut dyn_start = MCP_START_AT + (local_depth as usize);
                if beta - alpha <= 2 * ASP_WINDOW { dyn_start += 2; }
                if move_index >= dyn_start {
                    move_index += 1;
                    continue;
                }
            }

            let mut score;
            let child_in_check = gives_chk;

            // Safer LMR on late quiets only
            let do_lmr = local_depth >= LMR_MIN_DEPTH
                && !is_pv && !is_cap && !gives_chk && !child_in_check
                && !endgame_like && !improving && move_index >= 4;

            if do_lmr {
                let reduce = LMR_BASE_REDUCTION + if move_index >= 6 { 1 } else { 0 };
                let new_depth = (local_depth - 1 - reduce).max(1);
                score = -self.negamax(&nb, new_depth, -alpha - 1, -alpha, ply + 1, false, Some(node_eval), rep_stack);
                if score > alpha {
                    score = -self.negamax(&nb, local_depth - 1, -beta, -alpha, ply + 1, false, Some(node_eval), rep_stack);
                }
            } else {
                if move_index == 0 {
                    score = -self.negamax(&nb, local_depth - 1, -beta, -alpha, ply + 1, true, Some(node_eval), rep_stack);
                } else {
                    score = -self.negamax(&nb, local_depth - 1, -alpha - 1, -alpha, ply + 1, false, Some(node_eval), rep_stack);
                    if score > alpha && score < beta {
                        score = -self.negamax(&nb, local_depth - 1, -beta, -alpha, ply + 1, true, Some(node_eval), rep_stack);
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
                        let entry = self.killers.entry(ply).or_insert((None, None));
                        let (k0, _k1) = *entry;
                        self.killers.insert(ply, (Some(m), k0));
                        if let Some(pc) = b.piece_on(m.get_source()) {
                            let keyh = (b.side_to_move(), m.get_source(), m.get_dest(), piece_code(pc));
                            let e = self.history.get(&keyh).copied().unwrap_or(0) + local_depth * local_depth;
                            self.history.insert(keyh, e);
                        }
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

        let flag = if best_score <= orig_alpha { ALPHA }
                   else if best_score >= beta { BETA }
                   else { EXACT };
        self.tt.store(k, local_depth, to_tt(best_score, ply), flag, best_move);

        rep_stack.pop();
        best_score
    }
}

// ---------------------------
// Helpers used by main.rs
// ---------------------------
pub fn pv_line_from_tt(mut b: Board, tt: &TT, max_len: usize) -> Vec<ChessMove> {
    let mut pv = Vec::with_capacity(max_len);
    for _ in 0..max_len {
        let k = board_key(&b);
        if let Some(tte) = tt.probe(k) {
            if let Some(m) = crate::types::unpack_move(tte.best) {
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

pub fn root_search(
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
    let tt_move = search.tt.probe(board_key(b)).and_then(|e| crate::types::unpack_move(e.best));

    // Build ordered moves in a short scope so &search.history doesn't overlap with &mut search
    let mut moves = {
        let ord = MoveOrdering { history: &search.history };
        ord.ordered_moves(b, tt_move, killers)
    };

    let parent_eval = Some(search.evaluate(b));

    for (i, m) in moves.drain(..).enumerate() {
        if search.stop.load(Ordering::Relaxed) { break; }
        let nb = b.make_move_new(m);

        let mut rep_stack = vec![board_key(b)];
        let mut score;
        if i == 0 {
            score = -search.negamax(&nb, depth - 1, -beta, -a, 1, true, parent_eval, &mut rep_stack);
        } else {
            score = -search.negamax(&nb, depth - 1, -a - 1, -a, 1, false, parent_eval, &mut rep_stack);
            if score > a && score < beta {
                score = -search.negamax(&nb, depth - 1, -beta, -a, 1, true, parent_eval, &mut rep_stack);
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

// Fallback best move when stopping early
pub fn current_best_or_default(b: &Board) -> String {
    let mut legal: Vec<ChessMove> = MoveGen::new_legal(b).collect();
    if legal.is_empty() { return "0000".to_string(); }
    legal.sort_by_key(|&m| prefer_cap_check_mvv_uci(b, m));
    legal.reverse();
    legal[0].to_string()
}
