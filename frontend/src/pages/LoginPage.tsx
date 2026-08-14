import { useState } from 'react'

import { ApiError, login, setToken } from '../services/api'
import type { UserInfo } from '../types'

const TEST_USERS = [
  { email: 'supervisor@example.com', role: 'supervisor', blurb: 'Own team only' },
  { email: 'analyst@example.com', role: 'analyst', blurb: 'Stats, no pay' },
  { email: 'hr@example.com', role: 'hr', blurb: 'People & leave' },
  { email: 'admin@example.com', role: 'admin', blurb: 'Reads everything' },
  { email: 'superadmin@example.com', role: 'super admin', blurb: 'Edits access' },
]

export default function LoginPage({ onSignedIn }: { onSignedIn: (user: UserInfo) => void }) {
  const [email, setEmail] = useState('supervisor@example.com')
  const [password, setPassword] = useState('password123')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result = await login(email.trim(), password)
      setToken(result.access_token)
      onSignedIn(result.user)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <div className="auth-art" aria-hidden="true">
        <div className="auth-glow" />
        <blockquote className="auth-quote">
          The planner decides <em>which agent</em>. The agent decides <em>which tool</em>.
          <br />
          Only the tool decides <em>whether you may</em>.
        </blockquote>
      </div>

      <div className="auth-panel">
        <form className="auth-form" onSubmit={submit}>
          <div className="brand brand-lg">
            <span className="brand-mark" aria-hidden="true">
              R
            </span>
            <span className="brand-text">
              <span className="brand-name">RBAC</span>
              <span className="brand-sub">Agentic access control</span>
            </span>
          </div>

          <h1>Sign in</h1>
          <p className="auth-lede">
            Your role decides what the assistant can retrieve. The backend enforces it, not the
            model.
          </p>

          <label>
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              autoComplete="username"
            />
          </label>

          <label>
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              autoComplete="current-password"
            />
          </label>

          {error && <p className="form-error">{error}</p>}

          <button type="submit" className="primary-button" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>

          <div className="test-users">
            <span className="test-users-label">Demo accounts · password123</span>
            <div className="test-user-list">
              {TEST_USERS.map((user) => (
                <button
                  key={user.email}
                  type="button"
                  className={`test-user${email === user.email ? ' test-user-active' : ''}`}
                  onClick={() => {
                    setEmail(user.email)
                    setPassword('password123')
                  }}
                >
                  <span className="test-user-role">{user.role}</span>
                  <span className="test-user-blurb">{user.blurb}</span>
                </button>
              ))}
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
