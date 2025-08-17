use chess::{Board, ChessMove, MoveGen, Color};
use std::cmp::Reverse;
use std::collections::HashMap;

use crate::types::*;
use crate::types::{piece_code, piece_val};

pub type Killers = HashMap<i32, (Option<ChessMove>, Option<ChessMove>)>;
pub type History = HashMap<(Color, Square, Square, u8), i32>;
use chess::{Square};

pub struct Ordering<'a> {
    pub history: &'a History,
}

impl<'a> Ordering<'a> {
    #[inline]
    pub fn is_capture(&self, b: &Board, mv: ChessMove) -> bool {
        let to = mv.get_dest();
        let us = b.side_to_move();
        let them = opp(us);
        if b.color_on(to) == Some(them) { return true; }
        if let Some(ep_sq) = b.en_passant() {
            if to == ep_sq {
                if let Some(piece) = b.piece_on(mv.get_source()) {
                    if piece == chess::Piece::Pawn && mv.get_source().get_file() != to.get_file() && b.piece_on(to).is_none() {
                        return true;
                    }
                }
            }
        }
        false
    }

    #[inline]
    pub fn mvv_lva(&self, b: &Board, mv: ChessMove) -> i32 {
        if !self.is_capture(b, mv) { return 0; }
        let victim_val = b.piece_on(mv.get_dest()).map(piece_val).unwrap_or(P);
        let attacker_val = b.piece_on(mv.get_source()).map(piece_val).unwrap_or(P);
        10_000 + victim_val * 10 - attacker_val
    }

    pub fn ordered_moves(
        &self,
        b: &Board,
        tt_move: Option<ChessMove>,
        killers: (Option<ChessMove>, Option<ChessMove>),
    ) -> Vec<ChessMove> {
        let mut moves: Vec<ChessMove> = MoveGen::new_legal(b).collect();
        let us = b.side_to_move();
        moves.sort_by_key(|&m| {
            let mut k = 0i64;
            if let Some(tm) = tt_move { if m == tm { k += 10_000_000; } }
            k += self.mvv_lva(b, m) as i64;
            if let Some(k1) = killers.0 { if m == k1 { k += 5_000_000; } }
            if let Some(k2) = killers.1 { if m == k2 { k += 5_000_000; } }
            if b.make_move_new(m).checkers().popcnt() > 0 { k += 1_000; }
            if let Some(pc) = b.piece_on(m.get_source()) {
                k += *self.history.get(&(us, m.get_source(), m.get_dest(), piece_code(pc))).unwrap_or(&0) as i64;
            }
            if self.is_capture(b, m) { k += 1; }
            Reverse(k)
        });
        moves
    }
}
