import type { ProgressStep } from '../types'

/**
 * One line at a time. Each pipeline stage replaces the previous one — planner
 * thinking, then the agent it picked, then the tool, then the RBAC decision —
 * so the user reads a sequence rather than a growing list.
 */
export default function LiveStatus({ step }: { step: ProgressStep | null }) {
  if (!step) {
    return (
      <div className="live-status">
        <span className="live-dot" />
        <span className="live-text">Thinking</span>
      </div>
    )
  }

  const denied = step.decision === 'DENIED'

  return (
    <div className={`live-status${denied ? ' live-denied' : ''}`}>
      <span className="live-dot" />
      {/* Keyed by text so React remounts the span and replays the entrance. */}
      <span key={step.text} className="live-text">
        {step.text}
      </span>
    </div>
  )
}
