# Path: engine-svc/engines/ab_engine.py
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import chess

from .base import Engine as BaseEngine

# ---------------------------
# Tunables (unchanged)
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
ASP_MAX_WIDEN = 2048

DEBUG = True                      # prints `info string ...` breadcrumbs

# ---------------------------
# Piece values and PSTs (unchanged)
# ---------------------------
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}
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
# Utility: mate score normalize/de-normalize for TT
# ---------------------------
def _to_tt(score: int, ply: int) -> int:
    if score >= MATE - MAX_AB_DEPTH:
        return score + ply
    if score <= -MATE + MAX_AB_DEPTH:
        return score - ply
    return score

def _from_tt(score: int, ply: int) -> int:
    if score >= MATE - MAX_AB_DEPTH:
        return score - ply
    if score <= -MATE + MAX_AB_DEPTH:
        return score + ply
    return score

def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v

# ---------------------------
# Evaluation (unchanged)
# ---------------------------
def evaluate(board: chess.Board) -> int:
    if board.is_checkmate():
        return -MATE
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    if board.is_repetition(3) or board.can_claim_draw():
        return 0

    score = 0
    for color in (chess.WHITE, chess.BLACK):
        sign = 1 if color == chess.WHITE else -1
        for p in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            sqs = board.pieces(p, color)
            cnt = len(sqs)
            score += sign * PIECE_VALUES[p] * cnt
            for sq in sqs:
                score += sign * _pst(p, sq, color)
        kings = board.pieces(chess.KING, color)
        if kings:
            ksq = next(iter(kings))
            score += sign * _pst(chess.KING, ksq, color)

    mobility = len(list(board.legal_moves))
    score += mobility // 4

    return score if board.turn == chess.WHITE else -score

# ---------------------------
# TT (unchanged)
# ---------------------------
EXACT, ALPHA, BETA = 0, -1, 1

@dataclass
class TTEntry:
    depth: int
    score: int
    flag: int
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
# Search (unchanged)
# ---------------------------
class Search:
    def __init__(self):
        self.tt = TT()
        self.nodes = 0
        self.killers: Dict[int, Tuple[Optional[chess.Move], Optional[chess.Move]]] = {}
        self.history: Dict[Tuple[bool, int], int] = {}

    def _push_null(self, board: chess.Board):
        try:
            board.push_null()
        except AttributeError:
            board.push(chess.Move.null())

    def _ordered_moves(self, board: chess.Board, tt_move: Optional[chess.Move],
                       killers: Tuple[Optional[chess.Move], Optional[chess.Move]]) -> List[chess.Move]:
        moves = list(board.legal_moves)
        hist = self.history
        iscap = board.is_capture
        gives = board.gives_check
        def key(m: chess.Move):
            k = 0
            if tt_move and m == tt_move: k += 1_000_000
            k += _mvv_lva(board, m)
            if m in killers: k += 500_000
            if gives(m): k += 5_000
            k += hist.get((board.turn, m.to_square), 0)
            if iscap(m): k += 1
            return k
        moves.sort(key=key, reverse=True)
        return moves

    def _qsearch(self, board: chess.Board, alpha: int, beta: int) -> int:
        self.nodes += 1
        if board.is_checkmate():
            return -MATE
        if board.is_stalemate() or board.is_insufficient_material():
            return 0

        stand = evaluate(board)
        if stand >= beta:
            return beta
        if stand > alpha:
            alpha = stand

        if stand + Q_FUTILITY_MARGIN < alpha:
            return alpha

        legal_any = False
        for m in board.legal_moves:
            if not (board.is_capture(m) or (Q_INCLUDE_CHECKS and board.gives_check(m))):
                continue
            legal_any = True
            board.push(m)
            score = -self._qsearch(board, -beta, -alpha)
            board.pop()
            if score >= beta:
                return beta
            if score > alpha:
                alpha = score

        return alpha

    def _likely_zugzwang(self, board: chess.Board) -> bool:
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
        return (np_white + np_black) <= 1000

    def _negamax(self, board: chess.Board, depth: int, alpha: int, beta: int, ply: int, is_pv: bool) -> int:
        alpha = _clamp(alpha, -INF + 1, INF - 1)
        beta  = _clamp(beta,  -INF + 1, INF - 1)
        if alpha >= beta:
            alpha = beta - 1

        self.nodes += 1

        key = self.tt.key(board)
        tte = self.tt.probe(key)
        if tte and tte.depth >= depth:
            tts = _from_tt(tte.score, ply)
            if tte.flag == EXACT:
                return tts
            if tte.flag == ALPHA and tts <= alpha:
                return tts
            if tte.flag == BETA and tts >= beta:
                return tts

        if board.is_repetition(3) or board.can_claim_draw():
            return 0

        in_check = board.is_check()
        local_depth = depth + 1 if in_check else depth
        if local_depth <= 0:
            return self._qsearch(board, alpha, beta)

        if (not in_check) and local_depth >= NMP_MIN_DEPTH and not self._likely_zugzwang(board):
            try:
                self._push_null(board)
                r = NMP_R
                score = -self._negamax(board, local_depth - 1 - r, -beta, -beta + 1, ply + 1, False)
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

        static_eval = None
        if local_depth == 1:
            static_eval = evaluate(board)

        for m in moves:
            is_cap = board.is_capture(m)
            gives_chk = board.gives_check(m)

            if local_depth == 1 and not is_cap and not gives_chk:
                if static_eval is None:
                    static_eval = evaluate(board)
                if static_eval + FUTILITY_MARGIN_BASE <= alpha:
                    move_index += 1
                    continue

            if (local_depth >= MCP_MIN_DEPTH and move_index >= MCP_START_AT and not is_cap and not gives_chk):
                move_index += 1
                continue

            board.push(m)

            child_in_check = board.is_check()
            if (local_depth >= LMR_MIN_DEPTH and not is_pv and not is_cap and not gives_chk and not child_in_check):
                reduce = LMR_BASE_REDUCTION + (1 if move_index >= 4 else 0)
                new_depth = max(1, local_depth - 1 - reduce)
                score = -self._negamax(board, new_depth, -alpha - 1, -alpha, ply + 1, False)
                if score > alpha:
                    score = -self._negamax(board, local_depth - 1, -beta, -alpha, ply + 1, False)
            else:
                if move_index == 0:
                    score = -self._negamax(board, local_depth - 1, -beta, -alpha, ply + 1, is_pv)
                else:
                    score = -self._negamax(board, local_depth - 1, -alpha - 1, -alpha, ply + 1, False)
                    if score > alpha and score < beta:
                        score = -self._negamax(board, local_depth - 1, -beta, -alpha, ply + 1, True)

            board.pop()
            move_index += 1

            if score > best_score:
                best_score = score
                best_move = m
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        if not is_cap:
                            k0, _k1 = killers
                            self.killers[ply] = (m, k0)
                            self.history[(board.turn, m.to_square)] = self.history.get((board.turn, m.to_square), 0) + local_depth*local_depth
                        break

        if best_move is None and not list(board.legal_moves):
            return -MATE if board.is_check() else 0

        flag = EXACT
        if best_score <= orig_alpha:
            flag = ALPHA
        elif best_score >= beta:
            flag = BETA
        self.tt.store(key, depth, _to_tt(best_score, ply), flag, best_move)

        return best_score

    def _pv_line(self, board: chess.Board, depth: int) -> List[chess.Move]:
        pv = []
        b = board.copy(stack=False)
        for _ in range(depth):
            tte = self.tt.probe(self.tt.key(b))
            if not tte or not tte.best:
                break
            bm = tte.best
            if bm not in b.legal_moves:
                break
            pv.append(bm)
            b.push(bm)
        return pv

    def search(self, board: chess.Board, max_depth: int):
        self.nodes = 0
        self.tt.age += 1

        last_score = evaluate(board)
        overall_start = time.time()
        max_d = min(MAX_AB_DEPTH, max_depth)
        best_at_last_depth: Optional[chess.Move] = None

        for depth in range(1, max_d + 1):
            if DEBUG:
                print(f"info string dbg=iter depth={depth}", flush=True)

            window = ASP_WINDOW
            alpha = last_score - window
            beta  = last_score + window

            while True:
                score = self._negamax(board, depth, alpha, beta, 0, True)
                if score <= alpha and window < ASP_MAX_WIDEN:
                    window = min(ASP_MAX_WIDEN, window * 2)
                    alpha = score - window
                    beta  = alpha + 2*window
                    continue
                if score >= beta and window < ASP_MAX_WIDEN:
                    window = min(ASP_MAX_WIDEN, window * 2)
                    beta = score + window
                    alpha = beta - 2*window
                    continue
                break

            last_score = _clamp(score, -INF + 1, INF - 1)
            pv = self._pv_line(board, depth)
            if pv:
                best_at_last_depth = pv[0]

            spent = max(1e-6, time.time() - overall_start)
            nps = int(self.nodes / spent)
            pv_str = " ".join(m.uci() for m in pv)
            print(f"info depth {depth} nodes {self.nodes} nps {nps} score cp {last_score} pv {pv_str}", flush=True)
            yield best_at_last_depth

# ---------------------------
# AB Engine implementation
# ---------------------------
class ABEngine(BaseEngine):
    """Alpha-Beta engine: extracted from the legacy monolith with minimal edits."""

    def __init__(self):
        self.board = chess.Board()
        self.searcher = Search()
        if DEBUG:
            print("info string dbg=engine init", flush=True)

    # Keep exact ID lines to match old behavior
    def engine_name(self) -> str:
        return "PyRefEngine (AB-only)"

    def engine_author(self) -> str:
        return "open-source"

    def on_new_game(self) -> None:
        self.board = chess.Board()
        self.searcher = Search()

    def on_quit(self) -> None:
        # no resources to release beyond default; breadcrumb already printed by base
        pass

    # -- UCI command handlers (identical logic) --
    def handle_position_cmd(self, cmd: str) -> None:
        parts = cmd.split()
        try:
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
                    try:
                        self.board.push_uci(mv)
                    except Exception:
                        if DEBUG:
                            print(f"info string dbg=bad-move {mv}", flush=True)
        except Exception as e:
            if DEBUG:
                print(f"info string dbg=position-parse-error {type(e).__name__}:{e}", flush=True)
            self.board = chess.Board()

    def _current_best_or_default(self) -> str:
        legal = list(self.board.legal_moves)
        if not legal:
            return "0000"
        legal.sort(key=lambda m: (self.board.is_capture(m), self.board.gives_check(m)), reverse=True)
        return legal[0].uci()

    def bestmove_now(self) -> str:
        return self._current_best_or_default()

    def go(self, cmd: str) -> str:
        # Parse args: keep 'rollouts' for compatibility, but ignore it
        parts = cmd.split()
        depth = None
        rollouts = None
        i = 0
        while i < len(parts):
            if parts[i] == "depth" and i+1 < len(parts):
                try:
                    depth = int(parts[i+1])
                except ValueError:
                    depth = DEFAULT_DEPTH
                i += 2
                continue
            if parts[i] == "rollouts" and i+1 < len(parts):
                try:
                    rollouts = int(parts[i+1])
                except ValueError:
                    rollouts = DEFAULT_ROLLOUTS
                i += 2
                continue
            i += 1
        depth = depth or DEFAULT_DEPTH
        if rollouts is None:
            rollouts = DEFAULT_ROLLOUTS

        if DEBUG:
            print(f"info string dbg=go depth={depth} rollouts={rollouts} (rollouts ignored; AB-only)", flush=True)

        best = None
        for bm in self.searcher.search(self.board, depth):
            best = bm

        return self._current_best_or_default() if best is None else best.uci()