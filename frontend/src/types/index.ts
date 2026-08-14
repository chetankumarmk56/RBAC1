export interface UserInfo {
  email: string
  full_name: string
  role: string
  permissions: string[]
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
  steps: string[]
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

export type Stage = 'planner' | 'agent' | 'tool' | 'rbac' | 'database' | 'compose' | 'error'

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

export interface AccessMatrix {
  roles: RoleSummary[]
  tools: ToolSummary[]
}

export interface AccessChangeResponse {
  changed: boolean
  message: string
  matrix: AccessMatrix
}
