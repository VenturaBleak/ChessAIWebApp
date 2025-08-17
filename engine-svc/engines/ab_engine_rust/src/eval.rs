use chess::{Board, BoardStatus, Color, Piece, Square, BitBoard};
use crate::types::*;

pub struct ClassicalEval;

impl ClassicalEval {
    #[inline]
    pub fn eval(&self, b: &Board) -> i32 {
        match b.status() {
            BoardStatus::Checkmate => return -MATE,
            BoardStatus::Stalemate => return 0,
            BoardStatus::Ongoing => {}
        }
        if insufficient_material(b) { return 0; }
        if halfmove_clock_from_fen(b) as i32 >= 100 { return 0; }

        let mut mg = 0i32;
        let mut eg = 0i32;

        // ----- Material base (kept) -----
        for &color in &[Color::White, Color::Black] {
            let sgn = if color == b.side_to_move() { 1 } else { -1 };
            let mat = P * count_pieces(b, Piece::Pawn, color)
                    + N * count_pieces(b, Piece::Knight, color)
                    + B * count_pieces(b, Piece::Bishop, color)
                    + R_ * count_pieces(b, Piece::Rook,   color)
                    + Q_ * count_pieces(b, Piece::Queen,  color);
            mg += sgn * mat;
            eg += sgn * mat;
        }

        // ----- Opening / MG extras -----
        let opening_like = game_phase(b) >= (PHASE_MAX * 2 / 3);
        let fm = fullmove_number_from_fen(b);

        // tempo (small and only MG)
        mg += TEMPO_BONUS;

        for &color in &[Color::White, Color::Black] {
            let sgn = if color == b.side_to_move() { 1 } else { -1 };

            // bishop pair
            if count_pieces(b, Piece::Bishop, color) >= 2 {
                mg += sgn * BISHOP_PAIR_MG;
                eg += sgn * BISHOP_PAIR_EG;
            }

            // castling encouragement in opening
            if opening_like {
                if is_castled(b, color) {
                    mg += sgn * CASTLED_BONUS_EARLY;
                } else if fm >= 10 {
                    mg -= sgn * UNCASTLED_PENALTY_EARLY;
                }
            }

            // central pawn presence (d4/e4 for white, d5/e5 for black)
            let pawns = b.color_combined(color) & b.pieces(Piece::Pawn);
            for ps in pawns {
                let rrel = relative_rank(color, ps);
                let f = file_idx(ps);
                if (color == Color::White && rrel == 3 && (f == 3 || f == 4)) ||
                   (color == Color::Black && rrel == 4 && (f == 3 || f == 4)) {
                    mg += sgn * CENTER_PAWN_BONUS;
                }
            }

            // rook on (semi) open file (MG)
            let rooks = b.color_combined(color) & b.pieces(Piece::Rook);
            for rsq in rooks {
                mg += sgn * rook_file_bonus(b, color, rsq);
            }

            // light pawn-structure penalties in MG
            for ps in pawns {
                let f = file_idx(ps);
                if is_doubled_pawn_on_file(b, color, f) {
                    mg -= sgn * DOUBLED_PAWN_PENALTY_MG;
                }
                if is_isolated_pawn(b, color, f) {
                    mg -= sgn * ISOLATED_PAWN_PENALTY_MG;
                }
            }

            // development: minors still on home squares in opening
            if opening_like {
                let minors = b.color_combined(color) & (b.pieces(Piece::Knight) | b.pieces(Piece::Bishop));
                let home: BitBoard = if color == Color::White {
                    BitBoard::from_square(Square::B1)
                        | BitBoard::from_square(Square::G1)
                        | BitBoard::from_square(Square::C1)
                        | BitBoard::from_square(Square::F1)
                } else {
                    BitBoard::from_square(Square::B8)
                        | BitBoard::from_square(Square::G8)
                        | BitBoard::from_square(Square::C8)
                        | BitBoard::from_square(Square::F8)
                };
                let stuck = (minors & home).popcnt() as i32;
                mg -= sgn * MINOR_DEV_PENALTY * stuck;
            }
        }

        // ----- Endgame (kept from previous step) -----
        // King EG centralization
        for &color in &[Color::White, Color::Black] {
            let sgn = if color == b.side_to_move() { 1 } else { -1 };
            let bb = b.color_combined(color) & b.pieces(Piece::King);
            if bb.popcnt() >= 1 {
                let sq: Square = bb.to_square();
                let idx = pst_index_for(color, sq);
                eg += sgn * PST_KING_EG[idx];
            }
        }

        // Passed pawns
        for &color in &[Color::White, Color::Black] {
            let sgn = if color == b.side_to_move() { 1 } else { -1 };
            let pawns = b.color_combined(color) & b.pieces(Piece::Pawn);
            for sq in pawns {
                if is_passed_pawn(b, sq, color) {
                    let rr = relative_rank(color, sq);
                    eg += sgn * PASSED_PAWN_BONUS_BY_RANK[rr];
                }
            }
        }

        // Rook heuristics (7th rank, behind passer)
        for &color in &[Color::White, Color::Black] {
            let sgn = if color == b.side_to_move() { 1 } else { -1 };
            let them = opp(color);
            let rooks = b.color_combined(color) & b.pieces(Piece::Rook);
            let kbb = b.color_combined(them) & b.pieces(Piece::King);
            let opp_king_backrank = {
                let idx = kbb.to_square().to_index() / 8;
                if color == Color::White { idx == 7 } else { idx == 0 }
            };
            let opp_has_pawns = (b.color_combined(them) & b.pieces(Piece::Pawn)).popcnt() > 0;

            for sq in rooks {
                if relative_rank(color, sq) == 6 && (opp_has_pawns || opp_king_backrank) {
                    eg += sgn * 18;
                }
                let f = file_idx(sq);
                let pawns = b.color_combined(color) & b.pieces(Piece::Pawn);
                for ps in pawns {
                    if file_idx(ps) == f && is_passed_pawn(b, ps, color) {
                        if relative_rank(color, sq) < relative_rank(color, ps) {
                            eg += sgn * 20;
                        }
                    }
                }
            }
        }

        // Drawish OCB scaling (kept)
        let only_minors_and_pawns =
            total_material_excl_kings(b) <= (B * 2 + P * 16) &&
            count_pieces(b, Piece::Queen, Color::White) == 0 &&
            count_pieces(b, Piece::Queen, Color::Black) == 0 &&
            count_pieces(b, Piece::Rook,  Color::White) == 0 &&
            count_pieces(b, Piece::Rook,  Color::Black) == 0;

        if only_minors_and_pawns
            && count_pieces(b, Piece::Bishop, Color::White) == 1
            && count_pieces(b, Piece::Bishop, Color::Black) == 1
        {
            let wb = (b.color_combined(Color::White) & b.pieces(Piece::Bishop)).to_square();
            let bb = (b.color_combined(Color::Black) & b.pieces(Piece::Bishop)).to_square();
            let is_light = |sq: Square| -> bool { let i = sq.to_index(); ((i % 8) + (i / 8)) % 2 == 0 };
            if is_light(wb) != is_light(bb) {
                mg = mg * 3 / 4;
                eg = eg * 3 / 4;
            }
        }

        // ----- Tapered mix -----
        let phase = game_phase(b);
        let mg_w = phase;
        let eg_w = PHASE_MAX - phase;
        let mixed = (mg * mg_w + eg * eg_w) / PHASE_MAX.max(1);
        clamp(mixed, -INF + 1, INF - 1)
    }
}
