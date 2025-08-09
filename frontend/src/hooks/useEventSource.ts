
import { useRef, useCallback } from 'react'

export function useEventSource(onMessage: (e: MessageEvent) => void, onError?: (e: Event) => void) {
  const ref = useRef<EventSource | null>(null)

  const connect = useCallback((url: string) => {
    if (ref.current) ref.current.close()
    const es = new EventSource(url, { withCredentials: false })
    es.onmessage = onMessage
    es.onerror = (e) => {
      onError?.(e)
    }
    ref.current = es
    return es
  }, [onMessage, onError])

  const close = useCallback(() => {
    if (ref.current) {
      ref.current.close()
      ref.current = null
    }
  }, [])

  return { connect, close }
}
