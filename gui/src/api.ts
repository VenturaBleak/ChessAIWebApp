const BASE = (import.meta as any).env?.VITE_GAME_URL || window.location.origin.replace(/:\d+$/, ':8000')

export async function newGame(body: any){
  const r = await fetch(`${BASE}/new`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) })
  return r.json()
}
export async function getState(gameId: string){
  const r = await fetch(`${BASE}/state?gameId=${gameId}`)
  return r.json()
}
export async function postMove(gameId: string, move: string){
  const r = await fetch(`${BASE}/move`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({gameId, move}) })
  return r.json()
}
export async function aiMove(gameId: string){
  const r = await fetch(`${BASE}/ai_move`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({gameId}) })
  return r.json()
}
export async function selfPlayStep(gameId: string, steps=1){
  const r = await fetch(`${BASE}/selfplay_step`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({gameId, steps}) })
  return r.json()
}
export async function tune(gameId: string, side: 'white'|'black', params: any){
  const u = new URL(`${BASE}/tune`)
  u.searchParams.set('gameId', gameId)
  u.searchParams.set('side', side)
  const r = await fetch(u.toString(), { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(params) })
  return r.json()
}