const BASE = import.meta.env.VITE_API_BASE || '/api'

export async function newGame(mode: 'HUMAN_VS_AI' | 'AI_VS_AI') {
  const r = await fetch(`${BASE}/games`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json() as Promise<{ gameId: string, fen: string, turn: 'w'|'b', over: boolean, result?: string, legalMoves: string[] }>
}

export async function getState(gameId: string) {
  if (!gameId || gameId === 'undefined' || gameId === 'null') {
    throw new Error('Missing gameId')
  }
  const r = await fetch(`${BASE}/games/${gameId}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function humanMove(gameId: string, uci: string) {
  const r = await fetch(`${BASE}/games/${gameId}/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uci }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function aiThink(gameId: string, movetimeMs: number) {
  const sseBase = import.meta.env.VITE_EVENTS_BASE || '/events'
  return new EventSource(`${sseBase}/games/${gameId}/ai/think?movetimeMs=${movetimeMs}`)
}

export async function aiMove(gameId: string, movetimeMs: number) {
  const r = await fetch(`${BASE}/games/${gameId}/ai/move?movetimeMs=${movetimeMs}`, { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function selfPlayStart(gameId: string, whiteMs: number, blackMs: number) {
  const sseBase = import.meta.env.VITE_EVENTS_BASE || '/events'
  return new EventSource(`${sseBase}/games/${gameId}/selfplay/start?whiteMs=${whiteMs}&blackMs=${blackMs}`)
}

export async function selfPlayStop(gameId: string) {
  await fetch(`${BASE}/games/${gameId}/selfplay/stop`, { method: 'POST' })
}

export async function selfPlayStep(gameId: string, movetimeMs: number) {
  const r = await fetch(`${BASE}/games/${gameId}/selfplay/step?movetimeMs=${movetimeMs}`, { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
