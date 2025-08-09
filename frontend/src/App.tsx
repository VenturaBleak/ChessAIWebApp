// Path: frontend/src/App.tsx
/**
 * Tile-based chess UI
 * - Modes: HUMAN_VS_HUMAN, HUMAN_VS_AI, AI_VS_AI.
 * - Single "Black AI ms" control is used in BOTH HUMAN_VS_AI and AI_VS_AI.
 * - "White AI ms" is only for AI_VS_AI.
 * - In HUMAN_VS_HUMAN: Black AI ms is present but disabled (greyed).
 * - Pause/Resume (fixed-size) pauses AI turns only.
 * - Stream panel sits under the controls grid (same middle column).
 * - Disabled controls are greyed out; tiles reflect disabled state.
 * - Type-only import for chess Move to avoid runtime import errors.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Box, Button, Card, CardContent, FormControl, InputLabel, MenuItem,
  Select, Stack, TextField, Typography
} from '@mui/material'
import { Chess } from 'chess.js'
import type { Move } from 'chess.js'
import { Chessboard } from 'react-chessboard'
import type { GameMode, GameState } from './types'
import { useEventSource } from './hooks/useEventSource'

type Side = 'white' | 'black'

function computeState(game: Chess): GameState {
  const over = game.isGameOver()
  let result: GameState['result'] | undefined
  if (over) {
    if (game.isCheckmate()) result = game.turn() === 'w' ? '0-1' : '1-0'
    else if (game.isDraw() || game.isStalemate() || game.isThreefoldRepetition() || game.isInsufficientMaterial())
      result = '1/2-1/2'
  }
  return {
    fen: game.fen(),
    turn: game.turn(),
    over,
    result,
    legalMoves: (game.moves({ verbose: true }) as any[]).map((m: any) => `${m.from}${m.to}${m.promotion ?? ''}`)
  }
}

function pickAiMove(game: Chess): Move | null {
  const legal = game.moves({ verbose: true }) as Move[]
  if (!legal.length) return null
  const val: Record<string, number> = { p:1,n:3,b:3,r:5,q:9,k:0 }
  let best: Move[] = []
  let score = -Infinity
  for (const m of legal) {
    const s = (m as any).captured ? val[(m as any).captured] ?? 0 : 0
    if (s > score) { score = s; best = [m] }
    else if (s === score) best.push(m)
  }
  const pool = best.length ? best : legal
  return pool[Math.floor(Math.random() * pool.length)]
}

export default function App() {
  const [mode, setMode] = useState<GameMode>('HUMAN_VS_HUMAN')
  const [whiteMs, setWhiteMs] = useState(350)
  const [blackMs, setBlackMs] = useState(350)
  const [started, setStarted] = useState(false)
  const [paused, setPaused] = useState(false)

  const gameRef = useRef(new Chess())
  const [state, setState] = useState<GameState>(() => computeState(gameRef.current))

  // AI timer
  const aiTimer = useRef<number | null>(null)
  const clearTimer = useCallback(() => {
    if (aiTimer.current !== null) {
      window.clearTimeout(aiTimer.current)
      aiTimer.current = null
    }
  }, [])
  const resetGame = useCallback(() => {
    clearTimer()
    gameRef.current = new Chess()
    setState(computeState(gameRef.current))
  }, [clearTimer])

  // --- SSE (optional) ---
  const [sseLines, setSseLines] = useState<string[]>([])
  const [sseConnected, setSseConnected] = useState(false)
  const sseUrl = import.meta.env.VITE_DEMO_SSE_URL as string | undefined
  const { connect: sseConnect, close: sseClose } = useEventSource(
    (e) => {
      try {
        const data = JSON.parse(e.data)
        setSseLines(prev => [...prev.slice(-500), JSON.stringify(data)])
      } catch {
        setSseLines(prev => [...prev.slice(-500), e.data])
      }
    },
    () => setSseConnected(false)
  )
  useEffect(() => {
    if (!started || !sseUrl) {
      setSseConnected(false)
      sseClose()
      return
    }
    const es = sseConnect(sseUrl)
    setSseConnected(true)
    return () => { es?.close?.(); setSseConnected(false) }
  }, [started, sseUrl, sseConnect, sseClose])

  // --- Derived flags ---
  const isHvH = mode === 'HUMAN_VS_HUMAN'
  const isHvAI = mode === 'HUMAN_VS_AI'
  const isAivAI = mode === 'AI_VS_AI'
  const anyAIActive = useMemo(() => (isHvAI || isAivAI), [isHvAI, isAivAI])

  // --- Actions ---
  const start = useCallback(() => {
    setStarted(true)
    setPaused(false)
  }, [])
  const restart = useCallback(() => {
    setStarted(false)
    setPaused(false)
    resetGame()
  }, [resetGame])

  // Schedule a single AI move for the side to move
  const scheduleAiMove = useCallback((delayMs: number) => {
    clearTimer()
    if (paused) return
    aiTimer.current = window.setTimeout(() => {
      if (paused) return
      const g = gameRef.current
      if (g.isGameOver()) return
      const mv = pickAiMove(g)
      if (!mv) return
      g.move(mv)
      setState(computeState(g))
    }, Math.max(0, delayMs | 0))
  }, [clearTimer, paused])

  const scheduleNextForMode = useCallback(() => {
    if (!started || state.over || paused) return
    const toMove: Side = state.turn === 'w' ? 'white' : 'black'
    if (isAivAI) {
      const delay = toMove === 'white' ? whiteMs : blackMs
      scheduleAiMove(delay)
      return
    }
    if (isHvAI && toMove === 'black') {
      scheduleAiMove(blackMs)
    }
  }, [started, state.over, paused, state.turn, isAivAI, isHvAI, whiteMs, blackMs, scheduleAiMove])

  const togglePause = useCallback(() => {
    if (!started || state.over || !anyAIActive) return
    if (paused) {
      setPaused(false)
      scheduleNextForMode()
    } else {
      setPaused(true)
      clearTimer()
    }
  }, [clearTimer, paused, scheduleNextForMode, started, state.over, anyAIActive])

  // After any state change, if AI should move, schedule it
  useEffect(() => { scheduleNextForMode() }, [scheduleNextForMode])

  // When unpausing, schedule next AI move
  useEffect(() => { if (!paused) scheduleNextForMode() }, [paused, scheduleNextForMode])

  // Clean up on unmount
  useEffect(() => () => clearTimer(), [clearTimer])

  // --- Board interactivity ---
  const isHumanTurn = useMemo(() => {
    if (!started || state.over) return false
    if (isAivAI) return false
    if (isHvAI) return state.turn === 'w' // human is White in HvAI
    // HvH
    return true
  }, [started, state.over, state.turn, isAivAI, isHvAI])

  const onPieceDrop = useCallback((from: string, to: string) => {
    if (!isHumanTurn) return false
    const g = gameRef.current
    const move = g.move({ from, to, promotion: 'q' })
    if (!move) return false
    setState(computeState(g))
    return true
  }, [isHumanTurn])

  const statusText = useMemo(() => {
    if (!started) return 'Click Start to begin.'
    if (state.over) return `Game over ${state.result ?? ''}`
    if (paused) return 'Paused'
    const turnText = state.turn === 'w' ? 'White' : 'Black'
    if (isHvAI) return `${turnText} to move (Black is AI)`
    if (isAivAI) return `${turnText} to move (AI vs AI)`
    return `${turnText} to move`
  }, [started, state.over, state.result, state.turn, paused, isHvAI, isAivAI])

  // --- JSX ---
  return (
    <Box p={2} className="app-shell" sx={{ bgcolor: 'background.default', color: 'text.primary' }}>
      {/* Board (left column) */}
      <Stack spacing={1} alignItems="center">
        <Chessboard
          id="board"
          position={state.fen}
          arePiecesDraggable={isHumanTurn}
          onPieceDrop={onPieceDrop}
          boardWidth={520}
          customBoardStyle={{ borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.1)' }}
        />
        <Typography variant="subtitle2">{statusText}</Typography>
      </Stack>

      {/* Middle column: Controls + Stream stacked */}
      <Box className="middle-col">
        {/* Controls */}
        <Card className="controls-card" sx={{ p: 2 }}>
          <CardContent>
            <Box className="tiles-grid">
              {/* Mode select */}
              <Box className="tile" data-disabled={String(started)}>
                <FormControl size="small" fullWidth>
                  <InputLabel id="mode-label">Mode</InputLabel>
                  <Select
                    labelId="mode-label"
                    label="Mode"
                    value={mode}
                    onChange={(e) => setMode(e.target.value as GameMode)}
                    disabled={started}
                  >
                    <MenuItem value="HUMAN_VS_HUMAN">Human vs Human</MenuItem>
                    <MenuItem value="HUMAN_VS_AI">Human vs AI</MenuItem>
                    <MenuItem value="AI_VS_AI">AI vs AI</MenuItem>
                  </Select>
                </FormControl>
              </Box>

              {/* White AI ms: only relevant in AI_VS_AI, and never disabled in that mode */}
              <Box className="tile" data-disabled={String(!isAivAI)}>
                <TextField
                  label="White AI ms"
                  type="number"
                  size="small"
                  fullWidth
                  value={whiteMs}
                  onChange={(e) => setWhiteMs(Math.max(0, Number(e.target.value || 0)))}
                  inputProps={{ min: 0, step: 50 }}
                  disabled={!isAivAI}
                />
              </Box>

              {/* Black AI ms: disabled (greyed) in HUMAN_VS_HUMAN; enabled in HUMAN_VS_AI and AI_VS_AI */}
              <Box className="tile" data-disabled={String(isHvH)}>
                <TextField
                  label="Black AI ms"
                  type="number"
                  size="small"
                  fullWidth
                  value={blackMs}
                  onChange={(e) => setBlackMs(Math.max(0, Number(e.target.value || 0)))}
                  inputProps={{ min: 0, step: 50 }}
                  disabled={isHvH}
                />
              </Box>

              <Box className="tile" data-disabled={String(started)}>
                <Button className="fixed-width" onClick={start} disabled={started} fullWidth>
                  Start
                </Button>
              </Box>

              <Box className="tile" data-disabled={String(!started)}>
                <Button className="fixed-width" variant="outlined" onClick={restart} disabled={!started} fullWidth>
                  Restart
                </Button>
              </Box>

              <Box className="tile" data-disabled={String(!started || state.over || !anyAIActive)}>
                <Button
                  className="fixed-width"
                  onClick={togglePause}
                  disabled={!started || state.over || !anyAIActive}
                  fullWidth
                >
                  {paused ? 'Resume' : 'Pause'}
                </Button>
              </Box>
            </Box>
          </CardContent>
        </Card>

        {/* Stream sits UNDER the buttons grid in the same middle column */}
        <Card className="stream-card" sx={{ p: 2 }}>
          <CardContent>
            <Typography variant="subtitle1" gutterBottom>Engine Stream</Typography>
            <Box className="button-row" sx={{ mb: 1 }}>
              <Typography variant="body2">Status:&nbsp;</Typography>
              <Typography variant="body2" className="mono">
                {sseConnected ? 'connected' : sseUrl ? 'connecting/idle' : 'disabled'}
              </Typography>
            </Box>
            <Box className="stream-log" id="stream-log">
              {sseLines.map((l, i) => (<div key={i}>{l}</div>))}
            </Box>
          </CardContent>
        </Card>
      </Box>

      {/* Right column: State */}
      <Card className="state-card" sx={{ p: 2, minWidth: 260 }}>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>State</Typography>
          <Box className="mono" sx={{ fontSize: 12 }}>
            <div>Started: {String(started)}</div>
            <div>Paused: {String(paused)}</div>
            <div>Mode: {mode}</div>
            <div>FEN: {state.fen}</div>
            <div>Turn: {state.turn}</div>
            <div>Over: {String(state.over)} {state.result ? `(${state.result})` : ''}</div>
            <div>Legal: {state.legalMoves.join(' ')}</div>
          </Box>
        </CardContent>
      </Card>
    </Box>
  )
}