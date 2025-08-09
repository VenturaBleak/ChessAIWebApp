# Path: engine-svc/uci_reference_engine.py
"""
UCI reference engine — time-synchronized AB + MCTS with predictive gating.

What’s new (surgical):
- Single absolute deadline shared by alpha–beta and MCTS.
- Predictive depth gating: estimate next depth cost from previous depths; if it likely won’t
  finish before the deadline (leaving a small MCTS tail), don’t start it — switch to MCTS.
- MCTS runs until the *same* deadline. When time’s up, we print the best move immediately.
- Extra debug prints (`info string dbg=...`) so you can see the time manager’s decisions.

Strength features retained:
- PVS alpha–beta with quiescence, TT (aging, depth-prefer), aspiration windows, LMR, null-move pruning,
  check extensions, MVV-LVA + killer + history ordering.
- Fast, principled eval: material + PSTs + bishop pair + pawn structure + rook (semi-)open + light king safety + mobility.
"""
from __future__ import annotations

import sys
import time
import math
import random
import multiprocessing as mp
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import chess

# ---------------------------
# Tunables (documented)
# ---------------------------
MAX_AB_DEPTH = 64                 # Hard safety ceiling; the deadline usually stops us earlier.
QUIESCENCE_DELTA = 900            # Futility margin in quiescence (centipawns).
TT_SIZE_MB = 96                   # Transposition table capacity (approximate).
ASPIRATION_WINDOW = 24            # +/- window (cp) around last score; adapts on fail highs/lows.
PUCT_C = 1.6                      # Exploration constant for root PUCT (MCTS).
MCTS_MIN_PLIES = 10               # Playout depth target; stops early on quiet positions.
LMR_BASE = 0.75                   # Late-Move-Reduction intensity (higher => more reduction).
NULL_MOVE_REDUCTION = 2           # Null-move pruning reduction (plies).
MOBILITY_SCALE = 4                # Mobility scale divisor for eval (smaller => more mobility weight).

# Time manager knobs (new)
MCTS_TAIL_MIN = 0.10              # We try to reserve this many seconds for root MCTS refinement.
TIME_SAFETY_MARGIN = 0.015        # Safety margin to avoid overshooting the deadline.
PREDICT_GROWTH_CAP = 3.0          # Cap on predicted time growth from last depth to next.
PREDICT_MIN_GROWTH = 1.6          # Minimum multiplier when we only have one datapoint.
DEBUG = True                      # Emit UCI `info string dbg=...` breadcrumbs.

# ---------------------------
# Evaluation components
# ---------------------------
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

# modest PSTs (middlegame bias)
PST_PAWN = [
      0,  0,  0,  0,  0,  0,  0,  0,
     50, 50, 50, 50, 50, 50, 50, 50,
     10, 10, 20, 30, 30, 20, 10, 10,
      5,  5, 10, 25, 25, 10,  5,  5,
      0,  0,  0, 20, 20,  0,  0,  0,
      5, -5,-10,  0,  0,-10, -5,  5,
      5, 10, 10,-20,-20, 10, 10,  5,
      0,  0,  0,  0,  0,  0,  0,  0,
]
PST_KNIGHT = [
    -50,-40,-30,-30,-30,-30,-40,-50,
    -40,-20,  0,  0,  0,  0,-20,-40,
    -30,  0, 10, 15, 15, 10,  0,-30,
    -30,  5, 15, 20, 20, 15,  5,-30,
    -30,  0, 15, 20, 20,  15,  0,-30,
    -30,  5, 10, 15, 15, 10,  5,-30,
    -40,-20,  0,  5,  5,  0,-20,-40,
    -50,-40,-30,-30,-30,-30,-40,-50,
]
PST_BISHOP = [
    -20,-10,-10,-10,-10,-10,-10,-20,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -10,  0,  5, 10, 10,  5,  0,-10,
    -10,  5,  5, 10, 10,  5,  5,-10,
    -10,  0, 10, 10,  10, 10,  0,-10,
    -10, 10, 10, 10, 10, 10, 10,-10,
    -10,  5,  0,  0,  0,  0,  5,-10,
    -20,-10,-10,-10,-10,-10,-10,-20,
]
PST_ROOK = [
     0,  0,  5, 10, 10,  5,  0,  0,
    -5,  0,  0,  0,  0,  0,  0, -5,
    -5,  0,  0,  0,  0,  0,  0, -5,
    -5,  0,  0,  0,  0,  0,  0, -5,
    -5,  0,  0,  0,  0,  0,  0, -5,
    -5,  0,  0,  0,  0,  0,  0, -5,
     5, 10, 10, 10, 10, 10, 10,  5,
     0,  0,  0,  0,  0,   0,  0,  0,
]
PST_QUEEN = [
    -20,-10,-10, -5, -5,-10,-10,-20,
    -10,  0,  5,  0,  0,  0,  0,-10,
    -10,  5,  5,  5,  5,  5,  0,-10,
     -5,  0,  5,  5,  5,  5,  0, -5,
      0,  0,  5,  5,  5,  5,  0, -5,
    -10,  0,  5,  5,  5,  5,  0,-10,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -20,-10,-10, -5, -5,-10,-10,-20,
]
PSTS = {
    chess.PAWN: PST_PAWN,
    chess.KNIGHT: PST_KNIGHT,
    chess.BISHOP: PST_BISHOP,
    chess.ROOK: PST_ROOK,
    chess.QUEEN: PST_QUEEN,
}
BISHOP_PAIR = 30
ISOLATED_PAWN = -12
DOUBLED_PAWN = -16
PASSED_PAWN_BY_RANK = [0, 0, 15, 30, 50, 80, 120, 0]  # white perspective ranks 0..7
ROOK_SEMI_OPEN = 14
ROOK_OPEN = 24
KING_PAWN_SHIELD = 6

# MVV-LVA for captures ordering
MVV_LVA = {(victim, attacker): PIECE_VALUES[victim] * 10 - PIECE_VALUES[attacker]
           for victim in PIECE_VALUES for attacker in PIECE_VALUES}

@dataclass
class TTEntry:
    depth: int
    score: int
    flag: int   # 0=EXACT, -1=ALPHA, 1=BETA
    best: Optional[chess.Move]
    age: int

EXACT, ALPHA, BETA = 0, -1, 1

# ---------------------------
# Eval helpers
# ---------------------------
def pst_score(board: chess.Board) -> int:
    score = 0
    for pt in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        for sq in board.pieces(pt, chess.WHITE):
            score += PSTS[pt][sq]
        for sq in board.pieces(pt, chess.BLACK):
            score -= PSTS[pt][chess.square_mirror(sq)]
    return score

def evaluate(board: chess.Board) -> int:
    """Positive is good for side to move."""
    if board.is_checkmate():
        return -30000
    if board.is_stalemate() or board.is_insufficient_material() or board.can_claim_draw():
        return 0

    piece_map = board.piece_map()
    material = 0
    wpawns_files = [0]*8
    bpawns_files = [0]*8

    for sq, p in piece_map.items():
        val = PIECE_VALUES[p.piece_type]
        material += val if p.color == chess.WHITE else -val
        if p.piece_type != chess.KING:
            if p.color == chess.WHITE:
                material += PSTS.get(p.piece_type, [0]*64)[sq]
            else:
                material -= PSTS.get(p.piece_type, [0]*64)[chess.square_mirror(sq)]
        if p.piece_type == chess.PAWN:
            f = chess.square_file(sq)
            if p.color == chess.WHITE:
                wpawns_files[f] += 1
            else:
                bpawns_files[f] += 1

    # bishop pair
    if len(board.pieces(chess.BISHOP, chess.WHITE)) >= 2: material += BISHOP_PAIR
    if len(board.pieces(chess.BISHOP, chess.BLACK)) >= 2: material -= BISHOP_PAIR

    # doubled/isolated pawns
    for f in range(8):
        w = wpawns_files[f]; b = bpawns_files[f]
        if w > 1: material += DOUBLED_PAWN * (w - 1)
        if b > 1: material -= DOUBLED_PAWN * (b - 1)
        if w >= 1 and (f == 0 or wpawns_files[f-1] == 0) and (f == 7 or wpawns_files[f+1] == 0):
            material += ISOLATED_PAWN * w
        if b >= 1 and (f == 0 or bpawns_files[f-1] == 0) and (f == 7 or bpawns_files[f+1] == 0):
            material -= ISOLATED_PAWN * b

    # passed pawns
    for sq in board.pieces(chess.PAWN, chess.WHITE):
        f = chess.square_file(sq); r = chess.square_rank(sq)
        blocked = False
        for rr in range(r+1, 8):
            for ff in (f-1, f, f+1):
                if 0 <= ff < 8:
                    if board.piece_at(chess.square(ff, rr)) == chess.Piece(chess.PAWN, chess.BLACK):
                        blocked = True; break
            if blocked: break
        if not blocked:
            material += PASSED_PAWN_BY_RANK[r]
    for sq in board.pieces(chess.PAWN, chess.BLACK):
        f = chess.square_file(sq); r = chess.square_rank(sq)
        blocked = False
        for rr in range(r-1, -1, -1):
            for ff in (f-1, f, f+1):
                if 0 <= ff < 8:
                    if board.piece_at(chess.square(ff, rr)) == chess.Piece(chess.PAWN, chess.WHITE):
                        blocked = True; break
            if blocked: break
        if not blocked:
            material -= PASSED_PAWN_BY_RANK[7-r]

    # rooks on (semi-)open files
    for sq in board.pieces(chess.ROOK, chess.WHITE):
        f = chess.square_file(sq)
        material += ROOK_OPEN if (wpawns_files[f]==0 and bpawns_files[f]==0) else (ROOK_SEMI_OPEN if wpawns_files[f]==0 else 0)
    for sq in board.pieces(chess.ROOK, chess.BLACK):
        f = chess.square_file(sq)
        material -= ROOK_OPEN if (wpawns_files[f]==0 and bpawns_files[f]==0) else (ROOK_SEMI_OPEN if bpawns_files[f]==0 else 0)

    # light king safety: pawn shield
    def pawn_shield(color: chess.Color) -> int:
        king_sq = board.king(color)
        if king_sq is None:
            return 0
        kf = chess.square_file(king_sq)
        total = 0
        ranks = (1,2) if color == chess.WHITE else (6,5)
        for f in [kf] + ([kf-1] if kf-1>=0 else []) + ([kf+1] if kf+1<=7 else []):
            for r in ranks:
                pc = board.piece_at(chess.square(f, r))
                if pc and pc.piece_type == chess.PAWN and pc.color == color:
                    total += 1
        return total * 6
    material += pawn_shield(chess.WHITE) - pawn_shield(chess.BLACK)

    mobility = len(list(board.legal_moves))
    score = (material + mobility // MOBILITY_SCALE) if board.turn == chess.WHITE else (-(material) + mobility // MOBILITY_SCALE)
    return score

def tt_key(board: chess.Board) -> int:
    if hasattr(board, "transposition_key"):
        try: return int(board.transposition_key())
        except TypeError: return board.transposition_key()
    if hasattr(board, "zobrist_hash"):
        return board.zobrist_hash()
    return hash(board.fen())

def move_ordering(board: chess.Board, tt_move: Optional[chess.Move],
                  killers: Tuple[Optional[chess.Move], Optional[chess.Move]],
                  history: Dict) -> List[chess.Move]:
    moves = list(board.legal_moves)
    def score(mv: chess.Move):
        if tt_move and mv == tt_move: return 10_000_000
        if mv in killers: return 5_000_000
        if board.is_capture(mv):
            victim = board.piece_type_at(mv.to_square) or chess.PAWN
            attacker = board.piece_type_at(mv.from_square) or chess.PAWN
            return 1_000_000 + MVV_LVA.get((victim, attacker), 0)
        return history.get((board.turn, mv.to_square), 0)
    moves.sort(key=score, reverse=True)
    return moves

@dataclass
class TTEntry:
    depth: int
    score: int
    flag: int
    best: Optional[chess.Move]
    age: int

EXACT, ALPHA, BETA = 0, -1, 1

@dataclass
class SearchResult:
    score: int
    best: Optional[chess.Move]
    pv: List[chess.Move]

# ---------------------------
# Search
# ---------------------------
class Search:
    def __init__(self):
        self.tt: Dict[int, TTEntry] = {}
        self.tt_cap = (TT_SIZE_MB * 1024 * 1024) // 32
        self.killers: Dict[int, Tuple[Optional[chess.Move], Optional[chess.Move]]] = {}
        self.history: Dict[Tuple[bool, int], int] = {}
        self.nodes = 0
        self.start_time = 0.0
        self.deadline_at = 0.0
        self.age = 0

    # timing
    def start(self, total_seconds: float, *, deadline_at: Optional[float] = None):
        now = time.time()
        self.start_time = now
        self.deadline_at = deadline_at if deadline_at is not None else (now + total_seconds)

    def time_left(self) -> float:
        return max(0.0, self.deadline_at - time.time())

    def out_of_time(self) -> bool:
        return time.time() >= self.deadline_at

    # TT
    def tt_lookup(self, key: int) -> Optional[TTEntry]: return self.tt.get(key)

    def tt_store(self, key: int, entry: TTEntry):
        if len(self.tt) >= self.tt_cap:
            drop = len(self.tt)//8
            for k, v in list(self.tt.items())[:drop]:
                if v.age + 2 < self.age:
                    self.tt.pop(k, None)
        old = self.tt.get(key)
        if old is None or entry.depth >= old.depth or old.age + 2 < self.age:
            self.tt[key] = entry

    # Quiescence
    def quiesce(self, board: chess.Board, alpha: int, beta: int, ply: int) -> int:
        if self.out_of_time():
            return evaluate(board)
        self.nodes += 1
        stand = evaluate(board)
        if stand >= beta:
            return beta
        if alpha < stand:
            alpha = stand
        if stand + QUIESCENCE_DELTA < alpha:
            return alpha
        ent = self.tt_lookup(tt_key(board))
        tt_move = ent.best if ent else None
        killers = self.killers.get(ply, (None, None))
        for mv in move_ordering(board, tt_move, killers, self.history):
            if not board.is_capture(mv): continue
            board.push(mv)
            sc = -self.quiesce(board, -beta, -alpha, ply+1)
            board.pop()
            if sc >= beta: return beta
            if sc > alpha: alpha = sc
        return alpha

    # PVS + LMR + null-move + check extensions
    def negamax(self, board: chess.Board, depth: int, alpha: int, beta: int, ply: int) -> int:
        if self.out_of_time():
            return evaluate(board)

        key = tt_key(board)
        ent = self.tt_lookup(key)
        if ent and ent.depth >= depth:
            if ent.flag == EXACT: return ent.score
            if ent.flag == ALPHA and ent.score <= alpha: return ent.score
            if ent.flag == BETA and ent.score >= beta: return ent.score

        in_check = board.is_check()
        if in_check:
            depth += 1

        if depth <= 0:
            return self.quiesce(board, alpha, beta, ply)

        # Null move
        if not in_check and depth >= 3:
            board.push(chess.Move.null())
            sc = -self.negamax(board, depth - 1 - NULL_MOVE_REDUCTION, -beta, -beta+1, ply+1)
            board.pop()
            if sc >= beta:
                return beta

        self.nodes += 1
        best = None
        orig_alpha = alpha
        killers = self.killers.get(ply, (None, None))
        tt_move = ent.best if ent else None
        moves = move_ordering(board, tt_move, killers, self.history)

        first = True
        idx = 0
        for mv in moves:
            idx += 1
            is_quiet = (not board.is_capture(mv)) and (not board.gives_check(mv))
            board.push(mv)
            if not in_check and is_quiet and depth >= 3 and idx > 3:
                red = int(LMR_BASE * math.log2(2 + idx))
                red = min(red, depth - 1)
                sc = -self.negamax(board, depth - 1 - red, -alpha-1, -alpha, ply+1)
                if sc > alpha:
                    sc = -self.negamax(board, depth - 1, -beta, -alpha, ply+1)
            else:
                if first:
                    sc = -self.negamax(board, depth - 1, -beta, -alpha, ply+1)
                    first = False
                else:
                    sc = -self.negamax(board, depth - 1, -alpha-1, -alpha, ply+1)
                    if sc > alpha and sc < beta:
                        sc = -self.negamax(board, depth - 1, -beta, -alpha, ply+1)
            board.pop()

            if sc >= beta:
                k1, _ = killers
                if is_quiet and k1 != mv:
                    self.killers[ply] = (mv, k1)
                self.tt_store(key, TTEntry(depth, beta, BETA, mv, self.age))
                return beta

            if sc > alpha:
                alpha = sc
                best = mv
                if is_quiet:
                    hk = (board.turn, mv.to_square)
                    self.history[hk] = self.history.get(hk, 0) + depth*depth

        if best is None:
            return -30000 + ply if in_check else 0
        flag = EXACT if alpha != orig_alpha else ALPHA
        self.tt_store(key, TTEntry(depth, alpha, flag, best, self.age))
        return alpha

    def pv_line(self, board: chess.Board, depth: int) -> List[chess.Move]:
        pv = []
        seen = set()
        for _ in range(depth):
            ent = self.tt.get(tt_key(board))
            if not ent or not ent.best: break
            mv = ent.best
            if mv in seen: break
            seen.add(mv)
            pv.append(mv)
            board.push(mv)
        for _ in range(len(pv)):
            board.pop()
        return pv

    def search_iterative(self, board: chess.Board, max_depth_hint: int, mcts_tail_min: float) -> SearchResult:
        """
        Iterative deepening with aspiration windows up to last fully completed depth.
        Before starting depth d+1, use measured times of prior depths to estimate if there’s
        enough time left to finish d+1 *and* leave mcts_tail_min seconds for MCTS; if not,
        we stop here and hand over to MCTS.
        """
        self.age += 1
        self.nodes = 0
        last = evaluate(board)
        best_move = None
        best_pv: List[chess.Move] = []
        depth_times: List[float] = []

        start_all = time.time()
        for depth in range(1, min(MAX_AB_DEPTH, max_depth_hint) + 1):
            # Predictive gating BEFORE starting this depth (except for depth=1)
            if depth_times:
                remaining = self.time_left()
                last_t = depth_times[-1]
                # Growth estimate: if we have 2+ depths, use ratio; else a minimum multiplier
                if len(depth_times) >= 2:
                    ratio = depth_times[-1] / max(1e-3, depth_times[-2])
                    growth = min(PREDICT_GROWTH_CAP, max(PREDICT_MIN_GROWTH, ratio * 1.10))
                else:
                    growth = PREDICT_MIN_GROWTH
                predicted = last_t * growth
                need = predicted + mcts_tail_min + TIME_SAFETY_MARGIN
                if remaining < need:
                    if DEBUG:
                        print(f"info string dbg=gating stop_before depth={depth} remaining={remaining:.3f}s "
                              f"pred_next={predicted:.3f}s mcts_tail={mcts_tail_min:.3f}s", flush=True)
                    break

            if self.out_of_time():
                break

            depth_start = time.time()
            low, high = last - ASPIRATION_WINDOW, last + ASPIRATION_WINDOW
            while True:
                if self.out_of_time(): break
                sc = self.negamax(board, depth, low, high, 0)
                if self.out_of_time(): break
                if sc <= low:
                    low -= ASPIRATION_WINDOW * 2
                    continue
                if sc >= high:
                    high += ASPIRATION_WINDOW * 2
                    continue
                last = sc
                break
            if self.out_of_time():
                break

            pv = self.pv_line(board, depth)
            if pv:
                best_move = pv[0]
                best_pv = pv

            dt = time.time() - depth_start
            depth_times.append(dt)
            nps = int(self.nodes / max(1e-3, time.time() - self.start_time))
            if best_move is not None:
                pv_str = ' '.join(m.uci() for m in best_pv)
                print(f"info depth {depth} score cp {last} nodes {self.nodes} nps {nps} time {int((time.time()-start_all)*1000)} pv {pv_str}", flush=True)
            if DEBUG and depth % 3 == 0:
                print(f"info string dbg=iter depth={depth} took={dt:.3f}s left={self.time_left():.3f}s last={last} nps={nps}", flush=True)

        return SearchResult(last, best_move, best_pv)

# ---------------------------
# Root MCTS (PUCT with chess-aware playouts)
# ---------------------------
class RootMCTS:
    def __init__(self):
        self.stats = {}  # move_uci -> [N, W, P]

    def _policy_priors(self, board: chess.Board, moves: List[chess.Move]) -> Dict[str, float]:
        scores = []
        for mv in moves:
            s = 0.0
            if board.is_capture(mv):
                victim = board.piece_type_at(mv.to_square) or chess.PAWN
                attacker = board.piece_type_at(mv.from_square) or chess.PAWN
                s += 1.0 + MVV_LVA.get((victim, attacker), 0) / 1000.0
            if board.gives_check(mv):
                s += 0.6
            scores.append(s)
        if not scores:
            return {}
        maxs = max(scores)
        exps = [math.exp(s - maxs) for s in scores]
        denom = sum(exps)
        return {m.uci(): e/denom for m, e in zip(moves, exps)}

    def _guided_playout(self, board: chess.Board, max_plies: int) -> int:
        plies = 0
        while plies < max_plies and not board.is_game_over():
            legal = list(board.legal_moves)
            def mscore(mv: chess.Move):
                sc = 0
                if board.gives_check(mv): sc += 500
                if board.is_capture(mv):
                    victim = board.piece_type_at(mv.to_square) or chess.PAWN
                    attacker = board.piece_type_at(mv.from_square) or chess.PAWN
                    sc += 100 + MVV_LVA.get((victim, attacker), 0)
                return sc
            m = max(legal, key=mscore)
            board.push(m); plies += 1
            if plies >= 6 and not board.is_check() and (not board.is_capture(board.peek())):
                break
        sc = evaluate(board)
        return sc if (plies % 2 == 0) else -sc

    def refine(self, board: chess.Board, best_hint: Optional[chess.Move], deadline_at: float):
        moves = list(board.legal_moves)
        if not moves:
            return None
        priors = self._policy_priors(board, moves)
        self.stats = {m.uci(): [0, 0.0, max(1e-6, priors.get(m.uci(), 1.0/len(moves)))] for m in moves}

        total_sims = 0
        last_report = 0
        while time.time() + TIME_SAFETY_MARGIN < deadline_at:
            total_sims += 1
            N_total = 1 + sum(v[0] for v in self.stats.values())
            best_ucb, best_uci = -1e18, None
            for uci, (N, W, P) in self.stats.items():
                Q = W / N if N > 0 else 0.0
                U = PUCT_C * P * math.sqrt(N_total) / (1 + N)
                val = Q + U
                if val > best_ucb:
                    best_ucb, best_uci = val, uci
            mv = chess.Move.from_uci(best_uci)
            board.push(mv)
            val = self._guided_playout(board, MCTS_MIN_PLIES)
            board.pop()
            st = self.stats[best_uci]
            st[0] += 1
            st[1] += val

            if DEBUG and (total_sims - last_report) >= 200:
                last_report = total_sims
                ranked = sorted(((u, (W/N if N>0 else 0.0), N) for u,(N,W,P) in self.stats.items()),
                                key=lambda x: x[1], reverse=True)[:3]
                msg = " | ".join(f"{u}:Q={q:.1f},N={n}" for u,q,n in ranked)
                print(f"info string dbg=mcts sims={total_sims} top={msg}", flush=True)

        # pick highest mean Q
        best_uci, best_q = None, -1e18
        for uci, (N, W, P) in self.stats.items():
            if N == 0: continue
            Q = W / N
            if Q > best_q:
                best_q, best_uci = Q, uci
        if best_uci is None:
            return best_hint or moves[0]
        return chess.Move.from_uci(best_uci)

# ---------------------------
# UCI engine wrapper
# ---------------------------
class Engine:
    def __init__(self):
        self.board = chess.Board()
        self.search = Search()
        self.mcts = RootMCTS()

    def uci_loop(self):
        print("id name PyRefEngine++")
        print("id author open-source")
        print("uciok")
        sys.stdout.flush()
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if line == "uci":
                print("id name PyRefEngine++")
                print("id author open-source")
                print("uciok")
                sys.stdout.flush()
            elif line == "isready":
                print("readyok"); sys.stdout.flush()
            elif line == "ucinewgame":
                self.board = chess.Board()
                self.search = Search()
                self.mcts = RootMCTS()
                sys.stdout.flush()
            elif line.startswith("position"):
                self._handle_position(line)
            elif line.startswith("go"):
                self._handle_go(line)
            elif line == "quit":
                break

    def _handle_position(self, line: str):
        parts = line.split()
        idx = 1
        if len(parts) >= 2 and parts[1] == "startpos":
            self.board = chess.Board(); idx = 2
        elif len(parts) >= 2 and parts[1] == "fen":
            fen = " ".join(parts[2:8]); self.board = chess.Board(fen); idx = 8
        if idx < len(parts) and parts[idx] == "moves":
            for mv in parts[idx+1:]:
                self.board.push_uci(mv)

    def _handle_go(self, line: str):
        depth = None
        movetime_ms = None
        parts = line.split()
        for i, tok in enumerate(parts):
            if tok == "depth" and i+1 < len(parts):
                depth = int(parts[i+1])
            elif tok == "movetime" and i+1 < len(parts):
                movetime_ms = int(parts[i+1])
        if movetime_ms is None and depth is None:
            movetime_ms = 1000
        if movetime_ms is not None:
            self._think_time(movetime_ms, depth or MAX_AB_DEPTH)
        else:
            self._think_depth(depth)

    def _think_depth(self, depth: int):
        self.search.start(3600.0)
        res = self.search.search_iterative(self.board, depth, mcts_tail_min=0.0)
        best_move = res.best or (next(iter(self.board.legal_moves), None))
        if best_move is None:
            print("bestmove 0000", flush=True)
        else:
            if DEBUG:
                print(f"info string dbg=depth-only best={best_move.uci()}", flush=True)
            print(f"bestmove {best_move.uci()}", flush=True)

    def _think_time(self, movetime_ms: int, max_depth_hint: int):
        total = max(0.02, movetime_ms / 1000.0)
        deadline = time.time() + total
        self.search.start(total, deadline_at=deadline)
        if DEBUG:
            print(f"info string dbg=go movetime_ms={movetime_ms} total={total:.3f}", flush=True)

        # 1) Iterative deepening with predictive gating
        res = self.search.search_iterative(self.board, max_depth_hint, mcts_tail_min=MCTS_TAIL_MIN)
        best_move = res.best

        # 2) MCTS tail until the exact deadline
        left = self.search.time_left()
        if left > TIME_SAFETY_MARGIN:
            if DEBUG:
                print(f"info string dbg=handoff to mcts left={left:.3f}s", flush=True)
            mcts_best = self.mcts.refine(self.board, best_move, deadline)
            if mcts_best is not None:
                best_move = mcts_best

        # 3) Deterministic fallback (never random)
        if not best_move:
            legal = list(self.board.legal_moves)
            if legal:
                def ord_key(m: chess.Move):
                    v = 0
                    if self.board.is_capture(m):
                        victim = self.board.piece_type_at(m.to_square) or chess.PAWN
                        attacker = self.board.piece_type_at(m.from_square) or chess.PAWN
                        v += MVV_LVA.get((victim, attacker), 0)
                    if self.board.gives_check(m):
                        v += 1000
                    return v
                legal.sort(key=ord_key, reverse=True)
                best_move = legal[0]
            else:
                best_move = None

        # 4) Finalize immediately at deadline
        if DEBUG:
            elapsed = time.time() - self.search.start_time
            print(f"info string dbg=done elapsed={elapsed:.3f}s left={self.search.time_left():.3f}s nodes={self.search.nodes}", flush=True)
        if best_move is None:
            print("bestmove 0000", flush=True)
        else:
            print(f"bestmove {best_move.uci()}", flush=True)

if __name__ == "__main__":
    Engine().uci_loop()