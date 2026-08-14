import { agentLabel, modelLabel, permissionLabel, providerLabel, toolLabel } from '../labels'
import type { ChatMessage as Message } from '../types'
import Markdown from './Markdown'

/** `base_salary` -> `base salary`, for the withheld-columns tooltip. */
const readable = (field: string) => field.replace(/_/g, ' ')

export default function ChatMessage({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className="turn turn-user">
        <div className="user-bubble">{message.text}</div>
      </div>
    )
  }

  const trace = message.trace
  // A chain ('claude-sonnet -> gemini') or a bare 'gemini' both mean a fallback ran.
  const fellBack = Boolean(trace?.model?.includes('->') || trace?.provider?.includes('gemini'))

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
            {trace.agent && (
              <span className="chip" title={trace.agent}>
                {agentLabel(trace.agent)}
              </span>
            )}
            {trace.tool && (
              <span className="chip" title={`${trace.tool}()`}>
                {toolLabel(trace.tool)}
              </span>
            )}
            {trace.required_permission && (
              <span className="chip" title={trace.required_permission}>
                {permissionLabel(trace.required_permission)}
              </span>
            )}
            {trace.row_count !== null && trace.row_count !== undefined && (
              <span className="chip">
                {trace.row_count} row{trace.row_count === 1 ? '' : 's'}
              </span>
            )}
            {trace.withheld_fields?.length > 0 && (
              <span
                className="chip chip-withheld"
                title={`Withheld from your role: ${trace.withheld_fields.map(readable).join(', ')}`}
              >
                {trace.withheld_fields.length} field
                {trace.withheld_fields.length === 1 ? '' : 's'} withheld
              </span>
            )}
            {/* The model that answered. Older stored traces only kept the provider. */}
            {(trace.model || trace.provider) && (
              <span
                className={`chip${fellBack ? ' chip-fallback' : ''}`}
                title={
                  fellBack
                    ? 'The first model failed, so the next one your role holds answered.'
                    : 'The model that answered this request.'
                }
              >
                {trace.model ? modelLabel(trace.model) : providerLabel(trace.provider)}
              </span>
            )}
          </div>
        )}

        {trace?.scope_note && <p className="scope-note">{trace.scope_note}</p>}
      </div>
    </div>
  )
}
