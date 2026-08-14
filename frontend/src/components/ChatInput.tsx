import { useEffect, useRef, useState } from 'react'

import type { ModelOption } from '../types'

interface Props {
  disabled: boolean
  onSend: (message: string) => void
  /** Every model in the catalogue — locked ones included, so the boundary is visible. */
  models: ModelOption[]
  /** The selected model key, or '' for "the best one my role holds". */
  model: string
  onModelChange: (model: string) => void
  /** Which model 'Auto' resolves to, or null when the role holds none. */
  defaultModel: string | null
}

export default function ChatInput({
  disabled,
  onSend,
  models,
  model,
  onModelChange,
  defaultModel,
}: Props) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Grow with the content, up to a cap, like a real composer.
  useEffect(() => {
    const node = textareaRef.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, 200)}px`
  }, [value])

  function submit() {
    const message = value.trim()
    if (!message || disabled) return
    onSend(message)
    setValue('')
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter makes a new line.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  const selected = models.find((option) => option.key === model)
  const auto = models.find((option) => option.key === defaultModel)

  return (
    <div className="composer-dock">
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <textarea
          ref={textareaRef}
          value={value}
          rows={1}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about payroll, people, attendance, leave, analytics…"
          maxLength={2000}
          autoFocus
        />
        <button
          type="submit"
          className="send"
          disabled={disabled || value.trim() === ''}
          aria-label="Send message"
        >
          <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
            <path
              d="M12 19V5M12 5l-6 6M12 5l6 6"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </form>

      <div className="composer-foot">
        {/* Locked models stay selectable on purpose: the server refuses them, which
            is the point — the picker shows, the backend decides. */}
        <label className={`model-picker${selected && !selected.allowed ? ' model-picker-blocked' : ''}`}>
          <span className="model-picker-label">Model</span>
          <select
            value={model}
            disabled={disabled}
            onChange={(event) => onModelChange(event.target.value)}
            title={selected?.blurb ?? 'Runs the most capable model your role holds.'}
          >
            <option value="">Auto{auto ? ` · ${auto.label}` : ''}</option>
            {models.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
                {option.allowed ? '' : ' 🔒'}
              </option>
            ))}
          </select>
        </label>

        <p className="composer-hint">
          {selected && !selected.allowed
            ? `Your role cannot use ${selected.label} — sending will be refused before any model is called.`
            : 'Your role decides what comes back. The backend enforces it — not the model.'}
        </p>
      </div>
    </div>
  )
}
