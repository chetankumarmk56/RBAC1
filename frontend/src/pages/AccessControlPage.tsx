import { useEffect, useState } from 'react'

import { ApiError, fetchAccessMatrix, setAccess } from '../services/api'
import type { AccessMatrix } from '../types'

interface Props {
  onUnauthorized: () => void
}

export default function AccessControlPage({ onUnauthorized }: Props) {
  const [matrix, setMatrix] = useState<AccessMatrix | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [pendingCell, setPendingCell] = useState<string | null>(null)

  useEffect(() => {
    fetchAccessMatrix()
      .then(setMatrix)
      .catch((caught) => {
        if (caught instanceof ApiError && caught.status === 401) {
          onUnauthorized()
          return
        }
        setError(caught instanceof ApiError ? caught.message : 'Could not load the access matrix.')
      })
      .finally(() => setLoading(false))
  }, [onUnauthorized])

  async function toggle(roleName: string, toolName: string, granted: boolean) {
    setPendingCell(`${roleName}:${toolName}`)
    setError(null)
    setNotice(null)
    try {
      const result = await setAccess(roleName, toolName, granted)
      setMatrix(result.matrix)
      setNotice(result.message)
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        onUnauthorized()
        return
      }
      setError(caught instanceof ApiError ? caught.message : 'Could not apply the change.')
    } finally {
      setPendingCell(null)
    }
  }

  if (loading) return <div className="page-body muted">Loading access matrix…</div>

  if (!matrix) {
    return (
      <div className="page-body">
        <p className="error">{error ?? 'No access matrix available.'}</p>
      </div>
    )
  }

  return (
    <div className="page-body">
      <p className="muted page-intro">
        Tick a box to give a role access to a tool. Each change writes the tool's required
        permission to <code>role_permissions</code> in PostgreSQL and is recorded in the audit
        log. It applies from that role's next message — no restart.
      </p>

      {notice && <p className="notice">{notice}</p>}
      {error && <p className="error">{error}</p>}

      <div className="table-scroll">
        <table className="matrix">
          <thead>
            <tr>
              <th className="matrix-tool-head">Tool</th>
              <th>Permission</th>
              {matrix.roles.map((role) => (
                <th key={role.name} className="matrix-role-head" title={role.description ?? ''}>
                  {role.name}
                  {role.protected && <span className="lock" title="Protected role"> 🔒</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.tools.map((tool) => (
              <tr key={tool.name}>
                <th scope="row" className="matrix-tool" title={tool.description}>
                  <code>{tool.name}</code>
                  {tool.mutates && <span className="tag tag-write">writes</span>}
                </th>
                <td className="matrix-perm">
                  <code>{tool.required_permission}</code>
                </td>

                {matrix.roles.map((role) => {
                  const has = tool.roles_with_access.includes(role.name)
                  const locked = !tool.configurable || role.protected
                  const key = `${role.name}:${tool.name}`
                  return (
                    <td key={key} className="matrix-cell">
                      {locked ? (
                        <span
                          className={`locked-mark${has ? ' locked-on' : ''}`}
                          title={
                            role.protected
                              ? `The ${role.name} role is protected and cannot be changed.`
                              : `${tool.name} manages RBAC itself and cannot be granted from here.`
                          }
                        >
                          {has ? '✓' : '–'}
                        </span>
                      ) : (
                        <input
                          type="checkbox"
                          checked={has}
                          disabled={pendingCell !== null}
                          aria-label={`${role.name} can use ${tool.name}`}
                          onChange={(event) => toggle(role.name, tool.name, event.target.checked)}
                        />
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="legend">
        <p>
          <span className="lock">🔒</span> <strong>Protected role.</strong> The{' '}
          <code>super_admin</code> role cannot be edited, so a super admin cannot revoke their own{' '}
          <code>permissions:write</code> and lock everyone out.
        </p>
        <p>
          <span className="tag tag-write">writes</span> <strong>RBAC management tools.</strong>{' '}
          <code>grant_tool_access</code> and <code>revoke_tool_access</code> cannot be granted to
          another role from here — a second super admin can only be created in{' '}
          <code>seed.py</code>.
        </p>
      </div>

      <section className="roles-summary">
        <h2>Roles</h2>
        {matrix.roles.map((role) => (
          <div key={role.name} className="role-row">
            <div className="role-row-head">
              <span className={`role-badge role-${role.name}`}>{role.name}</span>
              <span className="muted">{role.description}</span>
            </div>
            <div className="role-row-perms">
              {role.permissions.map((permission) => (
                <span key={permission} className="chip chip-perm">
                  {permission}
                </span>
              ))}
            </div>
          </div>
        ))}
      </section>
    </div>
  )
}
