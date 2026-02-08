import { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getHelperDocument } from '../api/videoApi';

function HelperDocument({ userId, sessionId }) {
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const loadDocument = async () => {
    setLoading(true);
    setError(null);

    try {
      const data = await getHelperDocument(sessionId, userId);
      setDoc(data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load document');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setDoc(null);
    setError(null);
  }, [sessionId]);

  if (!doc && !loading && !error) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
            <polyline points="14 2 14 8 20 8"></polyline>
            <line x1="16" y1="13" x2="8" y2="13"></line>
            <line x1="16" y1="17" x2="8" y2="17"></line>
            <polyline points="10 9 9 9 8 9"></polyline>
          </svg>
        </div>
        <h3>Generate Helper Document</h3>
        <p>Create an AI-powered document with key points, summaries, and action items</p>
        <button onClick={loadDocument} className="btn btn-primary" style={{ marginTop: 20 }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"></path>
          </svg>
          Generate Document
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="loading">
        <div className="loading-spinner"></div>
        <span className="loading-text">Generating helper document...</span>
        <p style={{ fontSize: 12, marginTop: 8, color: 'var(--text-tertiary)' }}>
          This may take a moment for longer videos
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <div className="error">{error}</div>
        <button onClick={loadDocument} className="btn btn-secondary">
          Try Again
        </button>
      </div>
    );
  }

  return (
    <div className="helper-doc fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <h2 style={{ margin: 0, fontSize: '1.5rem' }}>{doc.title}</h2>
        <button onClick={loadDocument} className="btn btn-secondary btn-sm">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
          </svg>
          Regenerate
        </button>
      </div>

      <h2>Overview</h2>
      <div className="markdown-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {doc.overview}
        </ReactMarkdown>
      </div>

      <h2>Key Points</h2>
      {doc.key_points.map((kp, i) => (
        <div key={i} className="key-point-card">
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
            <h3 style={{ margin: 0, fontSize: '1rem' }}>
              {i + 1}. {kp.title}
            </h3>
            <span className={`importance-badge ${kp.importance}`}>
              {kp.importance}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color: 'var(--accent-primary)' }}>
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: 'var(--accent-primary)' }}>
              {formatTime(kp.timestamp_start)} - {formatTime(kp.timestamp_end)}
            </span>
          </div>
          {kp.screenshot_url && (
            <div style={{ marginBottom: 12 }}>
              <img
                src={kp.screenshot_url}
                alt={`Screenshot at ${formatTime(kp.timestamp_start)}`}
                style={{ width: '100%', borderRadius: 'var(--radius-md)' }}
              />
            </div>
          )}
          <div className="markdown-content" style={{ color: 'var(--text-secondary)' }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {kp.summary}
            </ReactMarkdown>
          </div>
        </div>
      ))}

      {doc.action_items && doc.action_items.length > 0 && (
        <>
          <h2>Action Items</h2>
          <div className="action-items">
            {doc.action_items.map((item, i) => (
              <div key={i} className="action-item">
                <div className="action-item-checkbox">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                    <polyline points="22 4 12 14.01 9 11.01"></polyline>
                  </svg>
                </div>
                <div className="markdown-content">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {item}
                  </ReactMarkdown>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function formatTime(seconds) {
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export default HelperDocument;
