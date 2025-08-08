import chess
import math
from typing import Optional, List, Tuple
from params import PIECE_VALUES, PSTS
from schemas import EngineParams

MATE_SCORE = 10_000

class Engine:
    def __init__(self, params: Optional[EngineParams] = None):
        self.params = params or EngineParams()

    # --- Evaluation ---
    def evaluate(self, board: chess.Board) -> int:
        if board.is_checkmate():
            return -MATE_SCORE if board.turn else MATE_SCORE
        if board.is_stalemate() or board.is_insufficient_material():
            return 0

        w = self.params
        score = 0

        # Material + PST
        for piece_type, symbol in [
            (chess.PAWN, 'P'), (chess.KNIGHT, 'N'), (chess.BISHOP, 'B'),
            (chess.ROOK, 'R'), (chess.QUEEN, 'Q'), (chess.KING, 'K')
        ]:
            pv = PIECE_VALUES[symbol]
            pst = PSTS[symbol]
            for sq in board.pieces(piece_type, chess.WHITE):
                score += int(w.w_material * pv)
                score += int(w.w_pst * pst[sq])
            for sq in board.pieces(piece_type, chess.BLACK):
                score -= int(w.w_material * pv)
                # mirror square for black
                msq = chess.square_mirror(sq)
                score -= int(w.w_pst * pst[msq])

        # Mobility (legal moves count difference)
        score += int(w.w_mobility * (self._mobility(board)))

        # King safety: penalty if king in check
        if board.is_check():
            score += int(-100 * w.w_king_safety) if board.turn else int(100 * w.w_king_safety)

        return score

    def _mobility(self, board: chess.Board) -> int:
        ours = sum(1 for _ in board.legal_moves)
        board.push(chess.Move.null())
        theirs = sum(1 for _ in board.legal_moves)
        board.pop()
        return ours - theirs

    # --- Search ---
    def bestmove(self, board: chess.Board) -> Tuple[Optional[chess.Move], int]:
        w = self.params
        alpha = -math.inf
        beta = math.inf
        best_move = None
        best_score = -math.inf
        for move in self._ordered_moves(board) if w.move_ordering else board.legal_moves:
            board.push(move)
            score = -self._search(board, w.depth - 1, -beta, -alpha)
            board.pop()
            if score > best_score:
                best_score = score
                best_move = move
            if score > alpha:
                alpha = score
        return best_move, int(best_score)

    def _search(self, board: chess.Board, depth: int, alpha: float, beta: float) -> int:
        if depth == 0:
            return self._qsearch(board, alpha, beta) if self.params.quiescence else self.evaluate(board)
        if board.is_game_over():
            return self.evaluate(board)

        best = -math.inf
        for move in self._ordered_moves(board) if self.params.move_ordering else board.legal_moves:
            board.push(move)
            score = -self._search(board, depth - 1, -beta, -alpha)
            board.pop()
            if score > best:
                best = score
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break  # beta cutoff
        return int(best)

    def _qsearch(self, board: chess.Board, alpha: float, beta: float) -> int:
        stand_pat = self.evaluate(board)
        if stand_pat >= beta:
            return int(beta)
        if alpha < stand_pat:
            alpha = stand_pat

        for move in board.legal_moves:
            if board.is_capture(move):
                board.push(move)
                score = -self._qsearch(board, -beta, -alpha)
                board.pop()
                if score >= beta:
                    return int(beta)
                if score > alpha:
                    alpha = score
        return int(alpha)

    def _ordered_moves(self, board: chess.Board) -> List[chess.Move]:
        # MVV-LVA style ordering: captures first by victim-attacker value; checks next
        def mvv_lva_key(m: chess.Move) -> int:
            if board.is_capture(m):
                victim = board.piece_type_at(m.to_square)
                attacker = board.piece_type_at(m.from_square)
                v = (victim or 0) * 10 - (attacker or 0)
                return 1000 + v
            if board.gives_check(m):
                return 100
            return 0
        return sorted(list(board.legal_moves), key=mvv_lva_key, reverse=True)