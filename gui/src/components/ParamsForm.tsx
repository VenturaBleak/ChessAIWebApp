import React, { useState } from 'react'

type Params = {
  depth: number
  quiescence: boolean
  move_ordering: boolean
  w_material: number
  w_mobility: number
  w_pst: number
  w_king_safety: number
}

export default function ParamsForm({ initial, onChange }: { initial: Params, onChange: (p: Params)=>void }){
  const [p, setP] = useState<Params>(initial)
  function set<K extends keyof Params>(k: K, v: Params[K]){
    const next = { ...p, [k]: v }
    setP(next)
    onChange(next)
  }
  return (
    <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:8}}>
      <label>Depth <input type="number" min={1} max={6} value={p.depth} onChange={e=>set('depth', Number(e.target.value))} /></label>
      <label>Quiescence <input type="checkbox" checked={p.quiescence} onChange={e=>set('quiescence', e.target.checked)} /></label>
      <label>Move ordering <input type="checkbox" checked={p.move_ordering} onChange={e=>set('move_ordering', e.target.checked)} /></label>
      <label>Material <input type="number" step="0.1" value={p.w_material} onChange={e=>set('w_material', Number(e.target.value))} /></label>
      <label>Mobility <input type="number" step="0.1" value={p.w_mobility} onChange={e=>set('w_mobility', Number(e.target.value))} /></label>
      <label>PST <input type="number" step="0.1" value={p.w_pst} onChange={e=>set('w_pst', Number(e.target.value))} /></label>
      <label>King safety <input type="number" step="0.1" value={p.w_king_safety} onChange={e=>set('w_king_safety', Number(e.target.value))} /></label>
    </div>
  )
}