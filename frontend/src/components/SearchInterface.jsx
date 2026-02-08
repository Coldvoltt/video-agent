import { useState } from 'react';
import { searchVideo } from '../api/videoApi';

function SearchInterface({ userId, sessionId }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [searched, setSearched] = useState(false);

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError(null);
    setSearched(true);

    try {
      const data = await searchVideo(userId, sessionId, query);
      setResults(data.results || []);
    } catch (err) {
      setError(err.response?.data?.detail || 'Search failed');
      setResults([]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <form onSubmit={handleSearch} className="search-form">
        <div className="input-group" style={{ marginBottom: 0, flex: 1 }}>
          <input
            type="text"
            placeholder="Search the transcript..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={loading}
          />
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading || !query.trim()}>
          {loading ? (
            <>
              <span className="btn-spinner"></span>
              Searching...
            </>
          ) : (
            <>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              </svg>
              Search
            </>
          )}
        </button>
      </form>

      {error && <div className="error" style={{ marginTop: 16 }}>{error}</div>}

      <div className="search-results">
        {results.length === 0 && !loading && searched && (
          <div className="empty-state" style={{ padding: 40 }}>
            <p>No results found for "{query}"</p>
            <p style={{ fontSize: 12, marginTop: 8 }}>Try different keywords or phrases</p>
          </div>
        )}

        {results.length > 0 && (
          <div style={{ marginBottom: 16, fontSize: 14, color: 'var(--text-tertiary)' }}>
            Found {results.length} result{results.length !== 1 ? 's' : ''} for "{query}"
          </div>
        )}

        {results.map((result, i) => (
          <div key={i} className="result-item">
            <div className="timestamp">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginRight: 6 }}>
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
              </svg>
              {formatTime(result.start)} - {formatTime(result.end)}
            </div>
            <div className="text">{result.text}</div>
            <div className="relevance">
              <span>Relevance:</span>
              <div className="relevance-bar">
                <div
                  className="relevance-bar-fill"
                  style={{ width: `${result.relevance * 100}%` }}
                ></div>
              </div>
              <span>{(result.relevance * 100).toFixed(0)}%</span>
            </div>
          </div>
        ))}
      </div>
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

export default SearchInterface;
