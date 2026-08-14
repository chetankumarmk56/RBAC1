import type { ChatMessage as Message } from '../types'
import Markdown from './Markdown'

export default function ChatMessage({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className="turn turn-user">
        <div className="user-bubble">{message.text}</div>
      </div>
    )
  }

  const trace = message.trace

  return (
    <div className="turn turn-assistant">
      <div className="assistant-mark" aria-hidden="true">
        R
      </div>

      <div className="assistant-body">
        <div className={`answer${message.failed ? ' answer-failed' : ''}`}>
          <Markdown text={message.text} />
        </div>

        {trace && (
          <div className="chips">
            {trace.decision && (
              <span className={`chip chip-${trace.decision.toLowerCase()}`}>
                {trace.decision === 'ALLOWED' ? 'Allowed' : 'Denied'}
              </span>
            )}
            {trace.agent && <span className="chip">{trace.agent.replace(/_/g, ' ')}</span>}
            {trace.tool && <span className="chip chip-mono">{trace.tool}()</span>}
            {trace.required_permission && (
              <span className="chip chip-mono">{trace.required_permission}</span>
            )}
            {trace.row_count !== null && trace.row_count !== undefined && (
              <span className="chip">
                {trace.row_count} row{trace.row_count === 1 ? '' : 's'}
              </span>
            )}
            {trace.provider && (
              <span
                className={`chip${trace.provider.includes('gemini') ? ' chip-fallback' : ''}`}
                title={
                  trace.provider.includes('gemini')
                    ? 'Claude was unavailable, so the Gemini fallback answered.'
                    : 'Answered by the primary provider.'
                }
              >
                {trace.provider}
              </span>
            )}
          </div>
        )}

        {trace?.scope_note && <p className="scope-note">{trace.scope_note}</p>}
      </div>
    </div>
  )
}
