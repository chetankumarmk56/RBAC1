import { roleLabel } from '../labels'
import type { ConversationSummary, UserInfo } from '../types'

export type Page = 'chat' | 'access'

interface Props {
  user: UserInfo
  page: Page
  canManageAccess: boolean
  conversations: ConversationSummary[]
  activeConversationId: number | null
  onNavigate: (page: Page) => void
  onNewChat: () => void
  onSelectConversation: (id: number) => void
  onDeleteConversation: (id: number) => void
  onLogout: () => void
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" aria-hidden="true">
      <path d="M10 4.5v11M4.5 10h11" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

function ShieldIcon() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
      <path
        d="M10 2.5 16 5v5c0 3.4-2.4 6.4-6 7.5-3.6-1.1-6-4.1-6-7.5V5l6-2.5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path
        d="m7.4 9.9 1.9 1.9 3.4-3.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 20 20" width="13" height="13" aria-hidden="true">
      <path
        d="M4.5 6h11M8 6V4.5h4V6M6 6l.6 9.5h6.8L14 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export default function Sidebar({
  user,
  page,
  canManageAccess,
  conversations,
  activeConversationId,
  onNavigate,
  onNewChat,
  onSelectConversation,
  onDeleteConversation,
  onLogout,
}: Props) {
  const initials = user.full_name
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          R
        </span>
        <span className="brand-text">
          <span className="brand-name">RBAC</span>
          <span className="brand-sub">Agentic access control</span>
        </span>
      </div>

      <button type="button" className="new-chat" onClick={onNewChat}>
        <PlusIcon />
        New chat
      </button>

      <div className="history">
        <p className="history-label">Recent</p>
        {conversations.length === 0 ? (
          <p className="history-empty">No conversations yet</p>
        ) : (
          <ul className="history-list">
            {conversations.map((conversation) => (
              <li key={conversation.id}>
                <button
                  type="button"
                  className={`history-item${
                    page === 'chat' && conversation.id === activeConversationId
                      ? ' history-item-active'
                      : ''
                  }`}
                  onClick={() => onSelectConversation(conversation.id)}
                  title={conversation.title}
                >
                  <span className="history-title">{conversation.title}</span>
                </button>
                <button
                  type="button"
                  className="history-delete"
                  aria-label={`Delete ${conversation.title}`}
                  onClick={() => onDeleteConversation(conversation.id)}
                >
                  <TrashIcon />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Shown only to holders of permissions:write. The API enforces it too —
          the console endpoints return 403 for every other role. */}
      {canManageAccess && (
        <button
          type="button"
          className={`nav-item${page === 'access' ? ' nav-item-active' : ''}`}
          onClick={() => onNavigate('access')}
        >
          <ShieldIcon />
          Access control
        </button>
      )}

      <div className="sidebar-foot">
        <div className="account">
          <span className="avatar" aria-hidden="true">
            {initials}
          </span>
          <span className="account-text">
            <span className="account-name">{user.full_name}</span>
            <span className={`role-badge role-${user.role}`}>{roleLabel(user.role)}</span>
          </span>
        </div>
        <button type="button" className="ghost-button" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </aside>
  )
}
