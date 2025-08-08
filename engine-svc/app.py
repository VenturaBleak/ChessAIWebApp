from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import chess
from engine import Engine
from schemas import BestMoveRequest, EvaluateRequest, EngineParams

app = FastAPI(title="engine-svc")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/bestmove")
def bestmove(req: BestMoveRequest):
    board = chess.Board(req.fen)
    eng = Engine(req.params or EngineParams())
    move, score = eng.bestmove(board)
    return {
        "move": move.uci() if move else None,
        "score": score
    }

@app.post("/evaluate")
def evaluate(req: EvaluateRequest):
    board = chess.Board(req.fen)
    eng = Engine(req.params or EngineParams())
    return {"score": eng.evaluate(board)}