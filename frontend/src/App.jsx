import { useState, useEffect } from 'react';
import VideoProcessor from './components/VideoProcessor';
import SessionList from './components/SessionList';
import ChatInterface from './components/ChatInterface';
import SearchInterface from './components/SearchInterface';
import HelperDocument from './components/HelperDocument';
import Transcript from './components/Transcript';
import SnippetCreator from './components/SnippetCreator';
import { listSessions, checkHealth } from './api/videoApi';

function App() {
  const [userId] = useState(() => {
    const stored = localStorage.getItem('userId');
    if (stored) return stored;
    const newId = 'user_' + Math.random().toString(36).substr(2, 9);
    localStorage.setItem('userId', newId);
    return newId;
  });

  const [sessions, setSessions] = useState([]);
  const [currentSession, setCurrentSession] = useState(null);
  const [activeTab, setActiveTab] = useState('chat');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [apiHealthy, setApiHealthy] = useState(false);

  useEffect(() => {
    checkApiHealth();
    loadSessions();
  }, []);

  const checkApiHealth = async () => {
    try {
      const health = await checkHealth();
      setApiHealthy(health.status === 'healthy');
    } catch (err) {
      setApiHealthy(false);
      setError('Cannot connect to API. Make sure the backend is running.');
    }
  };

  const loadSessions = async () => {
    try {
      const data = await listSessions(userId);
      setSessions(data.sessions || []);
    } catch (err) {
      console.error('Failed to load sessions:', err);
    }
  };

  const handleSessionCreated = (session) => {
    setSessions((prev) => [session, ...prev]);
    setCurrentSession(session);
    setActiveTab('chat');
  };

  const handleSessionDeleted = (sessionId) => {
    setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
    if (currentSession?.session_id === sessionId) {
      setCurrentSession(null);
    }
  };

  const tabs = [
    { id: 'chat', label: 'Chat' },
    { id: 'search', label: 'Search' },
    { id: 'document', label: 'Helper Doc' },
    { id: 'transcript', label: 'Transcript' },
    { id: 'snippets', label: 'Snippets' },
  ];

  return (
    <div className="app">
      <header className="header">
        <h1>Divverse Video Agent</h1>
        <p>AI-powered video intelligence</p>
      </header>

      {error && <div className="error">{error}</div>}

      {!apiHealthy && (
        <div className="error">
          API is not connected. Start the backend with: <code>python api.py</code>
        </div>
      )}

      <div className="main-content">
        <aside className="sidebar">
          <VideoProcessor
            userId={userId}
            onSessionCreated={handleSessionCreated}
            onError={setError}
          />

          <div className="section">
            <h3 className="section-title">Your Sessions</h3>
            <SessionList
              sessions={sessions}
              currentSession={currentSession}
              onSelectSession={setCurrentSession}
              onDeleteSession={handleSessionDeleted}
              userId={userId}
            />
          </div>
        </aside>

        <main className="content">
          {currentSession ? (
            <div className="fade-in">
              <div className="content-header">
                <h2>{currentSession.title}</h2>
                <div className="meta">
                  <span>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10"></circle>
                      <polyline points="12 6 12 12 16 14"></polyline>
                    </svg>
                    {formatDuration(currentSession.duration)}
                  </span>
                  <span>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                      <polyline points="22 4 12 14.01 9 11.01"></polyline>
                    </svg>
                    {currentSession.source === 'youtube' ? 'YouTube' : 'Local File'}
                  </span>
                </div>
                {currentSession.message && (
                  <div className="session-message">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10"></circle>
                      <line x1="12" y1="16" x2="12" y2="12"></line>
                      <line x1="12" y1="8" x2="12.01" y2="8"></line>
                    </svg>
                    {currentSession.message}
                  </div>
                )}
              </div>

              <div className="tabs">
                {tabs.map((tab) => (
                  <button
                    key={tab.id}
                    className={`tab ${activeTab === tab.id ? 'active' : ''}`}
                    onClick={() => setActiveTab(tab.id)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {activeTab === 'chat' && (
                <ChatInterface
                  userId={userId}
                  sessionId={currentSession.session_id}
                />
              )}

              {activeTab === 'search' && (
                <SearchInterface
                  userId={userId}
                  sessionId={currentSession.session_id}
                />
              )}

              {activeTab === 'document' && (
                <HelperDocument
                  userId={userId}
                  sessionId={currentSession.session_id}
                />
              )}

              {activeTab === 'transcript' && (
                <Transcript
                  userId={userId}
                  sessionId={currentSession.session_id}
                />
              )}

              {activeTab === 'snippets' && (
                <SnippetCreator
                  userId={userId}
                  sessionId={currentSession.session_id}
                />
              )}
            </div>
          ) : (
            <div className="empty-state">
              <div className="empty-state-icon">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <polygon points="23 7 16 12 23 17 23 7"></polygon>
                  <rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect>
                </svg>
              </div>
              <h3>No video selected</h3>
              <p>Process a YouTube URL or upload a video to get started</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function formatDuration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (hours > 0) {
    return `${hours}h ${minutes}m ${secs}s`;
  }
  return `${minutes}m ${secs}s`;
}

export default App;
