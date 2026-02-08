import { deleteSession } from '../api/videoApi';

function SessionList({ sessions, currentSession, onSelectSession, onDeleteSession, userId }) {
  const handleDelete = async (e, sessionId) => {
    e.stopPropagation();
    if (!confirm('Delete this session?')) return;

    try {
      await deleteSession(sessionId, userId);
      onDeleteSession(sessionId);
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  };

  if (sessions.length === 0) {
    return (
      <p style={{ color: '#888', fontSize: 14, textAlign: 'center' }}>
        No sessions yet
      </p>
    );
  }

  return (
    <ul className="session-list">
      {sessions.map((session) => (
        <li
          key={session.session_id}
          className={`session-item ${
            currentSession?.session_id === session.session_id ? 'active' : ''
          }`}
          onClick={() => onSelectSession(session)}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <h4>{session.title}</h4>
              <p>
                {formatDuration(session.duration)} • {session.source}
              </p>
            </div>
            <button
              onClick={(e) => handleDelete(e, session.session_id)}
              style={{
                background: 'none',
                border: 'none',
                color: '#c00',
                cursor: 'pointer',
                padding: '2px 6px',
                fontSize: 12,
              }}
            >
              ✕
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
}

export default SessionList;
