// Path: frontend/src/App.tsx
// Uses react-chessboard's native promotion flow via onPromotionCheck/onPromotionPieceSelect.
// Ensures promotion is always a single lowercase letter 'q'|'r'|'b'|'n' before posting to backend.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Box, Button, Card, CardContent, Divider, FormControl, Grid, InputLabel,
  MenuItem, Select, TextField, Typography
} from '@mui/material'
import { Chess } from 'chess.js'
import { Chessboard } from 'react-chessboard'
import {
  newGame, getState, postMove, think, selfPlayStart, engineStop,
  decodeUCIMove, type GameState
} from './api'

type Mode = 'HUMAN_VS_HUMAN' | 'HUMAN_VS_AI' | 'AI_VS_AI'

export default function App() {
  const [mode, setMode] = useState<Mode>('HUMAN_VS_AI')
  const [gameId, setGameId] = useState<string>('')
  const [state, setState] = useState<GameState>({
    gameId: '', fen: new Chess().fen(), turn: 'w', over: false, legalMoves: []
  })
  const [isSelfPlaying, setIsSelfPlaying] = useState(false)
  const [hasEverSelfPlayed, setHasEverSelfPlayed] = useState(false)
  const [pendingThink, setPendingThink] = useState(false)

  const [whiteDepth, setWhiteDepth] = useState<number>(6)
  const [whiteRollouts, setWhiteRollouts] = useState<number>(150)
  const [blackDepth, setBlackDepth] = useState<number>(6)
  const [blackRollouts, setBlackRollouts] = useState<number>(150)

  const esRef = useRef<EventSource | null>(null)
  const openStream = useCallback((es: EventSource) => {
    // eslint-disable-next-line no-console
    console.debug('[UI] openStream (closing previous?)', !!esRef.current)
    if (esRef.current) esRef.current.close()
    esRef.current = es
  }, [])
  const closeStream = useCallback(() => {
    if (esRef.current) {
      // eslint-disable-next-line no-console
      console.debug('[UI] closeStream')
      esRef.current.close()
      esRef.current = null
    }
  }, [])

  const isHumanVsHuman = mode === 'HUMAN_VS_HUMAN'
  const isHumanVsAI = mode === 'HUMAN_VS_AI'
  const isAIVsAI = mode === 'AI_VS_AI'

  const canStart = isAIVsAI && !!gameId && !state.over && !isSelfPlaying
  const canPauseResume = isAIVsAI && !!gameId && !state.over && (isSelfPlaying || hasEverSelfPlayed)

  useEffect(() => () => closeStream(), [closeStream])

  useEffect(() => {
    closeStream()
    setIsSelfPlaying(false)
    setHasEverSelfPlayed(false)
    setPendingThink(false)
    // eslint-disable-next-line no-console
    console.debug('[UI] mode changed', { mode })
  }, [mode, closeStream])

  useEffect(() => {
    if (!gameId) return
    // eslint-disable-next-line no-console
    console.debug('[UI] getState on gameId change', gameId)
    getState(gameId).then(s => {
      // eslint-disable-next-line no-console
      console.debug('[UI] getState result', s)
      setState(s)
    }).catch(e => console.error('[UI] getState failed', e))
  }, [gameId])

  // Bootstrap
  useEffect(() => {
    let cancelled = false
    async function ensureGame() {
      try {
        const saved = localStorage.getItem('lastGameId') || ''
        // eslint-disable-next-line no-console
        console.debug('[UI] bootstrap: saved lastGameId', saved)
        if (saved) {
          try {
            const gs = await getState(saved)
            if (!cancelled) {
              // eslint-disable-next-line no-console
              console.debug('[UI] bootstrap: resumed game', gs.gameId)
              setGameId(gs.gameId)
              setState(gs)
              return
            }
          } catch (e) {
            console.warn('[UI] bootstrap: stale lastGameId', saved, e)
          }
        }
        const fresh = await newGame(mode)
        if (!cancelled) {
          // eslint-disable-next-line no-console
          console.debug('[UI] bootstrap: created fresh game', fresh.gameId)
          setGameId(fresh.gameId)
          setState(fresh)
          localStorage.setItem('lastGameId', fresh.gameId)
        }
      } catch (e) {
        console.error('bootstrap: failed to ensure game', e)
      }
    }
    if (!gameId) ensureGame()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleNewGame = useCallback(async () => {
    try {
      // eslint-disable-next-line no-console
      console.debug('[UI] New Game click', { mode })
      closeStream()
      setIsSelfPlaying(false)
      setHasEverSelfPlayed(false)
      setPendingThink(false)
      const gs = await newGame(mode)
      // eslint-disable-next-line no-console
      console.debug('[UI] New Game created', gs.gameId)
      setGameId(gs.gameId)
      setState(gs)
      localStorage.setItem('lastGameId', gs.gameId)
    } catch (e) {
      console.error(e)
      alert('Failed to start a new game. Check backend.')
    }
  }, [mode, closeStream])

  const handleStart = useCallback(async () => {
    if (!gameId || !isAIVsAI || state.over || isSelfPlaying) return
    // eslint-disable-next-line no-console
    console.debug('[UI] Start selfplay', {
      gameId, fen: state.fen,
      whiteDepth, whiteRollouts, blackDepth, blackRollouts
    })
    const es = selfPlayStart(
      state.fen,
      whiteDepth, whiteRollouts,
      blackDepth, blackRollouts
    )
    es.onopen = () => console.debug('[ENGINE/selfplay] open')
    es.onmessage = async (e) => {
      // eslint-disable-next-line no-console
      console.debug('[ENGINE/selfplay] onmessage raw', e.data)
      try {
        const msg = JSON.parse(e.data)
        // eslint-disable-next-line no-console
        console.debug('[ENGINE/selfplay] parsed', msg)
        if (msg.type === 'bestmove' && typeof msg.move === 'string') {
          const { from, to, promotion } = decodeUCIMove(msg.move)
          // eslint-disable-next-line no-console
          console.debug('[ENGINE/selfplay] bestmove → postMove', { gameId, from, to, promotion })
          const next = await postMove(gameId, from, to, promotion)
          // eslint-disable-next-line no-console
          console.debug('[ENGINE/selfplay] postMove OK', next.fen)
          setState(next)
          localStorage.setItem('lastGameId', next.gameId)
        } else if (msg.type === 'done') {
          console.debug('[ENGINE/selfplay] done')
          setIsSelfPlaying(false)
          closeStream()
        } else {
          console.debug('[ENGINE/selfplay] info/other', msg)
        }
      } catch (err) {
        console.error('[ENGINE/selfplay] parse/apply failed', err, e.data)
      }
    }
    es.onerror = (err) => {
      console.error('[ENGINE/selfplay] error', err)
      setIsSelfPlaying(false)
      closeStream()
    }
    openStream(es)
    setIsSelfPlaying(true)
    setHasEverSelfPlayed(true)
  }, [
    gameId, isAIVsAI, state.over, isSelfPlaying, state.fen,
    whiteDepth, whiteRollouts, blackDepth, blackRollouts,
    openStream, closeStream
  ])

  const handlePauseResume = useCallback(async () => {
    if (!gameId || !isAIVsAI || state.over) return
    if (isSelfPlaying) {
      console.debug('[UI] Pause (engineStop)')
      try { await engineStop() } finally {
        setIsSelfPlaying(false)
        closeStream()
      }
    } else {
      console.debug('[UI] Resume (start)')
      handleStart()
    }
  }, [gameId, isAIVsAI, state.over, isSelfPlaying, handleStart, closeStream])

  // Utility: is this move a pawn promotion in the current FEN?
  function isPromotion(fen: string, from: string, to: string, piece?: string): boolean {
    try {
      const pos = new Chess(fen)
      const p = pos.get(from as any)
      if (!p || p.type !== 'p') return false
      const rankTo = to[1]
      if (p.color === 'w' && rankTo === '8') return true
      if (p.color === 'b' && rankTo === '1') return true
      return false
    } catch {
      return false
    }
  }

  // Normalize promotion tokens from the board ('q','Q','wQ','queen', etc.) → 'q'|'r'|'b'|'n'
  function normalizePromotion(input: unknown): 'q'|'r'|'b'|'n'|undefined {
    const raw = String(input ?? '').trim().toLowerCase()
    if (!raw) return undefined
    const table: Record<string, 'q'|'r'|'b'|'n'> = {
      q: 'q', queen: 'q', wq: 'q', bq: 'q',
      r: 'r', rook:  'r', wr: 'r', br: 'r',
      b: 'b', bishop:'b', wb: 'b', bb: 'b',
      n: 'n', knight:'n', wn: 'n', bn: 'n',
    }
    if (table[raw]) return table[raw]
    const c = raw[0]
    return (c === 'q' || c === 'r' || c === 'b' || c === 'n') ? c : undefined
  }

  // HUMAN drop (non-promotion): apply immediately
  const onPieceDrop = useCallback(async (source: string, target: string) => {
    console.debug('[UI] onPieceDrop', { source, target, gameId, mode, turn: state.turn })
    if (!gameId || state.over) return false
    if (isAIVsAI) return false
    if (isHumanVsAI && state.turn !== 'w') return false

    // If this will be a promotion, let the board handle via its picker.
    if (isPromotion(state.fen, source, target)) {
      console.debug('[UI] promotion detected → board will show picker')
      return false
    }

    try {
      console.debug('[UI] postMove (human)', { source, target })
      const next = await postMove(gameId, source, target)
      console.debug('[UI] postMove OK (human)', next.fen)
      setState(next)
      localStorage.setItem('lastGameId', next.gameId)

      if (isHumanVsAI && !next.over) {
        if (pendingThink) { console.debug('[UI] think suppressed: pending'); return true }
        setPendingThink(true)
        console.debug('[ENGINE/think] start', {
          fen: next.fen, side: 'black', depth: blackDepth, rollouts: blackRollouts
        })
        const es = think(next.fen, 'black', blackDepth, blackRollouts)
        es.onopen = () => console.debug('[ENGINE/think] open')
        es.onmessage = async (e) => {
          console.debug('[ENGINE/think] onmessage raw', e.data)
          try {
            const msg = JSON.parse(e.data)
            console.debug('[ENGINE/think] parsed', msg)
            if (msg.type === 'bestmove' && typeof msg.move === 'string') {
              const { from, to, promotion } = decodeUCIMove(msg.move)
              console.debug('[ENGINE/think] bestmove → postMove', { gameId, from, to, promotion })
              const after = await postMove(gameId, from, to, promotion)
              console.debug('[ENGINE/think] postMove OK', after.fen)
              setState(after)
              localStorage.setItem('lastGameId', after.gameId)
            } else if (msg.type === 'done') {
              console.debug('[ENGINE/think] done')
              setPendingThink(false)
              closeStream()
            } else {
              console.debug('[ENGINE/think] info/other', msg)
            }
          } catch (err) {
            console.error('[ENGINE/think] parse/apply failed', err, e.data)
          }
        }
        es.onerror = (err) => {
          console.error('[ENGINE/think] error', err)
          setPendingThink(false)
          closeStream()
        }
        openStream(es)
      }
      return true
    } catch (e) {
      console.error('[UI] onPieceDrop failed', e)
      return false
    }
  }, [
    gameId, state.over, isAIVsAI, isHumanVsAI, state.turn, state.fen,
    pendingThink, blackDepth, blackRollouts, openStream, closeStream
  ])

  // Let the board decide if a drop should trigger promotion UI.
  const onPromotionCheck = useCallback((from: string, to: string, piece?: string) => {
    const need = isPromotion(state.fen, from, to, piece)
    console.debug('[UI] onPromotionCheck', { from, to, piece, need })
    return need
  }, [state.fen])

  // Called when the user picks Queen/Rook/Bishop/Knight in the board’s UI.
  const onPromotionPieceSelect = useCallback(async (piece: string, from: string, to: string) => {
    if (!gameId) return false
    const normalized = normalizePromotion(piece) ?? 'q'
    console.debug('[UI] onPromotionPieceSelect', { pieceRaw: piece, normalized, from, to })

    try {
      const next = await postMove(gameId, from, to, normalized)
      console.debug('[UI] postMove OK (promotion)', next.fen)
      setState(next)
      localStorage.setItem('lastGameId', next.gameId)

      if (isHumanVsAI && !next.over) {
        if (!pendingThink) {
          setPendingThink(true)
          console.debug('[ENGINE/think] start (after promotion)', {
            fen: next.fen, side: 'black', depth: blackDepth, rollouts: blackRollouts
          })
          const es = think(next.fen, 'black', blackDepth, blackRollouts)
          es.onopen = () => console.debug('[ENGINE/think] open')
          es.onmessage = async (e) => {
            console.debug('[ENGINE/think] onmessage raw', e.data)
            try {
              const msg = JSON.parse(e.data)
              console.debug('[ENGINE/think] parsed', msg)
              if (msg.type === 'bestmove' && typeof msg.move === 'string') {
                const { from: ef, to: et, promotion: ep } = decodeUCIMove(msg.move)
                console.debug('[ENGINE/think] bestmove → postMove', { gameId, ef, et, ep })
                const after = await postMove(gameId, ef, et, ep)
                console.debug('[ENGINE/think] postMove OK', after.fen)
                setState(after)
                localStorage.setItem('lastGameId', after.gameId)
              } else if (msg.type === 'done') {
                console.debug('[ENGINE/think] done')
                setPendingThink(false)
                closeStream()
              }
            } catch (err) {
              console.error('[ENGINE/think] parse/apply failed', err, e.data)
            }
          }
          es.onerror = (err) => {
            console.error('[ENGINE/think] error', err)
            setPendingThink(false)
            closeStream()
          }
          openStream(es)
        } else {
          console.debug('[UI] think suppressed: pending (after promotion)')
        }
      }
      // tell the board to accept the drop
      return true
    } catch (e) {
      console.error('[UI] promotion apply failed', e)
      // tell the board to cancel the drop
      return false
    }
  }, [
    gameId, isHumanVsAI, pendingThink,
    blackDepth, blackRollouts, openStream, closeStream
  ])

  const boardOrientation = useMemo<'white' | 'black'>(() => 'white', [])
  const onNum = (setter: (n: number) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseInt(e.target.value || '0', 10)
    if (Number.isNaN(v)) return
    setter(Math.max(1, v))
  }

  // ────────────────────────────────────────────────────────────────────────────
  // UI MARKUP (visuals unchanged)
  // ────────────────────────────────────────────────────────────────────────────

  return (
    <Box p={2}>
      <Grid container spacing={2}>
        {/* Board column */}
        <Grid item xs={12} md={5} lg={6}>
          <Card>
            <CardContent sx={{ p: 1.5 }}>
              <Chessboard
                id="board"
                position={state.fen}
                boardWidth={420}
                customBoardStyle={{ borderRadius: 8 }}
                boardOrientation={boardOrientation}
                arePiecesDraggable={
                  !isAIVsAI && !state.over && !(isHumanVsAI && state.turn !== 'w')
                }
                onPieceDrop={onPieceDrop}
                /* Promotion flow (let the board handle the dialog) */
                onPromotionCheck={onPromotionCheck}
                onPromotionPieceSelect={onPromotionPieceSelect}
              />
              <Box mt={1.5}>
                <Typography variant="body2" color="text.secondary">
                  Game: {gameId || '-'}
                  {' • '}Turn: {state.turn}
                  {' • '}Over: {String(state.over)}
                  {state.result ? ` (${state.result})` : ''}
                </Typography>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        {/* Settings + controls column */}
        <Grid item xs={12} md={7} lg={6}>
          <Card>
            <CardContent>
              <Grid container spacing={2}>
                <Grid item xs={12}>
                  <FormControl size="small" fullWidth>
                    <InputLabel id="mode-label">Mode</InputLabel>
                    <Select
                      labelId="mode-label" label="Mode" value={mode}
                      onChange={(e) => setMode(e.target.value as Mode)}
                    >
                      <MenuItem value="HUMAN_VS_HUMAN">Human vs Human</MenuItem>
                      <MenuItem value="HUMAN_VS_AI">Human vs AI</MenuItem>
                      <MenuItem value="AI_VS_AI">AI vs AI</MenuItem>
                    </Select>
                  </FormControl>
                </Grid>

                <Grid item xs={12}>
                  <Grid container spacing={1}>
                    <Grid item xs={12} sm={4}>
                      <Button fullWidth onClick={handleStart} disabled={!canStart}>
                        Start
                      </Button>
                    </Grid>
                    <Grid item xs={12} sm={4}>
                      <Button fullWidth onClick={handlePauseResume} disabled={!canPauseResume}>
                        {isSelfPlaying ? 'Pause' : 'Resume'}
                      </Button>
                    </Grid>
                    <Grid item xs={12} sm={4}>
                      <Button fullWidth color="secondary" onClick={handleNewGame}>
                        New Game
                      </Button>
                    </Grid>
                  </Grid>
                </Grid>

                {(isHumanVsAI || isAIVsAI) && (
                  <>
                    <Grid item xs={12}>
                      <Divider />
                    </Grid>

                    {isHumanVsAI && (
                      <Grid item xs={12}>
                        <Typography variant="subtitle2" sx={{ mb: 1 }}>
                          AI (Black)
                        </Typography>
                        <Grid container spacing={1.5}>
                          <Grid item xs={6}>
                            <TextField
                              label="Depth"
                              type="number"
                              size="small"
                              fullWidth
                              inputProps={{ min: 1 }}
                              value={blackDepth}
                              onChange={onNum(setBlackDepth)}
                            />
                          </Grid>
                          <Grid item xs={6}>
                            <TextField
                              label="Rollouts"
                              type="number"
                              size="small"
                              fullWidth
                              inputProps={{ min: 1 }}
                              value={blackRollouts}
                              onChange={onNum(setBlackRollouts)}
                            />
                          </Grid>
                        </Grid>
                      </Grid>
                    )}

                    {isAIVsAI && (
                      <>
                        <Grid item xs={12}>
                          <Typography variant="subtitle2" sx={{ mb: 1 }}>
                            White AI
                          </Typography>
                          <Grid container spacing={1.5}>
                            <Grid item xs={6}>
                              <TextField
                                label="Depth"
                                type="number"
                                size="small"
                                fullWidth
                                inputProps={{ min: 1 }}
                                value={whiteDepth}
                                onChange={onNum(setWhiteDepth)}
                              />
                            </Grid>
                            <Grid item xs={6}>
                              <TextField
                                label="Rollouts"
                                type="number"
                                size="small"
                                fullWidth
                                inputProps={{ min: 1 }}
                                value={whiteRollouts}
                                onChange={onNum(setWhiteRollouts)}
                              />
                            </Grid>
                          </Grid>
                        </Grid>

                        <Grid item xs={12}>
                          <Typography variant="subtitle2" sx={{ mb: 1, mt: 1 }}>
                            Black AI
                          </Typography>
                          <Grid container spacing={1.5}>
                            <Grid item xs={6}>
                              <TextField
                                label="Depth"
                                type="number"
                                size="small"
                                fullWidth
                                inputProps={{ min: 1 }}
                                value={blackDepth}
                                onChange={onNum(setBlackDepth)}
                              />
                            </Grid>
                            <Grid item xs={6}>
                              <TextField
                                label="Rollouts"
                                type="number"
                                size="small"
                                fullWidth
                                inputProps={{ min: 1 }}
                                value={blackRollouts}
                                onChange={onNum(setBlackRollouts)}
                              />
                            </Grid>
                          </Grid>
                        </Grid>
                      </>
                    )}
                  </>
                )}

                <Grid item xs={12}>
                  <Typography variant="caption" color="text.secondary">
                    Start/Pause are enabled only in AI vs AI. In Human vs AI, AI plays as Black using the settings above.
                  </Typography>
                </Grid>
              </Grid>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  )
}