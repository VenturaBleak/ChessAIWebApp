import React from 'react'

export default function Controls({
  mode, setMode, aiPlays, setAiPlays, onNew, onSelfPlayToggle, running
}:{
  mode: 'human-vs-ai'|'self-play', setMode:(m:'human-vs-ai'|'self-play')=>void,
  aiPlays: 'white'|'black'|'both', setAiPlays:(s:'white'|'black'|'both')=>void,
  onNew: ()=>void, onSelfPlayToggle: ()=>void, running: boolean
}){
  return (
    <div style={{display:'flex', gap:12, alignItems:'center'}}>
      <select value={mode} onChange={e=>setMode(e.target.value as any)}>
        <option value="human-vs-ai">Human vs AI</option>
        <option value="self-play">Self-Play</option>
      </select>
      <label>AI plays
        <select value={aiPlays} onChange={e=>setAiPlays(e.target.value as any)}>
          <option value="white">White</option>
          <option value="black">Black</option>
          <option value="both">Both (Self-Play)</option>
        </select>
      </label>
      <button onClick={onNew}>New Game</button>
      {mode==='self-play' && (
        <button onClick={onSelfPlayToggle}>{running ? 'Pause' : 'Run'}</button>
      )}
    </div>
  )
}