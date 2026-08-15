export interface UserInfo {
  email: string
  full_name: string
  role: string
  permissions: string[]
  /** Models this role may run, most capable first. */
  models: string[]
  /** Row reach: all | department | team | self. */
  row_scope: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  user: UserInfo
}

/** How a prompt travelled through planner -> agent -> tool -> RBAC -> database. */
export interface ChatTrace {
  intent: string
  agent: string | null
  reasoning: string | null
  tool: string | null
  required_permission: string | null
  decision: 'ALLOWED' | 'DENIED' | null
  reason: string | null
  row_count: number | null
  scope_note: string | null
  /** Which LLM answered: 'claude', or 'gemini' when the fallback took over. */
  provider: string | null
  /** The model that answered, e.g. 'claude-sonnet'; on a model denial, the one refused. */
  model: string | null
  /** Columns the field-level policy removed before the agent saw the data. */
  withheld_fields: string[]
  steps: string[]
}

/** One entry in the composer's model picker. */
export interface ModelOption {
  key: string
  label: string
  provider: string
  blurb: string
  /** False when the role may not run it — picking it still sends, and the server refuses. */
  allowed: boolean
  /** False when the server has no credentials for that provider. */
  available: boolean
}

export interface ModelOptions {
  models: ModelOption[]
  default_model: string | null
}

export interface ChatResponse {
  reply: string
  trace: ChatTrace
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  trace?: ChatTrace
  failed?: boolean
}

/* ------------------------------- live pipeline ---------------------------- */

export type Stage =
  | 'model'
  | 'planner'
  | 'agent'
  | 'tool'
  | 'rbac'
  | 'database'
  | 'fields'
  | 'compose'
  | 'error'

export interface ProgressStep {
  stage: Stage
  text: string
  decision?: 'ALLOWED' | 'DENIED'
}

export interface StreamStatus extends ProgressStep {
  type: 'status'
  agent?: string
  tool?: string
  required_permission?: string
  row_count?: number
  intent?: string
}

export interface StreamConversation {
  type: 'conversation'
  id: number
  title: string
}

export interface StreamDone {
  type: 'done'
  reply: string
  trace: ChatTrace
}

export interface StreamError {
  type: 'error'
  message: string
}

export type StreamEvent = StreamStatus | StreamConversation | StreamDone | StreamError

/* -------------------------------- chat history ---------------------------- */

export interface ConversationSummary {
  id: number
  title: string
  updated_at: string
  message_count: number
}

export interface StoredMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  trace: ChatTrace | null
  failed: boolean
}

export interface ConversationDetail {
  id: number
  title: string
  updated_at: string
  messages: StoredMessage[]
}

/* ---------------------------- access-control console ---------------------- */

export interface RoleSummary {
  name: string
  description: string | null
  permissions: string[]
  /** Protected roles cannot be edited — the lockout guard. */
  protected: boolean
  /** Models this role may run, most capable first. */
  models: string[]
  /** Row reach: all | department | team | self. */
  row_scope: string
  /** dataset key -> the fields of that dataset granted to this role. */
  fields: Record<string, string[]>
  /**
   * dataset key -> the fields the role does not actually get. Wider than the
   * ungranted ones: a granted column lands here too when it reconstructs an
   * ungranted one, so a tick is not on its own proof the column comes back.
   */
  fields_withheld: Record<string, string[]>
}

export interface ToolSummary {
  name: string
  domain: string
  description: string
  required_permission: string
  mutates: boolean
  /** False for the tools that manage RBAC itself; those can never be granted. */
  configurable: boolean
  roles_with_access: string[]
}

export interface ModelSummary {
  key: string
  label: string
  provider: string
  blurb: string
  /** False when the server has no credentials for that provider. */
  available: boolean
  roles_with_access: string[]
}

export interface FieldSummary {
  key: string
  label: string
  /** Identity columns that are always returned and cannot be withheld. */
  locked: boolean
}

export interface DatasetSummary {
  key: string
  label: string
  blurb: string
  required_permission: string
  tool: string
  fields: FieldSummary[]
  roles_with_access: string[]
}

export interface ScopeOption {
  key: string
  description: string
}

export interface AccessMatrix {
  roles: RoleSummary[]
  tools: ToolSummary[]
  models: ModelSummary[]
  datasets: DatasetSummary[]
  scopes: ScopeOption[]
}

export interface AccessChangeResponse {
  changed: boolean
  message: string
  matrix: AccessMatrix
}
