import { useCallback, useEffect, useState } from 'react'

import Sidebar, { type Page } from './components/Sidebar'
import AccessControlPage from './pages/AccessControlPage'
import ChatPage from './pages/ChatPage'
import LoginPage from './pages/LoginPage'
import {
  clearToken,
  deleteConversation,
  fetchConversations,
  fetchMe,
  getToken,
} from './services/api'
import type { ConversationSummary, UserInfo } from './types'

/** Only holders of this permission see — or can reach — the access console. */
const MANAGE_ACCESS = 'permissions:write'

const PAGE_META: Record<Page, { title: string; sub: string }> = {
  chat: { title: 'Chat', sub: 'Planner · role agent · tool · RBAC · PostgreSQL' },
  access: { title: 'Access control', sub: 'Which roles may use which tools' },
}

export default function App() {
  const [user, setUser] = useState<UserInfo | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [page, setPage] = useState<Page>('chat')
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  // null means "new chat" — no row exists until the first message is sent.
  const [conversationId, setConversationId] = useState<number | null>(null)

  // Restore a session from a stored token; the backend re-resolves the role.
  useEffect(() => {
    if (!getToken()) {
      setRestoring(false)
      return
    }
    fetchMe()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setRestoring(false))
  }, [])

  const refreshConversations = useCallback(() => {
    fetchConversations()
      .then(setConversations)
      .catch(() => setConversations([]))
  }, [])

  useEffect(() => {
    if (user) refreshConversations()
  }, [user, refreshConversations])

  const logout = useCallback(() => {
    clearToken()
    setUser(null)
    setPage('chat')
    setConversations([])
    setConversationId(null)
  }, [])

  const newChat = useCallback(() => {
    setConversationId(null)
    setPage('chat')
  }, [])

  const selectConversation = useCallback((id: number) => {
    setConversationId(id)
    setPage('chat')
  }, [])

  const conversationStarted = useCallback(
    (id: number) => {
      setConversationId(id)
      refreshConversations()
    },
    [refreshConversations],
  )

  const removeConversation = useCallback(
    (id: number) => {
      deleteConversation(id)
        .catch(() => undefined)
        .finally(() => {
          setConversationId((current) => (current === id ? null : current))
          refreshConversations()
        })
    },
    [refreshConversations],
  )

  if (restoring) {
    return (
      <div className="boot">
        <span className="brand-mark" aria-hidden="true">
          R
        </span>
      </div>
    )
  }

  if (!user) {
    return <LoginPage onSignedIn={setUser} />
  }

  const canManageAccess = user.permissions.includes(MANAGE_ACCESS)
  // Guard against a stale page after a role change or a hand-edited state.
  const activePage: Page = page === 'access' && !canManageAccess ? 'chat' : page
  const meta = PAGE_META[activePage]

  return (
    <div className="app">
      <Sidebar
        user={user}
        page={activePage}
        canManageAccess={canManageAccess}
        conversations={conversations}
        activeConversationId={conversationId}
        onNavigate={setPage}
        onNewChat={newChat}
        onSelectConversation={selectConversation}
        onDeleteConversation={removeConversation}
        onLogout={logout}
      />

      <div className="content">
        <header className="topbar">
          <h1>{meta.title}</h1>
          <p>{meta.sub}</p>
        </header>

        {activePage === 'chat' ? (
          <ChatPage
            user={user}
            conversationId={conversationId}
            onConversationStarted={conversationStarted}
            onConversationUpdated={refreshConversations}
            onUnauthorized={logout}
          />
        ) : (
          <AccessControlPage onUnauthorized={logout} />
        )}
      </div>
    </div>
  )
}
