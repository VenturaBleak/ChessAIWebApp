# Path: engine-svc/uci_reference_engine.py
"""
A tiny reference UCI engine in Python for demonstration/testing.
Implements the UCI protocol subset:
- uci / isready / ucinewgame / position / go movetime X / quit
Search is a naive fixed-depth negamax with material evaluation.
This is **not** a strong engine; replace with a real one in production.
"""
import sys
import time
import threading
import chess

MAX_DEPTH = 3  # keep very small to avoid CPU burn

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

def evaluate(board: chess.Board) -> int:
    # Simple material count from White's perspective
    score = 0
    for piece_type, val in PIECE_VALUES.items():
        score += len(board.pieces(piece_type, chess.WHITE)) * val
        score -= len(board.pieces(piece_type, chess.BLACK)) * val
    return score if board.turn == chess.WHITE else -score

def negamax(board: chess.Board, depth: int, alpha: int, beta: int) -> int:
    if depth == 0 or board.is_game_over():
        return evaluate(board)
    max_eval = -10_000_000
    for move in board.legal_moves:
        board.push(move)
        score = -negamax(board, depth - 1, -beta, -alpha)
        board.pop()
        if score > max_eval:
            max_eval = score
        if max_eval > alpha:
            alpha = max_eval
        if alpha >= beta:
            break
    return max_eval

class Timer:
    def __init__(self, movetime_ms: int):
        self.deadline = time.time() + movetime_ms / 1000.0
    def timed_out(self) -> bool:
        return time.time() >= self.deadline

class Engine:
    def __init__(self):
        self.board = chess.Board()
        self.search_lock = threading.Lock()

    def uci_loop(self):
        print("id name ReferencePython", flush=True)
        print("id author Open Source", flush=True)
        print("uciok", flush=True)
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if line == "isready":
                print("readyok", flush=True)
            elif line == "ucinewgame":
                with self.search_lock:
                    self.board = chess.Board()
            elif line.startswith("position"):
                with self.search_lock:
                    self.set_position(line)
            elif line.startswith("go "):
                args = line.split()
                if "movetime" in args:
                    ms = int(args[args.index("movetime") + 1])
                else:
                    ms = 1000
                with self.search_lock:
                    self.search_and_reply(ms)
            elif line == "quit":
                break

    def set_position(self, cmd: str):
        # position [fen <FEN> | startpos ]  moves m1 m2 ...
        tokens = cmd.split()
        if "startpos" in tokens:
            self.board = chess.Board()
            moves_index = tokens.index("startpos") + 1
        elif "fen" in tokens:
            fen_index = tokens.index("fen") + 1
            fen = " ".join(tokens[fen_index:fen_index + 6])
            self.board = chess.Board(fen)
            moves_index = fen_index + 6
        else:
            return
        if moves_index < len(tokens) and tokens[moves_index] == "moves":
            for san in tokens[moves_index + 1:]:
                move = chess.Move.from_uci(san)
                if move in self.board.legal_moves:
                    self.board.push(move)

    def search_and_reply(self, movetime_ms: int):
        timer = Timer(movetime_ms)
        best_move = None
        best_eval = -10_000_000
        start = time.time()
        depth = 1
        while depth <= MAX_DEPTH and not timer.timed_out():
            local_best = None
            local_eval = -10_000_000
            for move in list(self.board.legal_moves):
                self.board.push(move)
                score = -negamax(self.board, depth - 1, -10_000_000, 10_000_000)
                self.board.pop()
                if score > local_eval:
                    local_eval = score
                    local_best = move
                if timer.timed_out():
                    break
            if local_best is not None:
                best_move = local_best
                best_eval = local_eval
                elapsed = max(1e-3, time.time() - start)
                nps = int(self.board.fullmove_number / elapsed)  # placeholder
                print(f"info depth {depth} score cp {best_eval} nps {nps} pv {best_move.uci()}", flush=True)
            depth += 1
        if best_move is None:
            # Fallback to first legal move
            best_move = next(iter(self.board.legal_moves))
        print(f"bestmove {best_move.uci()}", flush=True)

if __name__ == "__main__":
    Engine().uci_loop()