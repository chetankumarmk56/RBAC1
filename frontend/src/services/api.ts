import type {
  AccessChangeResponse,
  AccessMatrix,
  ChatResponse,
  ConversationDetail,
  ConversationSummary,
  LoginResponse,
  StreamEvent,
  UserInfo,
} from '../types'

const TOKEN_KEY = 'rbac_poc_token'

/**
 * Where the API lives. Empty in development, where Vite proxies /api to the
 * backend. Set VITE_API_BASE_URL when the frontend is served from a different
 * origin than the API — e.g. a static host in front of a separate backend.
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

const url = (path: string) => `${API_BASE}${path}`

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const response = await fetch(url(path), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  })

  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // Non-JSON error body; keep the status-based message.
    }
    throw new ApiError(response.status, detail)
  }

  return (await response.json()) as T
}

export function login(email: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export function fetchMe(): Promise<UserInfo> {
  return request<UserInfo>('/api/auth/me')
}

export function sendChat(message: string): Promise<ChatResponse> {
  return request<ChatResponse>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ message }),
  })
}

/**
 * The same pipeline as `sendChat`, narrated over Server-Sent Events.
 * `onEvent` fires once per pipeline stage, then once with the finished answer.
 */
export async function streamChat(
  message: string,
  conversationId: number | null,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const token = getToken()
  const response = await fetch(url('/api/chat/stream'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  })

  if (!response.ok || !response.body) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // Non-JSON error body; keep the status-based message.
    }
    throw new ApiError(response.status, detail)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE frames are separated by a blank line.
    let split = buffer.indexOf('\n\n')
    while (split !== -1) {
      const frame = buffer.slice(0, split)
      buffer = buffer.slice(split + 2)
      const line = frame.split('\n').find((candidate) => candidate.startsWith('data: '))
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(6)) as StreamEvent)
        } catch {
          // Ignore a malformed frame rather than dropping the whole stream.
        }
      }
      split = buffer.indexOf('\n\n')
    }
  }
}

/** Chat history. The API scopes every row to the authenticated user. */
export function fetchConversations(): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>('/api/conversations')
}

export function fetchConversation(id: number): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/api/conversations/${id}`)
}

export async function deleteConversation(id: number): Promise<void> {
  const token = getToken()
  const response = await fetch(url(`/api/conversations/${id}`), {
    method: 'DELETE',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) {
    throw new ApiError(response.status, `Could not delete conversation (${response.status})`)
  }
}

/** Access-control console. The API rejects these with 403 without `permissions:write`. */
export function fetchAccessMatrix(): Promise<AccessMatrix> {
  return request<AccessMatrix>('/api/admin/access-matrix')
}

export function setAccess(
  roleName: string,
  toolName: string,
  granted: boolean,
): Promise<AccessChangeResponse> {
  return request<AccessChangeResponse>('/api/admin/access', {
    method: 'POST',
    body: JSON.stringify({ role_name: roleName, tool_name: toolName, granted }),
  })
}
