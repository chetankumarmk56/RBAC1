/**
 * Display names for the RBAC vocabulary.
 *
 * The backend stays the source of truth for the identifiers themselves — a tool is
 * `get_payroll` and a permission is `payroll:read` in the database, the API and the
 * audit log. This file only decides how they read on screen, and every lookup falls
 * back to a tidied version of the identifier, so something added on the backend still
 * renders sensibly before it is listed here.
 *
 * Raw identifiers are never hidden outright: the console shows them under the label,
 * and chips carry them in a tooltip.
 */

const TOOL_LABELS: Record<string, string> = {
  get_payroll: 'Payroll',
  get_employee: 'Employee directory',
  get_attendance: 'Attendance',
  get_performance: 'Performance reviews',
  get_leave: 'Leave & time off',
  get_analytics: 'Analytics',
  get_reports: 'Summary reports',
  get_audit_logs: 'Audit log',
  get_role_permissions: 'Role permissions',
  get_tool_permissions: 'Tool access matrix',
  get_model_access: 'Model access',
  get_data_access: 'Data access',
  grant_tool_access: 'Grant tool access',
  revoke_tool_access: 'Revoke tool access',
  set_model_access: 'Change model access',
  set_field_access: 'Change column access',
  set_data_scope: 'Change row scope',
}

const PERMISSION_LABELS: Record<string, string> = {
  'payroll:read': 'Read payroll',
  'employee:read': 'Read employee directory',
  'attendance:read': 'Read attendance',
  'performance:read': 'Read performance',
  'leave:read': 'Read leave',
  'analytics:read': 'Read analytics',
  'reports:read': 'Read reports',
  'audit:read': 'Read audit log',
  'permissions:manage': 'View access settings',
  'permissions:write': 'Change access settings',
}

const ROLE_LABELS: Record<string, string> = {
  supervisor: 'Supervisor',
  analyst: 'Analyst',
  hr: 'HR',
  admin: 'Admin',
  super_admin: 'Super admin',
}

/** Agents and the two non-agent gates that can appear in a trace. */
const AGENT_LABELS: Record<string, string> = {
  supervisor_agent: 'Supervisor agent',
  analyst_agent: 'Analyst agent',
  hr_agent: 'HR agent',
  admin_agent: 'Administrator agent',
  model_gate: 'Model gate',
  admin_console: 'Access console',
}

const MODEL_LABELS: Record<string, string> = {
  'claude-opus': 'Claude Opus',
  'claude-sonnet': 'Claude Sonnet',
  'claude-haiku': 'Claude Haiku',
  gemini: 'Gemini',
}

const PROVIDER_LABELS: Record<string, string> = {
  claude: 'Claude',
  gemini: 'Gemini',
}

/** `some_identifier` / `some-identifier` -> `Some identifier`. */
function humanise(value: string): string {
  const words = value.replace(/[_-]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function toolLabel(name?: string | null): string {
  if (!name) return ''
  return TOOL_LABELS[name] ?? humanise(name)
}

export function permissionLabel(name?: string | null): string {
  if (!name) return ''
  if (PERMISSION_LABELS[name]) return PERMISSION_LABELS[name]

  // A model decision is audited as a permission — `model:claude-opus`.
  const [subject, action] = name.split(':')
  if (subject === 'model') return `${modelLabel(action)} access`
  if (!action) return humanise(name)
  return `${humanise(action)} ${subject.replace(/[_-]+/g, ' ')}`
}

export function roleLabel(name?: string | null): string {
  if (!name) return ''
  return ROLE_LABELS[name] ?? humanise(name)
}

export function agentLabel(name?: string | null): string {
  if (!name) return ''
  return AGENT_LABELS[name] ?? humanise(name)
}

/** Handles a fallback chain too: `claude-sonnet -> gemini`. */
export function modelLabel(key?: string | null): string {
  if (!key) return ''
  return key
    .split('->')
    .map((part) => {
      const trimmed = part.trim()
      return MODEL_LABELS[trimmed] ?? humanise(trimmed)
    })
    .join(' → ')
}

export function providerLabel(name?: string | null): string {
  if (!name) return ''
  return PROVIDER_LABELS[name] ?? humanise(name)
}
