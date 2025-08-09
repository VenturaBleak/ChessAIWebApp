// Path: frontend/src/types.ts
export type GameMode = 'HUMAN_VS_HUMAN' | 'HUMAN_VS_AI' | 'AI_VS_AI';

export interface GameState {
  fen: string;
  turn: 'w' | 'b';
  over: boolean;
  result?: '1-0' | '0-1' | '1/2-1/2';
  legalMoves: string[];
}