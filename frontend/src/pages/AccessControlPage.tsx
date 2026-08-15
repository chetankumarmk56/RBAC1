import { useCallback, useEffect, useState } from 'react'

import { modelLabel, permissionLabel, providerLabel, roleLabel, toolLabel } from '../labels'
import {
  ApiError,
  fetchAccessMatrix,
  setAccess,
  setDataScope,
  setFieldAccess,
  setModelAccess,
} from '../services/api'
import type { AccessChangeResponse, AccessMatrix, RoleSummary } from '../types'

interface Props {
  onUnauthorized: () => void
}

type Tab = 'tools' | 'data' | 'models'

const TABS: { key: Tab; label: string; blurb: string }[] = [
  { key: 'tools', label: 'Tools', blurb: 'Which roles may run which tool.' },
  {
    key: 'data',
    label: 'Data',
    blurb: 'Which datasets, which columns of them, and how many employees’ rows.',
  },
  { key: 'models', label: 'Models', blurb: 'Which language model each role may run.' },
]

/** A cell that is fixed by a guardrail rather than editable. */
function Locked({ on, title }: { on: boolean; title: string }) {
  return (
    <span className={`locked-mark${on ? ' locked-on' : ''}`} title={title}>
      {on ? '✓' : '–'}
    </span>
  )
}

/**
 * A row header: what the thing is called, with the identifier it has in the database
 * underneath. The label is for reading, the identifier is what the audit log records.
 */
function Named({ label, id, children }: { label: string; id: string; children?: React.ReactNode }) {
  return (
    <>
      <span className="named-label">
        {label}
        {children}
      </span>
      <code className="named-id">{id}</code>
    </>
  )
}

export default function AccessControlPage({ onUnauthorized }: Props) {
  const [matrix, setMatrix] = useState<AccessMatrix | null>(null)
  const [tab, setTab] = useState<Tab>('tools')
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

  /** Every change goes through one path: post, then take the fresh matrix back. */
  const apply = useCallback(
    async (key: string, run: () => Promise<AccessChangeResponse>) => {
      setPendingCell(key)
      setError(null)
      setNotice(null)
      try {
        const result = await run()
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
    },
    [onUnauthorized],
  )

  if (loading) return <div className="page-body muted">Loading access matrix…</div>

  if (!matrix) {
    return (
      <div className="page-body">
        <p className="error">{error ?? 'No access matrix available.'}</p>
      </div>
    )
  }

  const roles = matrix.roles
  const busy = pendingCell !== null
  const roleHeads = roles.map((role) => (
    <th key={role.name} className="matrix-role-head" title={role.description ?? ''}>
      {roleLabel(role.name)}
      {role.protected && <span className="lock" title="Protected role"> 🔒</span>}
    </th>
  ))

  // Columns a role does not receive, per dataset it can actually open. Datasets the
  // role has no permission for are skipped — everything there is withheld already,
  // and the permission chips above say so.
  const withheldSummary = (role: RoleSummary) =>
    matrix.datasets
      .filter((dataset) => dataset.roles_with_access.includes(role.name))
      .map((dataset) => ({ dataset, fields: role.fields_withheld[dataset.key] ?? [] }))
      .filter(({ fields }) => fields.length > 0)

  const protectedNote = (role: RoleSummary) =>
    `The ${roleLabel(role.name)} role is protected and cannot be changed.`

  return (
    <div className="page-body">
      <div className="tabs" role="tablist">
        {TABS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            role="tab"
            aria-selected={tab === entry.key}
            className={`tab${tab === entry.key ? ' tab-active' : ''}`}
            onClick={() => setTab(entry.key)}
          >
            {entry.label}
          </button>
        ))}
      </div>

      <p className="muted page-intro">
        {TABS.find((entry) => entry.key === tab)?.blurb} Every change is written to PostgreSQL,
        recorded in the audit log, and applies from that role's next message — no restart.
      </p>

      {notice && <p className="notice">{notice}</p>}
      {error && <p className="error">{error}</p>}

      {/* ------------------------------------------------------------- tools -- */}
      {tab === 'tools' && (
        <>
          <div className="table-scroll">
            <table className="matrix">
              <thead>
                <tr>
                  <th className="matrix-tool-head">Tool</th>
                  <th>Permission</th>
                  {roleHeads}
                </tr>
              </thead>
              <tbody>
                {matrix.tools.map((tool) => (
                  <tr key={tool.name}>
                    <th scope="row" className="matrix-tool" title={tool.description}>
                      <Named label={toolLabel(tool.name)} id={tool.name}>
                        {tool.mutates && <span className="tag tag-write">writes</span>}
                      </Named>
                    </th>
                    <td className="matrix-perm">
                      <Named
                        label={permissionLabel(tool.required_permission)}
                        id={tool.required_permission}
                      />
                    </td>

                    {roles.map((role) => {
                      const has = tool.roles_with_access.includes(role.name)
                      const locked = !tool.configurable || role.protected
                      const key = `tool:${role.name}:${tool.name}`
                      return (
                        <td key={key} className="matrix-cell">
                          {locked ? (
                            <Locked
                              on={has}
                              title={
                                role.protected
                                  ? protectedNote(role)
                                  : `${toolLabel(tool.name)} manages RBAC itself and cannot be granted from here.`
                              }
                            />
                          ) : (
                            <input
                              type="checkbox"
                              checked={has}
                              disabled={busy}
                              aria-label={`${roleLabel(role.name)} can use ${toolLabel(tool.name)}`}
                              onChange={(event) =>
                                apply(key, () =>
                                  setAccess(role.name, tool.name, event.target.checked),
                                )
                              }
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
              <code>super_admin</code> role cannot be edited, so a super admin cannot revoke their
              own <code>permissions:write</code> and lock everyone out.
            </p>
            <p>
              <span className="tag tag-write">writes</span> <strong>RBAC management tools.</strong>{' '}
              The tools that grant and revoke access cannot themselves be granted to another role
              from here — a second super admin can only be created in <code>seed.py</code>.
            </p>
          </div>
        </>
      )}

      {/* -------------------------------------------------------------- data -- */}
      {tab === 'data' && (
        <>
          <section className="panel">
            <h2>Row access</h2>
            <p className="panel-blurb">
              How many employees' rows a role sees, whatever dataset it reads. Narrower scopes
              resolve against the employee record the login is linked to.
            </p>
            <div className="scope-rows">
              {roles.map((role) => (
                <div key={role.name} className="scope-row">
                  <span className={`role-badge role-${role.name}`}>{roleLabel(role.name)}</span>
                  <div className="scope-options">
                    {matrix.scopes.map((scope) => {
                      const key = `scope:${role.name}:${scope.key}`
                      return (
                        <label
                          key={key}
                          className={`scope-option${role.row_scope === scope.key ? ' scope-on' : ''}${
                            role.protected ? ' scope-locked' : ''
                          }`}
                          title={
                            role.protected ? protectedNote(role) : scope.description
                          }
                        >
                          <input
                            type="radio"
                            name={`scope-${role.name}`}
                            checked={role.row_scope === scope.key}
                            disabled={busy || role.protected}
                            onChange={() => apply(key, () => setDataScope(role.name, scope.key))}
                          />
                          {scope.key}
                        </label>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {matrix.datasets.map((dataset) => (
            <section key={dataset.key} className="panel">
              <h2>
                {dataset.label}
                <span className="panel-perm">
                  {permissionLabel(dataset.required_permission)}
                  <code>{dataset.required_permission}</code>
                </span>
              </h2>
              <p className="panel-blurb">{dataset.blurb}</p>

              <div className="table-scroll">
                <table className="matrix">
                  <thead>
                    <tr>
                      <th className="matrix-tool-head">Column</th>
                      {roleHeads}
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="matrix-row-dataset">
                      <th scope="row" className="matrix-tool">
                        <Named label="Dataset access" id={dataset.tool} />
                      </th>
                      {roles.map((role) => {
                        const has = dataset.roles_with_access.includes(role.name)
                        const key = `data:${role.name}:${dataset.key}`
                        return (
                          <td key={key} className="matrix-cell">
                            {role.protected ? (
                              <Locked on={has} title={protectedNote(role)} />
                            ) : (
                              <input
                                type="checkbox"
                                checked={has}
                                disabled={busy}
                                aria-label={`${roleLabel(role.name)} can read ${dataset.label}`}
                                onChange={(event) =>
                                  apply(key, () =>
                                    setAccess(role.name, dataset.tool, event.target.checked),
                                  )
                                }
                              />
                            )}
                          </td>
                        )
                      })}
                    </tr>

                    {dataset.fields.map((field) => (
                      <tr key={field.key}>
                        <th scope="row" className="matrix-tool">
                          <Named label={field.label} id={field.key}>
                            {field.locked && <span className="tag tag-locked">identity</span>}
                          </Named>
                        </th>
                        {roles.map((role) => {
                          const has = (role.fields[dataset.key] ?? []).includes(field.key)
                          const hasDataset = dataset.roles_with_access.includes(role.name)
                          // Ticked, but withheld anyway because it reconstructs a column
                          // this role may not see. The box stays checked — it is what is
                          // stored — but the cell reads as inactive, like an ungranted dataset.
                          const rebuilt =
                            has && (role.fields_withheld[dataset.key] ?? []).includes(field.key)
                          const key = `field:${role.name}:${dataset.key}:${field.key}`
                          if (field.locked || role.protected) {
                            return (
                              <td key={key} className="matrix-cell">
                                <Locked
                                  on={field.locked || has}
                                  title={
                                    role.protected
                                      ? protectedNote(role)
                                      : `${field.label} identifies the row and is always returned.`
                                  }
                                />
                              </td>
                            )
                          }
                          return (
                            <td
                              key={key}
                              className={`matrix-cell${hasDataset && !rebuilt ? '' : ' matrix-cell-inactive'}`}
                              title={
                                !hasDataset
                                  ? `The ${roleLabel(role.name)} role cannot read ${dataset.label} at all, so this has no effect until the dataset is granted.`
                                  : rebuilt
                                    ? `${field.label} is withheld from the ${roleLabel(role.name)} role even though it is ticked: together with the other ${dataset.label.toLowerCase()} figures it reconstructs a column this role may not see. Grant that column back to make this one count.`
                                    : undefined
                              }
                            >
                              <input
                                type="checkbox"
                                checked={has}
                                disabled={busy}
                                aria-label={`${roleLabel(role.name)} can see ${dataset.label} ${field.label}`}
                                onChange={(event) =>
                                  apply(key, () =>
                                    setFieldAccess(
                                      role.name,
                                      dataset.key,
                                      field.key,
                                      event.target.checked,
                                    ),
                                  )
                                }
                              />
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}

          <div className="legend">
            <p>
              <strong>Dataset access</strong> is the same grant as the tool row on the Tools tab —
              one permission, one place in the database. Untick it and the role cannot read the
              dataset at all.
            </p>
            <p>
              <strong>Columns</strong> narrow what comes back once the dataset is granted. A
              withheld column is stripped from the tool result before the agent sees it, along with
              any total computed from it, so the model cannot quote or estimate it.
            </p>
            <p>
              A <strong>greyed-out tick</strong> is a column that is granted but withheld anyway,
              because the columns around it reconstruct one this role may not see — payroll's four
              money figures satisfy net pay = base salary + bonus − deductions, so any three give
              the fourth. Withholding one of them withholds all four. Hover the cell for the
              specific reason.
            </p>
          </div>
        </>
      )}

      {/* ------------------------------------------------------------ models -- */}
      {tab === 'models' && (
        <>
          <div className="table-scroll">
            <table className="matrix">
              <thead>
                <tr>
                  <th className="matrix-tool-head">Model</th>
                  <th>Provider</th>
                  {roleHeads}
                </tr>
              </thead>
              <tbody>
                {matrix.models.map((model) => (
                  <tr key={model.key}>
                    <th scope="row" className="matrix-tool" title={model.blurb}>
                      <Named label={model.label} id={model.key}>
                        {!model.available && (
                          <span className="tag tag-missing" title="No API key for this provider">
                            no key
                          </span>
                        )}
                      </Named>
                    </th>
                    <td className="matrix-perm">{providerLabel(model.provider)}</td>
                    {roles.map((role) => {
                      const has = model.roles_with_access.includes(role.name)
                      const key = `model:${role.name}:${model.key}`
                      return (
                        <td key={key} className="matrix-cell">
                          {role.protected ? (
                            <Locked on={has} title={protectedNote(role)} />
                          ) : (
                            <input
                              type="checkbox"
                              checked={has}
                              disabled={busy}
                              aria-label={`${roleLabel(role.name)} can use ${model.label}`}
                              onChange={(event) =>
                                apply(key, () =>
                                  setModelAccess(role.name, model.key, event.target.checked),
                                )
                              }
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
              A role runs the <strong>most capable model it holds</strong> and falls back down its
              own list when one fails. Users pick a model in the chat composer; asking for one their
              role does not hold is refused before any provider is called, and the refusal is
              audited like any other denial.
            </p>
            <p>
              <span className="tag tag-missing">no key</span> means this server has no API key for
              that provider — granting the model has no effect until one is set in{' '}
              <code>backend/.env</code>.
            </p>
          </div>
        </>
      )}

      <section className="roles-summary">
        <h2>Roles</h2>
        {roles.map((role) => (
          <div key={role.name} className="role-row">
            <div className="role-row-head">
              <span className={`role-badge role-${role.name}`}>{roleLabel(role.name)}</span>
              <span className="muted">{role.description}</span>
            </div>
            <div className="role-row-perms">
              <span className="chip chip-scope" title="Row reach">
                rows: {role.row_scope}
              </span>
              {role.models.length > 0 ? (
                <span className="chip chip-model" title="Runs the first, falls back along the rest">
                  {modelLabel(role.models.join(' -> '))}
                </span>
              ) : (
                <span className="chip chip-denied">no model</span>
              )}
              {role.permissions.map((permission) => (
                <span key={permission} className="chip chip-perm" title={permission}>
                  {permissionLabel(permission)}
                </span>
              ))}
              {/* Permission chips are dataset-level. Without this, a role reading as
                  "payroll" here could in fact receive no payroll figure at all. */}
              {withheldSummary(role).map(({ dataset, fields }) => (
                <span
                  key={`withheld:${dataset.key}`}
                  className="chip chip-denied"
                  title={`Withheld from ${dataset.label}: ${fields.join(', ')}`}
                >
                  −{dataset.label.toLowerCase()}: {fields.length} withheld
                </span>
              ))}
            </div>
          </div>
        ))}
      </section>
    </div>
  )
}
