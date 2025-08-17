use chess::{BitBoard, Board, ChessMove, Color, Piece, Square};

// ---------------------------
// Tunables / constants
// ---------------------------
pub const DEFAULT_DEPTH: i32 = 8;
pub const DEFAULT_ROLLOUTS: i32 = 0;
pub const MAX_AB_DEPTH: i32 = 64;
pub const INF: i32 = 60_000;
pub const MATE: i32 = 30_000;

// Quiescence
pub const Q_INCLUDE_CHECKS: bool = true;
pub const Q_FUTILITY_MARGIN: i32 = 150;

// LMR
pub const LMR_MIN_DEPTH: i32 = 3;
pub const LMR_BASE_REDUCTION: i32 = 1;

// Frontier futility (depth==1)
pub const FUTILITY_MARGIN_BASE: i32 = 200;

// Move-Count Pruning
pub const MCP_MIN_DEPTH: i32 = 3;
pub const MCP_START_AT: usize = 6;

// Aspiration windows
pub const ASP_WINDOW: i32 = 24;
pub const ASP_MAX_WIDEN: i32 = 2048;

// Piece values
pub const P: i32 = 100;
pub const N: i32 = 320;
pub const B: i32 = 330;
pub const R_: i32 = 500;
pub const Q_: i32 = 900;

// ---- Opening / MG knobs ----
pub const TEMPO_BONUS: i32 = 10;
pub const BISHOP_PAIR_MG: i32 = 28;
pub const BISHOP_PAIR_EG: i32 = 12;
pub const CASTLED_BONUS_EARLY: i32 = 40;
pub const UNCASTLED_PENALTY_EARLY: i32 = 16;
pub const CENTER_PAWN_BONUS: i32 = 12;
pub const MINOR_DEV_PENALTY: i32 = 10;
pub const ROOK_OPEN_FILE_BONUS: i32 = 12;
pub const ROOK_SEMIOPEN_FILE_BONUS: i32 = 6;
pub const DOUBLED_PAWN_PENALTY_MG: i32 = 10;
pub const ISOLATED_PAWN_PENALTY_MG: i32 = 8;

// PSTs (kept zeroed for brevity)
pub const PST_PAWN: [i32; 64] = [0; 64];
pub const PST_KNIGHT: [i32; 64] = [0; 64];
pub const PST_BISHOP: [i32; 64] = [0; 64];
pub const PST_ROOK: [i32; 64] = [0; 64];
pub const PST_QUEEN: [i32; 64] = [0; 64];
pub const PST_KING: [i32; 64] = [0; 64];

// --- Endgame king PSQT ---
pub const PST_KING_EG: [i32; 64] = [
   -50,-40,-30,-30,-30,-30,-40,-50,
   -40,-20,  0,  0,  0,  0,-20,-40,
   -30,  0, 10, 15, 15, 10,  0,-30,
   -30,  0, 15, 20, 20, 15,  0,-30,
   -30,  0, 15, 20, 20, 15,  0,-30,
   -30,  0, 10, 15, 15, 10,  0,-30,
   -40,-20,  0,  0,  0,  0,-20,-40,
   -50,-40,-30,-30,-30,-30,-40,-50,
];

// --- Passed-pawn rank bonuses (relative ranks 0..7; index 7 unused) ---
pub const PASSED_PAWN_BONUS_BY_RANK: [i32; 8] = [0, 5, 12, 24, 40, 70, 110, 0];

// ---------------------------
// Game phase (tapered eval)
// ---------------------------
pub const PHASE_PAWN:   i32 = 0;
pub const PHASE_KNIGHT: i32 = 1;
pub const PHASE_BISHOP: i32 = 1;
pub const PHASE_ROOK:   i32 = 2;
pub const PHASE_QUEEN:  i32 = 4;
pub const PHASE_MAX: i32 = PHASE_KNIGHT*4 + PHASE_BISHOP*4 + PHASE_ROOK*4 + PHASE_QUEEN*2;

#[inline]
pub fn game_phase(b: &Board) -> i32 {
    let mut phase = 0;
    for &c in &[Color::White, Color::Black] {
        phase += PHASE_KNIGHT * count_pieces(b, Piece::Knight, c);
        phase += PHASE_BISHOP * count_pieces(b, Piece::Bishop, c);
        phase += PHASE_ROOK   * count_pieces(b, Piece::Rook,   c);
        phase += PHASE_QUEEN  * count_pieces(b, Piece::Queen,  c);
    }
    clamp(phase, 0, PHASE_MAX)
}

// ---------------------------
// PST helpers
// ---------------------------
#[inline] pub fn pst_for(piece: Piece, idx: usize) -> i32 {
    match piece {
        Piece::Pawn => PST_PAWN[idx],
        Piece::Knight => PST_KNIGHT[idx],
        Piece::Bishop => PST_BISHOP[idx],
        Piece::Rook => PST_ROOK[idx],
        Piece::Queen => PST_QUEEN[idx],
        Piece::King => PST_KING[idx],
    }
}
#[inline] pub fn pst_index_for(color: Color, sq: Square) -> usize {
    let i = sq.to_index() as usize;
    if color == Color::White { i } else { i ^ 56 }
}

// ---------------------------
// Small helpers
// ---------------------------
#[inline] pub fn piece_val(pc: Piece) -> i32 {
    match pc {
        Piece::Pawn => P, Piece::Knight => N, Piece::Bishop => B,
        Piece::Rook => R_, Piece::Queen => Q_, Piece::King => 0,
    }
}
#[inline] pub fn piece_code(pc: Piece) -> u8 {
    match pc {
        Piece::Pawn => 1, Piece::Knight => 2, Piece::Bishop => 3,
        Piece::Rook => 4, Piece::Queen => 5, Piece::King => 6,
    }
}
#[inline] pub fn clamp(v: i32, lo: i32, hi: i32) -> i32 {
    if v < lo { lo } else if v > hi { hi } else { v }
}
#[inline] pub fn opp(c: Color) -> Color { if c == Color::White { Color::Black } else { Color::White } }
#[inline] pub fn board_key(b: &Board) -> u64 { b.get_hash() }

#[inline]
pub fn halfmove_clock_from_fen(b: &Board) -> u32 {
    let fen = b.to_string();
    let mut it = fen.split_whitespace();
    let _ = it.next(); let _ = it.next(); let _ = it.next(); let _ = it.next();
    it.next().and_then(|s| s.parse::<u32>().ok()).unwrap_or(0)
}
#[inline]
pub fn fullmove_number_from_fen(b: &Board) -> u32 {
    let fen = b.to_string();
    fen.split_whitespace().nth(5).and_then(|s| s.parse::<u32>().ok()).unwrap_or(1)
}

pub fn count_pieces(b: &Board, piece: Piece, color: Color) -> i32 {
    (b.pieces(piece) & b.color_combined(color)).popcnt() as i32
}

pub fn insufficient_material(b: &Board) -> bool {
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

#[inline]
pub fn total_material_excl_kings(b: &Board) -> i32 {
    [Color::White, Color::Black].iter().map(|&c| {
        P*count_pieces(b, Piece::Pawn, c)
      + N*count_pieces(b, Piece::Knight, c)
      + B*count_pieces(b, Piece::Bishop, c)
      + R_*count_pieces(b, Piece::Rook,   c)
      + Q_*count_pieces(b, Piece::Queen,  c)
    }).sum()
}

#[inline] pub fn is_endgame_like(b: &Board) -> bool { total_material_excl_kings(b) <= 1200 }

// ---- Opening/MG helpers ----
#[inline] pub fn relative_rank(color: Color, sq: Square) -> usize {
    let r = (sq.to_index() / 8) as i32;
    let rr = if color == Color::White { r } else { 7 - r };
    rr as usize
}
#[inline] pub fn file_idx(sq: Square) -> i32 { (sq.to_index() % 8) as i32 }

#[inline] pub fn is_castled(b: &Board, c: Color) -> bool {
    let ksq = (b.color_combined(c) & b.pieces(Piece::King)).to_square();
    if c == Color::White { ksq == Square::G1 || ksq == Square::C1 }
    else { ksq == Square::G8 || ksq == Square::C8 }
}

// Rook file bonus: open = no pawns for either side on the file; semi-open = no own pawn but some enemy pawn.
#[inline]
pub fn rook_file_bonus(b: &Board, c: Color, sq: Square) -> i32 {
    let f = file_idx(sq);
    let our_pawns = b.color_combined(c) & b.pieces(Piece::Pawn);
    let their_pawns = b.color_combined(opp(c)) & b.pieces(Piece::Pawn);

    let mut own_on_file = false;
    for ps in our_pawns { if file_idx(ps) == f { own_on_file = true; break; } }
    if own_on_file {
        return 0;
    }
    let mut opp_on_file = false;
    for ps in their_pawns { if file_idx(ps) == f { opp_on_file = true; break; } }
    if opp_on_file { ROOK_SEMIOPEN_FILE_BONUS } else { ROOK_OPEN_FILE_BONUS }
}

// Very light & cheap structure tests (MG-only usage)
pub fn is_doubled_pawn_on_file(b: &Board, c: Color, file: i32) -> bool {
    let mut cnt = 0;
    let pawns = b.color_combined(c) & b.pieces(Piece::Pawn);
    for ps in pawns {
        if file_idx(ps) == file { cnt += 1; if cnt >= 2 { return true; } }
    }
    false
}
pub fn is_isolated_pawn(b: &Board, c: Color, file: i32) -> bool {
    let has_on = |ff: i32| -> bool {
        if ff < 0 || ff > 7 { return false; }
        let pawns = b.color_combined(c) & b.pieces(Piece::Pawn);
        for ps in pawns { if file_idx(ps) == ff { return true; } }
        false
    };
    !(has_on(file - 1) || has_on(file + 1))
}

// ---- Passed-pawn test (used in EG too) ----
pub fn is_passed_pawn(b: &Board, sq: Square, us: Color) -> bool {
    let them = opp(us);
    if b.piece_on(sq) != Some(Piece::Pawn) || b.color_on(sq) != Some(us) { return false; }
    let our_rank = relative_rank(us, sq) as i32;
    let f = file_idx(sq);
    for df in -1..=1 {
        let ff = f + df;
        if ff < 0 || ff > 7 { continue; }
        for rr in (our_rank + 1)..=6 {
            let idx = if us == Color::White { (rr * 8 + ff) } else { ((7 - rr) * 8 + ff) };
            let sq2 = unsafe { Square::new(idx as u8) };
            if b.piece_on(sq2) == Some(Piece::Pawn) && b.color_on(sq2) == Some(them) {
                return false;
            }
        }
    }
    true
}

// Useful if you later decide to add 7-man tablebase probes.
pub fn total_piece_count(b: &Board) -> i32 {
    let mut n = 0;
    for &c in &[Color::White, Color::Black] {
        for &p in &[Piece::Pawn, Piece::Knight, Piece::Bishop, Piece::Rook, Piece::Queen, Piece::King] {
            n += count_pieces(b, p, c);
        }
    }
    n
}

// (Used by search.rs for a fallback best move ordering)
pub fn prefer_cap_check_mvv_uci(b: &Board, m: ChessMove) -> (i32, i32, i32, String) {
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
pub fn pack_move(m: ChessMove) -> u16 {
    let from = m.get_source().to_index() as u16;
    let to = m.get_dest().to_index() as u16;
    let promo = match m.get_promotion() {
        Some(Piece::Knight) => 1, Some(Piece::Bishop) => 2,
        Some(Piece::Rook)   => 3, Some(Piece::Queen)  => 4, _ => 0,
    } as u16;
    (from & 63) | ((to & 63) << 6) | ((promo & 7) << 12)
}
pub fn unpack_move(code: u16) -> Option<ChessMove> {
    if code == 0 { return None; }
    let from_idx = (code & 63) as u8;
    let to_idx   = ((code >> 6) & 63) as u8;
    let from = unsafe { Square::new(from_idx) };
    let to   = unsafe { Square::new(to_idx) };
    let promo = match (code >> 12) & 7 {
        1 => Some(Piece::Knight), 2 => Some(Piece::Bishop),
        3 => Some(Piece::Rook),   4 => Some(Piece::Queen), _ => None,
    };
    Some(ChessMove::new(from, to, promo))
}

// TT flags
pub const EXACT: i8 = 0;
pub const ALPHA: i8 = -1;
pub const BETA:  i8 = 1;
