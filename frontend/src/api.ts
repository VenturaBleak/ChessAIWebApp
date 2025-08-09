// Path: frontend/src/api.ts

/**
 * ──────────────────────────────────────────────────────────────────────────────
 * Frontend API Layer — Microservices Orchestration (UI as the Conductor)
 * ──────────────────────────────────────────────────────────────────────────────
 *
 * PURPOSE
 *   This file is the *single* integration point between the React UI and the
 *   backend microservices. It intentionally separates calls to:
 *
 *     1) Game Service  (authoritative game state: validate/apply moves)
 *     2) Engine Service (UCI engines: compute best moves; never writes state)
 *
 *   Per your requirement, the Engine Service and Game Service **never** talk to
 *   each other. The UI (this layer) orchestrates both:
 *
 *     - Human move:    UI → Game Service (apply move) → UI updates board.
 *     - AI move:       UI → Engine Service (get bestmove) → UI → Game Service
 *                      (apply bestmove) → UI updates board.
 *     - AI vs AI:      UI opens an Engine stream (bestmove events), and for
 *                      each bestmove the UI applies it to the Game Service,
 *                      then updates the board.
 *
 * GOVERNING LOGIC
 *   - The **Game Service is the single source of truth** for the chess game:
 *       FEN/PGN/move list/outcome live only there.
 *   - The **Engine Service is stateless w.r.t. game state**: it proposes moves
 *       via UCI, but never mutates game state. We pass it a FEN and settings.
 *   - The UI ensures strict sequencing:
 *       after receiving a bestmove from the Engine stream, we immediately post
 *       it to the Game Service and wait for the confirmed GameState before
 *       accepting/processsing the next engine move. This prevents races/drift.
 *
 * ENDPOINT CONTRACTS (suggested, match your backend)
 *   GAME SERVICE (HTTP):
 *     POST   {GAME_BASE}/games                       -> create game
 *     GET    {GAME_BASE}/games/{gameId}              -> fetch current state
 *     POST   {GAME_BASE}/games/{gameId}/move         -> apply move {from,to,promotion?}
 *
 *   ENGINE SERVICE (SSE + HTTP):
 *     GET    {ENGINE_EVENTS_BASE}/engines/think
 *            ?fen=&side=white|black&depth=&rollouts= -> one-shot think stream
 *            emits JSON events:
 *              { "type":"bestmove", "move":"e2e4" }  // UCI move
 *              { "type":"done" }
 *
 *     GET    {ENGINE_EVENTS_BASE}/engines/selfplay
 *            ?fen=&whiteDepth=&whiteRollouts=&blackDepth=&blackRollouts=
 *            emits JSON events:
 *              { "type":"bestmove", "side":"w"|"b", "move":"e7e5" }
 *              { "type":"done" }
 *
 *     POST   {ENGINE_API_BASE}/engines/stop          -> stops active stream
 *
 * ENV CONFIG (Vite):
 *   VITE_GAME_API_BASE        (default '/api')
 *   VITE_ENGINE_EVENTS_BASE   (default '/engine-events')
 *   VITE_ENGINE_API_BASE      (default '/engine')
 *
 * NOTES
 *   - Browsers do not support custom headers on EventSource; authentication
 *     should be cookie-based or via query params / token in URL if required.
 *   - We export small helpers (decodeUCIMove) so the UI can convert a UCI
 *     string like "e2e4" or "e7e8q" into {from,to,promotion}.
 */

// ──────────────────────────────────────────────────────────────────────────────
// Base URLs (configurable via Vite env)
// ──────────────────────────────────────────────────────────────────────────────
const GAME_BASE = import.meta.env.VITE_GAME_API_BASE || '/api'
const ENGINE_EVENTS_BASE = import.meta.env.VITE_ENGINE_EVENTS_BASE || '/engine-events'
const ENGINE_API_BASE = import.meta.env.VITE_ENGINE_API_BASE || '/engine'

// Log resolved bases once
// eslint-disable-next-line no-console
console.debug('[API] BASES', { GAME_BASE, ENGINE_EVENTS_BASE, ENGINE_API_BASE })

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────
export interface GameState {
  gameId: string
  fen: string
  turn: 'w' | 'b'
  over: boolean
  result?: '1-0' | '0-1' | '1/2-1/2'
  legalMoves: string[]
}

export type Side = 'white' | 'black'

export type EngineThinkEvent =
  | { type: 'bestmove'; move: string }   // UCI move, e.g. "e2e4" or "e7e8q"
  | { type: 'done' }
  | { type: 'info'; [k: string]: unknown } // optional extra info from engines

export type EngineSelfPlayEvent =
  | { type: 'bestmove'; side: 'w' | 'b'; move: string }
  | { type: 'done' }
  | { type: 'info'; [k: string]: unknown }

// ──────────────────────────────────────────────────────────────────────────────
// Utilities
// ──────────────────────────────────────────────────────────────────────────────

/**
 * Convert a UCI move string into a structured move the Game Service expects.
 * Examples:
 *   "e2e4"   -> from:"e2", to:"e4"
 *   "e7e8q"  -> from:"e7", to:"e8", promotion:"q"
 */

export function decodeUCIMove(uci: string): { from: string; to: string; promotion?: string } {
  const clean = (uci || '').trim().toLowerCase()
  if (clean.length < 4) throw new Error(`Invalid UCI move: "${uci}"`)
  const from = clean.slice(0, 2)
  const to = clean.slice(2, 4)
  const promotion = clean.length >= 5 ? clean.slice(4, 5) : undefined
  return { from, to, promotion }
}

/**
 * Open an EventSource and return it. Callers must close it when done.
 * Note: You cannot set custom headers on EventSource in browsers.
 */
function openSSE(url: string): EventSource {
  // eslint-disable-next-line no-console
  console.debug('[API] openSSE', url)
  return new EventSource(url)
}

// ──────────────────────────────────────────────────────────────────────────────
// Game Service — the authoritative state keeper
// ──────────────────────────────────────────────────────────────────────────────

/**
 * Create a new game with a specific mode.
 * UI → Game only. Engine is not involved here.
 */
export async function newGame(
  mode: 'HUMAN_VS_HUMAN' | 'HUMAN_VS_AI' | 'AI_VS_AI'
): Promise<GameState> {
  const url = `${GAME_BASE}/games`
  // eslint-disable-next-line no-console
  console.debug('[API] newGame →', { url, mode })
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  // eslint-disable-next-line no-console
  console.debug('[API] newGame ←', r.status)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  // eslint-disable-next-line no-console
  console.debug('[API] newGame data', data)
  return data
}

/**
 * Read current game state. Use this after any move or on page reloads.
 */

export async function getState(gameId: string): Promise<GameState> {
  const url = `${GAME_BASE}/games/${gameId}`
  // eslint-disable-next-line no-console
  console.debug('[API] getState →', url)
  const r = await fetch(url)
  // eslint-disable-next-line no-console
  console.debug('[API] getState ←', r.status)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  // eslint-disable-next-line no-console
  console.debug('[API] getState data', data)
  return data
}

/**
 * Apply a move to the game. This is the only way state changes.
 * - Human moves: UI calls this directly.
 * - AI moves:    UI calls this with the engine-proposed bestmove.
 */

export async function postMove(
  gameId: string,
  from: string,
  to: string,
  promotion?: string
): Promise<GameState> {
  const url = `${GAME_BASE}/games/${gameId}/move`
  const body = { from, to, promotion }
  // eslint-disable-next-line no-console
  console.debug('[API] postMove →', { url, body })
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  // eslint-disable-next-line no-console
  console.debug('[API] postMove ←', r.status)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  // eslint-disable-next-line no-console
  console.debug('[API] postMove data', data)
  return data
}

// ──────────────────────────────────────────────────────────────────────────────
// Engine Service — UCI-facing, returns move *proposals* only
// ──────────────────────────────────────────────────────────────────────────────

/**
 * Ask the engine for ONE bestmove from a given FEN and side to move.
 * Returns an EventSource that streams EngineThinkEvent:
 *   - {type:"bestmove", move:"g8f6"}  // UCI
 *   - {type:"done"}
 *
 * UI responsibility:
 *   - Close the EventSource when you receive "done" or on component cleanup.
 *   - When "bestmove" arrives, immediately call postMove(...) to the Game
 *     Service and wait for its GameState before proceeding.
 */
export function think(
  fen: string,
  side: Side,
  depth = 6,
  rollouts = 150
): EventSource {
  const qs = new URLSearchParams({
    fen,
    side,
    depth: String(depth),
    rollouts: String(rollouts),
  })
  const url = `${ENGINE_EVENTS_BASE}/engines/think?${qs.toString()}`
  // eslint-disable-next-line no-console
  console.debug('[API] think URL', url)
  return openSSE(url)
}

/**
 * Start a continuous self-play stream that alternates sides and emits a
 * sequence of bestmoves. The Engine Service NEVER pushes to the Game Service.
 * The UI must take each bestmove and immediately call postMove(...) to the
 * Game Service, then update the FEN used for subsequent reasoning if needed.
 */
export function selfPlayStart(
  fen: string,
  whiteDepth = 6,
  whiteRollouts = 150,
  blackDepth = 6,
  blackRollouts = 150
): EventSource {
  const qs = new URLSearchParams({
    fen,
    whiteDepth: String(whiteDepth),
    whiteRollouts: String(whiteRollouts),
    blackDepth: String(blackDepth),
    blackRollouts: String(blackRollouts),
  })
  const url = `${ENGINE_EVENTS_BASE}/engines/selfplay?${qs.toString()}`
  // eslint-disable-next-line no-console
  console.debug('[API] selfPlayStart URL', url)
  return openSSE(url)
}

/**
 * Stop any active engine stream for the current client/session.
 * Implementation detail on the server is up to you; this is a simple POST.
 */
export async function engineStop(): Promise<void> {
  const url = `${ENGINE_API_BASE}/engines/stop`
  // eslint-disable-next-line no-console
  console.debug('[API] engineStop →', url)
  const r = await fetch(url, { method: 'POST' })
  // eslint-disable-next-line no-console
  console.debug('[API] engineStop ←', r.status)
  if (!r.ok) throw new Error(await r.text())
}