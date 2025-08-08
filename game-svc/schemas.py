from typing import Optional, Literal
from pydantic import BaseModel, Field, conint, confloat

Depth = conint(ge=1, le=8)
Weight = confloat(ge=0.0, le=5.0)
Steps = conint(ge=1, le=200)

class EngineParams(BaseModel):
    depth: Depth = 3
    quiescence: bool = True
    move_ordering: bool = True
    w_material: Weight = 1.0
    w_mobility: Weight = 0.1
    w_pst: Weight = 0.2
    w_king_safety: Weight = 0.1

    model_config = {"extra": "forbid"}

# Lock these down with Literals/Enums to prevent typos
AiMode = Literal["human-vs-ai", "self-play"]
AiPlays = Literal["white", "black", "both"]

class NewGameRequest(BaseModel):
    fen: Optional[str] = Field(None, description="Starting FEN; default is standard initial position")
    ai_mode: AiMode = "human-vs-ai"
    ai_plays: AiPlays = "black"
    params_white: Optional[EngineParams] = None
    params_black: Optional[EngineParams] = None

    model_config = {"extra": "forbid"}

class MoveRequest(BaseModel):
    gameId: str
    move: str  # UCI

    model_config = {"extra": "forbid"}

class AiMoveRequest(BaseModel):
    gameId: str
    model_config = {"extra": "forbid"}

class SelfPlayStepRequest(BaseModel):
    gameId: str
    steps: Steps = 1

    model_config = {"extra": "forbid"}