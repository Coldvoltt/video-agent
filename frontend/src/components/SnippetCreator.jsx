import { useState } from 'react';
import { createSnippetFromQuery, createSnippetFromTimestamp, getSnippetUrl } from '../api/videoApi';

function SnippetCreator({ userId, sessionId }) {
  const [mode, setMode] = useState('query'); // 'query' or 'timestamp'
  const [query, setQuery] = useState('');
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [maxDuration, setMaxDuration] = useState(60);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const handleQuerySnippet = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const data = await createSnippetFromQuery(userId, sessionId, query, maxDuration);
      setResult(data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create snippet');
    } finally {
      setLoading(false);
    }
  };

  const handleTimestampSnippet = async (e) => {
    e.preventDefault();
    if (!startTime || !endTime) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const data = await createSnippetFromTimestamp(
        userId,
        sessionId,
        parseFloat(startTime),
        parseFloat(endTime)
      );
      setResult(data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create snippet');
    } finally {
      setLoading(false);
    }
  };

  const renderLocalSnippet = (snippet, index) => {
    const videoUrl = getSnippetUrl(userId, snippet.snippet_path);

    return (
      <div key={index} className="snippet-card" style={{ marginBottom: 20, padding: 15, background: '#f8f9fa', borderRadius: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <strong>Snippet {snippet.index || index + 1}</strong>
          {snippet.relevance != null && (
            <span style={{ color: '#888', fontSize: 12 }}>
              Relevance: {(snippet.relevance * 100).toFixed(0)}%
            </span>
          )}
        </div>
        {snippet.context && (
          <p style={{ fontSize: 14, color: '#666', margin: '10px 0' }}>{snippet.context}</p>
        )}
        <div style={{ borderRadius: 8, overflow: 'hidden', marginBottom: 12, background: '#000' }}>
          <video
            controls
            preload="metadata"
            style={{ width: '100%', display: 'block', maxHeight: 400 }}
          >
            <source src={videoUrl} type="video/mp4" />
            Your browser does not support the video tag.
          </video>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: '#888' }}>
            {formatTime(snippet.start_time)} - {formatTime(snippet.end_time)}
          </span>
          <a
            href={videoUrl}
            download
            className="btn btn-secondary"
            style={{ fontSize: 13, padding: '6px 14px', textDecoration: 'none' }}
          >
            Download Snippet
          </a>
        </div>
      </div>
    );
  };

  return (
    <div>
      <div style={{ display: 'flex', gap: 10, marginBottom: 20 }}>
        <button
          className={`btn ${mode === 'query' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => { setMode('query'); setResult(null); }}
        >
          By Content Query
        </button>
        <button
          className={`btn ${mode === 'timestamp' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => { setMode('timestamp'); setResult(null); }}
        >
          By Timestamp
        </button>
      </div>

      {mode === 'query' ? (
        <form onSubmit={handleQuerySnippet}>
          <div className="input-group">
            <label>What topic do you want a snippet of?</label>
            <input
              type="text"
              placeholder="e.g., introduction, main argument, conclusion..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={loading}
            />
          </div>
          <div className="input-group">
            <label>Max duration (seconds)</label>
            <input
              type="number"
              value={maxDuration}
              onChange={(e) => setMaxDuration(parseInt(e.target.value) || 60)}
              min={10}
              max={300}
              disabled={loading}
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={loading || !query.trim()}>
            {loading ? 'Creating...' : 'Create Snippets'}
          </button>
        </form>
      ) : (
        <form onSubmit={handleTimestampSnippet}>
          <div style={{ display: 'flex', gap: 15 }}>
            <div className="input-group" style={{ flex: 1 }}>
              <label>Start time (seconds)</label>
              <input
                type="number"
                placeholder="0"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                min={0}
                step={0.1}
                disabled={loading}
              />
            </div>
            <div className="input-group" style={{ flex: 1 }}>
              <label>End time (seconds)</label>
              <input
                type="number"
                placeholder="60"
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
                min={0}
                step={0.1}
                disabled={loading}
              />
            </div>
          </div>
          <button type="submit" className="btn btn-primary" disabled={loading || !startTime || !endTime}>
            {loading ? 'Creating...' : 'Create Snippet'}
          </button>
        </form>
      )}

      {error && <div className="error" style={{ marginTop: 15 }}>{error}</div>}

      {result && (
        <div style={{ marginTop: 20 }}>
          <h3>Snippet{result.snippets?.length > 1 ? 's' : ''} Created</h3>
          <p style={{ color: '#888', fontSize: 14, marginBottom: 15 }}>
            Source: {result.source}
          </p>

          {result.source === 'youtube' && result.snippets ? (
            result.snippets.map((snippet, i) => (
              <div key={i} className="snippet-card" style={{ marginBottom: 20, padding: 15, background: '#f8f9fa', borderRadius: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <strong>Snippet {snippet.index}</strong>
                  <span style={{ color: '#888', fontSize: 12 }}>
                    Relevance: {(snippet.relevance * 100).toFixed(0)}%
                  </span>
                </div>
                <p style={{ fontSize: 14, color: '#666', margin: '10px 0' }}>{snippet.context}</p>
                <div style={{ position: 'relative', paddingBottom: '56.25%', height: 0, marginBottom: 10 }}>
                  <iframe
                    src={snippet.links.embed_url}
                    style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', borderRadius: 6 }}
                    frameBorder="0"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    allowFullScreen
                    title={`Snippet ${snippet.index}`}
                  />
                </div>
                <div className="snippet-links">
                  <a href={snippet.links.watch_url} target="_blank" rel="noopener noreferrer">
                    Watch on YouTube ({snippet.links.timestamp_display})
                  </a>
                  <a href={snippet.links.short_url} target="_blank" rel="noopener noreferrer">
                    Short URL
                  </a>
                </div>
              </div>
            ))
          ) : result.source === 'youtube' && result.links ? (
            <div className="snippet-card" style={{ padding: 15, background: '#f8f9fa', borderRadius: 8 }}>
              <div style={{ position: 'relative', paddingBottom: '56.25%', height: 0, marginBottom: 10 }}>
                <iframe
                  src={result.links.embed_url}
                  style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', borderRadius: 6 }}
                  frameBorder="0"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  allowFullScreen
                  title="Video snippet"
                />
              </div>
              <div className="snippet-links">
                <a href={result.links.watch_url} target="_blank" rel="noopener noreferrer">
                  Watch on YouTube ({result.links.timestamp_display})
                </a>
                <a href={result.links.short_url} target="_blank" rel="noopener noreferrer">
                  Short URL
                </a>
              </div>
            </div>
          ) : result.snippets ? (
            result.snippets.map((snippet, i) => renderLocalSnippet(snippet, i))
          ) : result.snippet_path ? (
            renderLocalSnippet(result, 0)
          ) : null}
        </div>
      )}
    </div>
  );
}

function formatTime(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export default SnippetCreator;
