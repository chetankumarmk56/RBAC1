import { useCallback, useEffect, useRef, useState } from 'react'

import ChatInput from '../components/ChatInput'
import ChatMessageView from '../components/ChatMessage'
import LiveStatus from '../components/LiveStatus'
import { modelLabel, permissionLabel, roleLabel } from '../labels'
import { ApiError, fetchConversation, fetchModelOptions, streamChat } from '../services/api'
import type { ChatMessage, ModelOption, ProgressStep, UserInfo } from '../types'

interface Props {
  user: UserInfo
  conversationId: number | null
  onConversationStarted: (id: number) => void
  onConversationUpdated: () => void
  onUnauthorized: () => void
}

interface Starter {
  text: string
  /** The last starter for every role is one the role cannot reach. */
  hint?: string
}

/**
 * Four openers per role. The first three land; the fourth is deliberately out of
 * reach for that role, so the RBAC boundary is one click away on every new chat.
 * Super admin holds every permission, so its fourth exercises the lockout guard
 * instead — the one action even a super admin is refused.
 */
const STARTERS: Record<string, Starter[]> = {
  supervisor: [
    { text: "Show me my team's payroll." },
    { text: "How is my team's attendance?" },
    { text: "Show me my team's performance reviews." },
    { text: 'Who on my team is on leave right now?', hint: 'no leave access' },
  ],
  analyst: [
    { text: 'What is the average attendance?' },
    { text: 'Show me the performance rating breakdown.' },
    { text: 'Give me a summary report.' },
    { text: 'Show me employee salaries.', hint: 'no payroll access' },
  ],
  hr: [
    { text: 'Show me employees currently on leave.' },
    { text: 'List everyone in the Engineering department.' },
    { text: 'How has attendance been this month?' },
    { text: 'Show me payroll information.', hint: 'no payroll access' },
  ],
  admin: [
    { text: 'Show me payroll information.' },
    { text: 'Show me denied access attempts.' },
    { text: 'Show me the current role permissions.' },
    { text: 'Give the analyst role access to payroll.', hint: 'cannot change access' },
  ],
  super_admin: [
    { text: 'Show me the tool access matrix.' },
    { text: 'Give the analyst role access to payroll.' },
    { text: 'Revoke payroll access from the analyst role.' },
    { text: 'Revoke payroll access from the super_admin role.', hint: 'protected role' },
  ],
}

let messageCounter = 0
const nextId = () => `m${(messageCounter += 1)}`

export default function ChatPage({
  user,
  conversationId,
  onConversationStarted,
  onConversationUpdated,
  onUnauthorized,
}: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [step, setStep] = useState<ProgressStep | null>(null)
  const [busy, setBusy] = useState(false)
  const [models, setModels] = useState<ModelOption[]>([])
  const [defaultModel, setDefaultModel] = useState<string | null>(null)
  // '' means "let my role's best model answer"; a key names one explicitly.
  const [model, setModel] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  // Kept in a ref so `send` always reads the live id, even mid-stream.
  const activeId = useRef<number | null>(conversationId)
  // Ids this component created itself, so the loader doesn't clobber the answer
  // that is still streaming into view.
  const selfStarted = useRef<number | null>(null)

  useEffect(() => {
    if (conversationId !== null && selfStarted.current === conversationId) {
      // We just started this conversation; its messages are already on screen.
      selfStarted.current = null
      return
    }

    activeId.current = conversationId
    setMessages([])

    if (conversationId === null) return

    let cancelled = false
    fetchConversation(conversationId)
      .then((detail) => {
        if (cancelled) return
        setMessages(
          detail.messages.map((stored) => ({
            id: `s${stored.id}`,
            role: stored.role,
            text: stored.content,
            trace: stored.trace ?? undefined,
            failed: stored.failed,
          })),
        )
      })
      .catch((caught) => {
        if (caught instanceof ApiError && caught.status === 401) onUnauthorized()
      })
    return () => {
      cancelled = true
    }
  }, [conversationId, onUnauthorized])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, step])

  // The picker lists every model, locked ones included — the server decides.
  useEffect(() => {
    fetchModelOptions()
      .then((options) => {
        setModels(options.models)
        setDefaultModel(options.default_model)
      })
      .catch(() => setModels([]))
  }, [user.role])

  const send = useCallback(
    async (text: string) => {
      setMessages((current) => [...current, { id: nextId(), role: 'user', text }])
      setBusy(true)
      setStep(null)

      try {
        await streamChat(text, activeId.current, model || null, (event) => {
          if (event.type === 'conversation') {
            // A new chat gets its id here, before the answer arrives.
            if (activeId.current !== event.id) {
              activeId.current = event.id
              selfStarted.current = event.id
              onConversationStarted(event.id)
            }
          } else if (event.type === 'status') {
            // Each stage replaces the previous line rather than stacking under it.
            setStep({ stage: event.stage, text: event.text, decision: event.decision })
          } else if (event.type === 'done') {
            setMessages((current) => [
              ...current,
              { id: nextId(), role: 'assistant', text: event.reply, trace: event.trace },
            ])
          } else {
            setMessages((current) => [
              ...current,
              { id: nextId(), role: 'assistant', text: event.message, failed: true },
            ])
          }
        })
        onConversationUpdated()
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 401) {
          onUnauthorized()
          return
        }
        const detail = caught instanceof ApiError ? caught.message : 'Could not reach the server.'
        setMessages((current) => [
          ...current,
          { id: nextId(), role: 'assistant', text: detail, failed: true },
        ])
      } finally {
        setBusy(false)
        setStep(null)
      }
    },
    [model, onConversationStarted, onConversationUpdated, onUnauthorized],
  )

  const starters = STARTERS[user.role] ?? []
  const empty = messages.length === 0 && !busy

  return (
    <>
      <div className="thread">
        {empty && (
          <div className="welcome">
            <p className="welcome-eyebrow">Signed in as {roleLabel(user.role)}</p>
            <h2>What would you like to know?</h2>
            <p className="welcome-lede">
              Ask in plain English. A planner agent routes your request to a role agent, the
              agent picks a tool, and the tool checks your permissions before it reaches
              PostgreSQL.
            </p>

            {starters.length > 0 && (
              <div className="starters">
                {starters.map((starter, index) => (
                  <button
                    key={starter.text}
                    type="button"
                    className={`starter${starter.hint ? ' starter-blocked' : ''}`}
                    style={{ animationDelay: `${120 + index * 70}ms` }}
                    onClick={() => send(starter.text)}
                    title={
                      starter.hint
                        ? 'Your role cannot reach this — the tool will refuse it.'
                        : undefined
                    }
                  >
                    <span className="starter-text">{starter.text}</span>
                    {starter.hint ? (
                      <span className="starter-hint">{starter.hint}</span>
                    ) : (
                      <span className="starter-arrow" aria-hidden="true">
                        →
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}

            <details className="perms-disclosure">
              <summary>
                <span className="caret" aria-hidden="true" />
                Your {user.permissions.length} permissions · {user.models.length} model
                {user.models.length === 1 ? '' : 's'} · rows: {user.row_scope}
              </summary>
              <div className="perms-grid">
                <span className="chip chip-scope" title="How far your rows reach">
                  rows: {user.row_scope}
                </span>
                {user.models.length > 0 ? (
                  <span className="chip chip-model" title="Runs the first, falls back along the rest">
                    {modelLabel(user.models.join(' -> '))}
                  </span>
                ) : (
                  <span className="chip chip-denied">no model</span>
                )}
                {user.permissions.map((permission) => (
                  <span key={permission} className="chip" title={permission}>
                    {permissionLabel(permission)}
                  </span>
                ))}
              </div>
            </details>
          </div>
        )}

        {messages.map((message) => (
          <ChatMessageView key={message.id} message={message} />
        ))}

        {busy && (
          <div className="turn turn-assistant">
            <div className="assistant-mark assistant-mark-busy" aria-hidden="true">
              R
            </div>
            <div className="assistant-body">
              <LiveStatus step={step} />
            </div>
          </div>
        )}

        <div ref={endRef} />
      </div>

      <ChatInput
        disabled={busy}
        onSend={send}
        models={models}
        model={model}
        onModelChange={setModel}
        defaultModel={defaultModel}
      />
    </>
  )
}
