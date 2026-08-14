import { useEffect, useRef, useState } from 'react'

interface Props {
  disabled: boolean
  onSend: (message: string) => void
}

export default function ChatInput({ disabled, onSend }: Props) {
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
      <p className="composer-hint">
        Your role decides what comes back. The backend enforces it — not the model.
      </p>
    </div>
  )
}
