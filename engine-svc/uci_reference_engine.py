"""
UCI reference engine — alpha–beta only (depth-driven), no time control.

Contract (unchanged):
    go depth <D> [rollouts <R>]
- `depth` drives the search (iterative deepening to D).
- `rollouts` is accepted for compatibility but **ignored** (we log this).

Core features (best-practice MVP):
- Negamax alpha–beta with:
    * Iterative deepening + aspiration windows
    * Quiescence (captures + checks) with futility guard
    * Transposition table (TT) with simple AGE-based replacement
    * Move ordering: TT move > captures (MVV-LVA) > killers > history > quiets
    * Null-move pruning (cross-version-safe push_null) with low-material guard
    * Late Move Reductions (LMR) on late quiet moves (pre-push flags)
    * Futility pruning at frontier (depth == 1) for quiet moves
    * Move-Count Pruning (MCP) for very late quiets
    * Check extension (+1 ply)
- Deterministic and simple; no time management.
- UCI `info` per completed depth; `info string` breadcrumbs for debugging.
"""

from __future__ import annotations
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import chess

# ---------------------------
# Tunables
# ---------------------------
DEFAULT_DEPTH = 8
DEFAULT_ROLLOUTS = 0              # accepted but ignored
MAX_AB_DEPTH = 64
INF = 60_000
MATE = 30_000

# Quiescence
Q_INCLUDE_CHECKS = True
Q_FUTILITY_MARGIN = 150  # cp

# LMR
LMR_MIN_DEPTH = 3
LMR_BASE_REDUCTION = 1            # base reduction in plies for late quiets

# Null-move pruning
NMP_MIN_DEPTH = 3
NMP_R = 2

# Frontier futility pruning (depth==1)
FUTILITY_MARGIN_BASE = 200        # cp

# Move-Count Pruning (skip very late quiet moves at depth>=3)
MCP_MIN_DEPTH = 3
MCP_START_AT = 6                  # after N moves, start skipping some quiets

# Aspiration windows
ASP_WINDOW = 24                   # centipawns

DEBUG = True                      # prints `info string ...` breadcrumbs

# ---------------------------
# Piece values and PSTs (light)
# ---------------------------
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

# Flat PSTs for stability / simplicity (kept zero – safe MVP)
PSTS = {
    chess.PAWN:   [0]*64,
    chess.KNIGHT: [0]*64,
    chess.BISHOP: [0]*64,
    chess.ROOK:   [0]*64,
    chess.QUEEN:  [0]*64,
    chess.KING:   [0]*64,
}

def _pst(piece_type: int, square: int, color: bool) -> int:
    arr = PSTS[piece_type]
    idx = square if color == chess.WHITE else chess.square_mirror(square)
    return arr[idx]

def _mvv_lva(board: chess.Board, m: chess.Move) -> int:
    if not board.is_capture(m):
        return 0
    victim = board.piece_type_at(m.to_square) or chess.PAWN
    attacker = board.piece_type_at(m.from_square) or chess.PAWN
    return 10_000 + PIECE_VALUES[victim]*10 - PIECE_VALUES[attacker]

# ---------------------------
# Evaluation (simple, stable)
# ---------------------------
def evaluate(board: chess.Board) -> int:
    """Centipawns from side-to-move POV."""
    # draw guards
    if board.is_repetition(3):
        return 0
    if board.is_checkmate():
        return -MATE
    if board.is_stalemate() or board.is_insufficient_material() or board.can_claim_draw():
        return 0

    score = 0
    # material + PST
    for color in (chess.WHITE, chess.BLACK):
        sign = 1 if color == chess.WHITE else -1
        for p in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            sqs = board.pieces(p, color)            # SquareSet
            score += sign * PIECE_VALUES[p] * len(sqs)
            for sq in sqs:
                score += sign * _pst(p, sq, color)
        # King PST
        kings = board.pieces(chess.KING, color)
        if kings:
            ksq = next(iter(kings))
            score += sign * _pst(chess.KING, ksq, color)

    # mobility (very light)
    mobility = len(list(board.legal_moves))
    score += mobility // 4

    return score if board.turn == chess.WHITE else -score

# ---------------------------
# TT
# ---------------------------
EXACT, ALPHA, BETA = 0, -1, 1

@dataclass
class TTEntry:
    depth: int
    score: int
    flag: int    # EXACT/ALPHA/BETA
    best: Optional[chess.Move]
    age: int

class TT:
    def __init__(self):
        self.table: Dict[int, TTEntry] = {}
        self.age = 0

    def key(self, board: chess.Board) -> int:
        if hasattr(board, "transposition_key"):
            try:
                return int(board.transposition_key())
            except TypeError:
                return board.transposition_key()
        if hasattr(board, "zobrist_hash"):
            return board.zobrist_hash()
        return hash(board.board_fen() + (' w' if board.turn else ' b'))

    def probe(self, key: int) -> Optional[TTEntry]:
        return self.table.get(key)

    def store(self, key: int, depth: int, score: int, flag: int, best: Optional[chess.Move]):
        prev = self.table.get(key)
        if (prev is None) or (depth > prev.depth) or (self.age > prev.age):
            self.table[key] = TTEntry(depth, score, flag, best, self.age)

# ---------------------------
# Search
# ---------------------------
class Search:
    def __init__(self):
        self.tt = TT()
        self.nodes = 0
        self.killers: Dict[int, Tuple[Optional[chess.Move], Optional[chess.Move]]] = {}
        self.history: Dict[Tuple[bool, int], int] = {}

    # cross-version-safe null move
    def _push_null(self, board: chess.Board):
        try:
            board.push_null()
        except AttributeError:
            board.push(chess.Move.null())

    def _ordered_moves(self, board: chess.Board, tt_move: Optional[chess.Move],
                       killers: Tuple[Optional[chess.Move], Optional[chess.Move]]) -> List[chess.Move]:
        moves = list(board.legal_moves)
        def key(m: chess.Move):
            k = 0
            if tt_move and m == tt_move: k += 1_000_000
            k += _mvv_lva(board, m)
            if m in killers: k += 500_000
            if board.gives_check(m): k += 5_000
            k += self.history.get((board.turn, m.to_square), 0)
            return k
        moves.sort(key=key, reverse=True)
        return moves

    def _qsearch(self, board: chess.Board, alpha: int, beta: int) -> int:
        self.nodes += 1
        stand = evaluate(board)
        if stand >= beta:
            return beta
        if alpha < stand:
            alpha = stand

        # delta/futility guard
        if stand + Q_FUTILITY_MARGIN < alpha:
            return alpha

        for m in board.legal_moves:
            if not (board.is_capture(m) or (Q_INCLUDE_CHECKS and board.gives_check(m))):
                continue
            board.push(m)
            score = -self._qsearch(board, -beta, -alpha)
            board.pop()
            if score >= beta:
                return beta
            if score > alpha:
                alpha = score
        return alpha

    def _likely_zugzwang(self, board: chess.Board) -> bool:
        """Crude detector to tone down null-move in low-material endings."""
        np_white = (
            320 * len(board.pieces(chess.KNIGHT, chess.WHITE)) +
            330 * len(board.pieces(chess.BISHOP, chess.WHITE)) +
            500 * len(board.pieces(chess.ROOK,   chess.WHITE)) +
            900 * len(board.pieces(chess.QUEEN,  chess.WHITE))
        )
        np_black = (
            320 * len(board.pieces(chess.KNIGHT, chess.BLACK)) +
            330 * len(board.pieces(chess.BISHOP, chess.BLACK)) +
            500 * len(board.pieces(chess.ROOK,   chess.BLACK)) +
            900 * len(board.pieces(chess.QUEEN,  chess.BLACK))
        )
        return (np_white + np_black) <= 1000  # ~ two rooks total or less

    def _negamax(self, board: chess.Board, depth: int, alpha: int, beta: int, ply: int, is_pv: bool) -> int:
        # Count this node
        self.nodes += 1

        # TT probe
        key = self.tt.key(board)
        tte = self.tt.probe(key)
        if tte and tte.depth >= depth:
            if tte.flag == EXACT:
                return tte.score
            if tte.flag == ALPHA and tte.score <= alpha:
                return tte.score
            if tte.flag == BETA and tte.score >= beta:
                return tte.score

        # Draw guard again (cheap in TT misses)
        if board.is_repetition(3):
            return 0

        in_check = board.is_check()
        if in_check:
            depth += 1  # check extension

        if depth <= 0:
            return self._qsearch(board, alpha, beta)

        # Null-move pruning (avoid in low-material endings)
        if (not in_check) and depth >= NMP_MIN_DEPTH and not self._likely_zugzwang(board):
            try:
                self._push_null(board)
                r = NMP_R
                score = -self._negamax(board, depth - 1 - r, -beta, -beta + 1, ply + 1, False)
                board.pop()
                if score >= beta:
                    return beta
            except Exception as e:
                if DEBUG:
                    print(f"info string dbg=nullmove error={type(e).__name__}:{e}", flush=True)

        orig_alpha = alpha
        best_move = None
        best_score = -INF

        killers = self.killers.get(ply, (None, None))
        tt_move = tte.best if tte else None

        moves = self._ordered_moves(board, tt_move, killers)
        move_index = 0

        # Frontier futility pruning helper (depth==1, quiets)
        static_eval = None
        if depth == 1:
            static_eval = evaluate(board)

        for m in moves:
            # pre-push flags for pruning/LMR
            is_cap = board.is_capture(m)
            gives_chk = board.gives_check(m)

            # Frontier futility pruning (depth==1, very safe: skip quiets that cannot raise alpha)
            if depth == 1 and not is_cap and not gives_chk:
                if static_eval is None:
                    static_eval = evaluate(board)
                if static_eval + FUTILITY_MARGIN_BASE <= alpha:
                    move_index += 1
                    continue

            # Move-Count Pruning: at deeper plies, skip very late quiets
            if (depth >= MCP_MIN_DEPTH and move_index >= MCP_START_AT and not is_cap and not gives_chk):
                move_index += 1
                continue

            board.push(m)

            # LMR for late quiets in non-PV, non-check child
            reduce = 0
            if (depth >= LMR_MIN_DEPTH and not is_pv and not is_cap and not gives_chk and not board.is_check()):
                reduce = LMR_BASE_REDUCTION + (1 if move_index >= 4 else 0)
                new_depth = max(1, depth - 1 - reduce)
                score = -self._negamax(board, new_depth, -alpha - 1, -alpha, ply + 1, False)
                if score > alpha:
                    score = -self._negamax(board, depth - 1, -beta, -alpha, ply + 1, False)
            else:
                # PVS: first move full window, others try null-window first
                if move_index == 0:
                    score = -self._negamax(board, depth - 1, -beta, -alpha, ply + 1, is_pv)
                else:
                    score = -self._negamax(board, depth - 1, -alpha - 1, -alpha, ply + 1, False)
                    if score > alpha and score < beta:
                        score = -self._negamax(board, depth - 1, -beta, -alpha, ply + 1, True)

            board.pop()
            move_index += 1

            if score > best_score:
                best_score = score
                best_move = m
                if score > alpha:
                    alpha = score
                    # killers/history for quiet beta-cuts
                    if alpha >= beta:
                        if not is_cap:
                            k0, _k1 = killers
                            self.killers[ply] = (m, k0)
                            self.history[(board.turn, m.to_square)] = self.history.get((board.turn, m.to_square), 0) + depth*depth
                        break

        # Store in TT
        flag = EXACT
        if best_score <= orig_alpha:
            flag = ALPHA
        elif best_score >= beta:
            flag = BETA
        self.tt.store(key, depth, best_score, flag, best_move)

        return best_score

    def _pv_line(self, board: chess.Board, depth: int) -> List[chess.Move]:
        pv = []
        b = board.copy(stack=False)
        for _ in range(depth):
            tte = self.tt.probe(self.tt.key(b))
            if not tte or not tte.best or tte.best not in b.legal_moves:
                break
            pv.append(tte.best)
            b.push(tte.best)
        return pv

    def search(self, board: chess.Board, max_depth: int):
        """Iterative deepening with aspiration windows; yields after each completed depth."""
        self.nodes = 0
        self.tt.age += 1

        last_score = evaluate(board)  # seed for aspiration
        overall_start = time.time()
        max_d = min(MAX_AB_DEPTH, max_depth)
        best_at_last_depth: Optional[chess.Move] = None

        for depth in range(1, max_d + 1):
            if DEBUG:
                print(f"info string dbg=iter depth={depth}", flush=True)

            # aspiration window around last score
            alpha = last_score - ASP_WINDOW
            beta  = last_score + ASP_WINDOW

            while True:
                score = self._negamax(board, depth, alpha, beta, 0, True)
                if score <= alpha:
                    alpha -= 2*ASP_WINDOW
                    continue
                if score >= beta:
                    beta += 2*ASP_WINDOW
                    continue
                break

            last_score = score
            pv = self._pv_line(board, depth)
            if pv:
                best_at_last_depth = pv[0]

            nps = int(self.nodes / max(1e-6, time.time() - overall_start))
            pv_str = " ".join(m.uci() for m in pv)
            print(f"info depth {depth} nodes {self.nodes} nps {nps} score cp {last_score} pv {pv_str}", flush=True)
            yield best_at_last_depth

# ---------------------------
# Engine (UCI)
# ---------------------------
class Engine:
    def __init__(self):
        self.board = chess.Board()
        self.searcher = Search()
        if DEBUG:
            print("info string dbg=engine init", flush=True)

    def _handle_position(self, cmd: str):
        # position [fen <fen> | startpos ]  moves ...
        parts = cmd.split()
        if "startpos" in parts:
            self.board = chess.Board()
            idx = parts.index("startpos") + 1
        elif "fen" in parts:
            idx = parts.index("fen") + 1
            fen = " ".join(parts[idx:idx+6])
            self.board = chess.Board(fen)
            idx += 6
        else:
            return
        if idx < len(parts) and parts[idx] == "moves":
            for mv in parts[idx+1:]:
                self.board.push_uci(mv)

    def _current_best_or_default(self) -> str:
        legal = list(self.board.legal_moves)
        if not legal:
            return "0000"
        # deterministic fallback: prefer captures/checks
        legal.sort(key=lambda m: (self.board.is_capture(m), self.board.gives_check(m)), reverse=True)
        return legal[0].uci()

    def uci_loop(self):
        print("id name PyRefEngine (AB-only)")
        print("id author open-source")
        print("uciok")
        sys.stdout.flush()
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            cmd = line.strip()
            if DEBUG:
                print(f"info string dbg=recv '{cmd}'", flush=True)

            if cmd == "isready":
                print("readyok")
                sys.stdout.flush()

            elif cmd == "uci":
                print("id name PyRefEngine (AB-only)")
                print("id author open-source")
                print("uciok")
                sys.stdout.flush()

            elif cmd.startswith("ucinewgame"):
                self.board = chess.Board()
                self.searcher = Search()

            elif cmd.startswith("position "):
                self._handle_position(cmd)

            elif cmd.startswith("go "):
                # Parse args: keep 'rollouts' for compatibility, but ignore it
                parts = cmd.split()
                depth = None
                rollouts = None
                i = 0
                while i < len(parts):
                    if parts[i] == "depth" and i+1 < len(parts):
                        depth = int(parts[i+1]); i += 2; continue
                    if parts[i] == "rollouts" and i+1 < len(parts):
                        rollouts = int(parts[i+1]); i += 2; continue
                    i += 1
                depth = depth or DEFAULT_DEPTH
                if rollouts is None:
                    rollouts = DEFAULT_ROLLOUTS

                if DEBUG:
                    print(f"info string dbg=go depth={depth} rollouts={rollouts} (rollouts ignored; AB-only)", flush=True)

                # AB search (iterative deepening)
                best = None
                for bm in self.searcher.search(self.board, depth):
                    best = bm

                best_uci = self._current_best_or_default() if best is None else best.uci()
                print(f"bestmove {best_uci}")
                sys.stdout.flush()

            elif cmd == "stop":
                # Best-effort immediate best
                print(f"bestmove {self._current_best_or_default()}")
                sys.stdout.flush()

            elif cmd == "quit":
                if DEBUG:
                    print("info string dbg=quit", flush=True)
                break

if __name__ == "__main__":
    Engine().uci_loop()