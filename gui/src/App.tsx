import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Chessboard } from 'react-chessboard'
import { Chess } from 'chess.js'
import MoveList from './components/MoveList'
import Controls from './components/Controls'
import ParamsForm from './components/ParamsForm'
import { newGame, getState, postMove, aiMove, selfPlayStep, tune } from './api'

type Side = 'white' | 'black'
type Mode = 'human-vs-ai' | 'self-play'
type AiPlays = 'white' | 'black' | 'both'

const DEFAULT_PARAMS = {
  depth: 3,
  quiescence: true,
  move_ordering: true,
  w_material: 1.0,
  w_mobility: 0.1,
  w_pst: 0.2,
  w_king_safety: 0.1
}

export default function App(){
  const [gameId, setGameId] = useState<string>('')        // current game id
  const [fen, setFen] = useState<string>('')              // current position
  const [turn, setTurn] = useState<Side>('white')         // side to move
  const [moves, setMoves] = useState<string[]>([])        // SAN list
  const [aiMode, setAiMode] = useState<Mode>('human-vs-ai')
  const [aiPlays, setAiPlays] = useState<AiPlays>('black')
  const [paramsW, setParamsW] = useState(DEFAULT_PARAMS)
  const [paramsB, setParamsB] = useState(DEFAULT_PARAMS)
  const [running, setRunning] = useState(false)

  const intervalRef = useRef<number | null>(null)

  async function bootstrap(){
    const data = await newGame({
      ai_mode: aiMode,
      ai_plays: aiPlays,
      params_white: paramsW,
      params_black: paramsB
    })
    setGameId(data.gameId)
    applyState(data)
  }

  function applyState(s: any){
    setFen(s.fen)
    setTurn(s.turn)
    setMoves(s.movesSAN)
  }

  useEffect(()=>{ bootstrap() }, []) // initial game

  async function onDrop(sourceSquare: string, targetSquare: string){
    if (!gameId) return false
    if (aiMode === 'self-play') return false

    const move = sourceSquare + targetSquare + maybePromotion(sourceSquare, targetSquare)
    const next = await postMove(gameId, move)
    applyState(next)

    const isAiTurn =
      next.aiMode === 'human-vs-ai' &&
      (next.aiPlays === 'both' ||
       (next.turn === 'black' && next.aiPlays === 'black') ||
       (next.turn === 'white' && next.aiPlays === 'white'))

    if (isAiTurn){
      const after = await aiMove(gameId)
      applyState(after)
    }
    return true
  }

  function maybePromotion(from: string, to: string){
    // auto-queen if a pawn reaches back rank
    if (fen){
      const c = new Chess(fen)
      const m = { from, to, promotion: 'q' as const }
      if (c.move(m)){
        const wasPawn = c.history({ verbose: true }).slice(-1)[0]?.piece === 'p'
        const toRank = to[1]
        if (wasPawn && (toRank === '1' || toRank === '8')) return 'q'
      }
    }
    return ''
  }

  async function onNew(){
    const data = await newGame({
      ai_mode: aiMode,
      ai_plays: aiPlays,
      params_white: paramsW,
      params_black: paramsB
    })
    setGameId(data.gameId)
    applyState(data)
  }

  async function onTune(side: 'white' | 'black', p: any){
    if (!gameId) return
    const s = await tune(gameId, side, p)
    applyState(s)
  }

  // Self-play loop: ensure a self-play game (ai_plays='both'),
  // capture its id locally for the interval closure, and tick.
  async function startLoop(){
    if (intervalRef.current) return

    let id = gameId

    if (aiMode !== 'self-play' || aiPlays !== 'both' || !id){
      setAiMode('self-play')
      setAiPlays('both')
      const data = await newGame({
        ai_mode: 'self-play',
        ai_plays: 'both',
        params_white: paramsW,
        params_black: paramsB
      })
      id = data.gameId
      setGameId(id)
      applyState(data)
    }

    setRunning(true)
    intervalRef.current = window.setInterval(async ()=>{
      const s = await selfPlayStep(id, 2) // two plies per tick
      applyState(s)
      if (s.result){ stopLoop() }
    }, 700)
  }

  function stopLoop(){
    setRunning(false)
    if (intervalRef.current){
      window.clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }

  // Cleanup interval if component unmounts / hot-reloads
  useEffect(()=>{
    return () => {
      if (intervalRef.current){
        window.clearInterval(intervalRef.current)
      }
    }
  }, [])

  const boardOrientation = useMemo(
    () => (aiMode === 'human-vs-ai' && aiPlays === 'black' ? 'white' : 'white'),
    [aiMode, aiPlays]
  )

  return (
    <div style={{display:'grid', gridTemplateColumns:'minmax(320px,520px) 1fr', gap:24, padding:24, fontFamily:'Inter, system-ui, sans-serif'}}>
      <div style={{display:'grid', gap:12}}>
        <Chessboard
          position={fen}
          onPieceDrop={onDrop}
          boardWidth={520}
          areArrowsAllowed
          boardOrientation={boardOrientation as any}
        />
        <MoveList moves={moves} />
      </div>
      <div style={{display:'grid', gap:16}}>
        <h2 style={{margin:0}}>Controls</h2>
        <Controls
          mode={aiMode}
          setMode={setAiMode}
          aiPlays={aiPlays}
          setAiPlays={setAiPlays}
          onNew={onNew}
          onSelfPlayToggle={() => running ? stopLoop() : startLoop()}
          running={running}
        />
        <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:16}}>
          <div>
            <h3 style={{margin:'8px 0'}}>White Engine</h3>
            <ParamsForm initial={paramsW} onChange={p => { setParamsW(p); onTune('white', p) }} />
          </div>
          <div>
            <h3 style={{margin:'8px 0'}}>Black Engine</h3>
            <ParamsForm initial={paramsB} onChange={p => { setParamsB(p); onTune('black', p) }} />
          </div>
        </div>
        <div style={{opacity:0.7}}>
          Turn: <b>{turn}</b> {fen && '•'} FEN: <code style={{userSelect:'all'}}>{fen}</code>
        </div>
      </div>
    </div>
  )
}