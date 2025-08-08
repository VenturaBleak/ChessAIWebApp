from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os, uuid, httpx, chess
from typing import Dict
from schemas import (
    NewGameRequest, MoveRequest, SelfPlayStepRequest, AiMoveRequest, EngineParams
)

ENGINE_URL = os.getenv("ENGINE_URL", "http://localhost:8001")

app = FastAPI(title="game-svc")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

class Game:
    def __init__(self, fen=None, ai_mode="human-vs-ai", ai_plays="black",
                 params_white: EngineParams = EngineParams(),
                 params_black: EngineParams = EngineParams()):
        self.board = chess.Board(fen) if fen else chess.Board()
        self.ai_mode = ai_mode
        self.ai_plays = ai_plays
        self.params_white = params_white
        self.params_black = params_black
        self.moves_san = []
        self.last_move = None

GAMES: Dict[str, Game] = {}

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/new")
def new_game(req: NewGameRequest):
    gid = str(uuid.uuid4())
    g = Game(
        fen=req.fen,
        ai_mode=req.ai_mode,
        ai_plays=req.ai_plays,
        params_white=req.params_white or EngineParams(),
        params_black=req.params_black or EngineParams(),
    )
    GAMES[gid] = g
    return {"gameId": gid, **state(gid)}

@app.get("/state")
def state(gameId: str):
    g = GAMES.get(gameId)
    if not g:
        raise HTTPException(404, "Unknown gameId")
    return {
        "fen": g.board.fen(),
        "turn": "white" if g.board.turn else "black",
        "legalMoves": [m.uci() for m in g.board.legal_moves],
        "movesSAN": g.moves_san,
        "result": _result(g.board),
        "lastMove": g.last_move,
        "aiMode": g.ai_mode,
        "aiPlays": g.ai_plays,
        "paramsWhite": g.params_white.model_dump(),
        "paramsBlack": g.params_black.model_dump(),
    }

@app.post("/move")
def move(req: MoveRequest):
    g = GAMES.get(req.gameId)
    if not g:
        raise HTTPException(404, "Unknown gameId")
    m = chess.Move.from_uci(req.move)
    if m not in g.board.legal_moves:
        raise HTTPException(400, "Illegal move")
    san = g.board.san(m)
    g.board.push(m)
    g.moves_san.append(san)
    g.last_move = req.move
    return state(req.gameId)

@app.post("/ai_move")
def ai_move(req: AiMoveRequest):
    g = GAMES.get(req.gameId)
    if not g:
        raise HTTPException(404, "Unknown gameId")
    if g.board.is_game_over():
        return state(req.gameId)

    side = "white" if g.board.turn else "black"
    if g.ai_mode == "human-vs-ai" and g.ai_plays not in ("both", side):
        raise HTTPException(400, "It's not AI's turn or AI doesn't play this side")

    params = g.params_white if g.board.turn else g.params_black
    with httpx.Client(timeout=30) as client:
        r = client.post(f"{os.getenv('ENGINE_URL','http://engine-svc:8001')}/bestmove",
                        json={"fen": g.board.fen(), "params": params.model_dump()})
        r.raise_for_status()
        move = r.json().get("move")
    if not move:
        raise HTTPException(500, "Engine returned no move")
    return move_and_return(g, req.gameId, move)

@app.post("/selfplay_step")
def selfplay_step(req: SelfPlayStepRequest):
    g = GAMES.get(req.gameId)
    if not g:
        raise HTTPException(404, "Unknown gameId")
    if g.ai_mode != "self-play":
        raise HTTPException(400, "Game is not in self-play mode")

    steps = max(1, min(200, req.steps))
    for _ in range(steps):
        if g.board.is_game_over():
            break
        params = g.params_white if g.board.turn else g.params_black
        with httpx.Client(timeout=30) as client:
            r = client.post(f"{ENGINE_URL}/bestmove", json={
                "fen": g.board.fen(),
                "params": params.model_dump(),
            })
            r.raise_for_status()
            move = r.json().get("move")
        if not move:
            break
        move_and_return(g, req.gameId, move)
    return state(req.gameId)

@app.post("/tune")
def tune(gameId: str, side: str, params: EngineParams):
    g = GAMES.get(gameId)
    if not g:
        raise HTTPException(404, "Unknown gameId")
    if side not in ("white", "black"):
        raise HTTPException(400, "side must be 'white' or 'black'")
    if side == "white":
        g.params_white = params
    else:
        g.params_black = params
    return state(gameId)

# --- helpers ---

def move_and_return(g: Game, gid: str, uci: str):
    m = chess.Move.from_uci(uci)
    if m not in g.board.legal_moves:
        raise HTTPException(500, "Engine suggested illegal move")
    san = g.board.san(m)
    g.board.push(m)
    g.moves_san.append(san)
    g.last_move = uci
    return state(gid)

def _result(board: chess.Board):
    if not board.is_game_over():
        return None
    outcome = board.outcome()
    if outcome.winner is True:
        return "1-0"
    if outcome.winner is False:
        return "0-1"
    return "1/2-1/2"