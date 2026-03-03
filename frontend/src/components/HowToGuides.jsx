import { useState, useEffect, useRef } from 'react';
import { getHowToGuides, getCustomHowToGuide } from '../api/videoApi';

function HowToGuides({ userId, sessionId }) {
  const [guides, setGuides] = useState(null);
  const [customGuides, setCustomGuides] = useState([]);
  const [expandedGuides, setExpandedGuides] = useState({});
  const [loading, setLoading] = useState(false);
  const [customLoading, setCustomLoading] = useState(false);
  const [error, setError] = useState(null);
  const [customQuery, setCustomQuery] = useState('');

  // Per-session cache (same pattern as HelperDocument)
  const cache = useRef({});
  const prevSession = useRef(sessionId);

  useEffect(() => {
    if (prevSession.current !== sessionId) {
      cache.current[prevSession.current] = { guides, customGuides, expandedGuides };
      const saved = cache.current[sessionId];
      if (saved) {
        setGuides(saved.guides);
        setCustomGuides(saved.customGuides);
        setExpandedGuides(saved.expandedGuides);
      } else {
        setGuides(null);
        setCustomGuides([]);
        setExpandedGuides({});
      }
      setError(null);
      setCustomQuery('');
      prevSession.current = sessionId;
    }
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-generate on mount / session change when no cached data
  useEffect(() => {
    if (!guides && !loading && !cache.current[sessionId]) {
      loadGuides();
    }
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadGuides = async () => {
    setLoading(true);
    setError(null);

    try {
      const data = await getHowToGuides(sessionId, userId);
      setGuides(data.guides || []);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to generate guides');
    } finally {
      setLoading(false);
    }
  };

  const handleCustomQuery = async (e) => {
    e.preventDefault();
    if (!customQuery.trim() || customLoading) return;

    setCustomLoading(true);
    setError(null);

    try {
      const data = await getCustomHowToGuide(userId, sessionId, customQuery.trim());
      const newGuide = {
        ...data.guide,
        confidence: data.confidence,
        note: data.note,
        isCustom: true,
        query: customQuery.trim(),
      };
      setCustomGuides((prev) => [newGuide, ...prev]);
      setExpandedGuides((prev) => ({ ...prev, [`custom_${customGuides.length}`]: true }));
      setCustomQuery('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to generate custom guide');
    } finally {
      setCustomLoading(false);
    }
  };

  const toggleGuide = (key) => {
    setExpandedGuides((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  // Loading state
  if (loading || (!guides && !error)) {
    return (
      <div className="loading">
        <div className="loading-spinner"></div>
        <span className="loading-text">Extracting how-to guides...</span>
        <p style={{ fontSize: 12, marginTop: 8, color: 'var(--text-tertiary)' }}>
          Analyzing the transcript for actionable procedures
        </p>
      </div>
    );
  }

  // Error state
  if (error && !guides) {
    return (
      <div>
        <div className="error">{error}</div>
        <button onClick={loadGuides} className="btn btn-secondary">
          Try Again
        </button>
      </div>
    );
  }

  return (
    <div className="howto-container fade-in">
      {/* Header with regenerate */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: '1.25rem' }}>How-To Guides</h2>
        <button onClick={loadGuides} className="btn btn-secondary btn-sm">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="23 4 23 10 17 10" />
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
          </svg>
          Regenerate
        </button>
      </div>

      {/* Custom query form */}
      <form onSubmit={handleCustomQuery} className="howto-custom-form">
        <input
          type="text"
          value={customQuery}
          onChange={(e) => setCustomQuery(e.target.value)}
          placeholder="Ask for a specific guide, e.g. 'How do I configure the database?'"
          disabled={customLoading}
        />
        <button
          type="submit"
          className="btn btn-primary btn-sm"
          disabled={!customQuery.trim() || customLoading}
        >
          {customLoading ? (
            <div className="btn-spinner"></div>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          )}
        </button>
      </form>

      {error && <div className="error" style={{ marginBottom: 16 }}>{error}</div>}

      {/* Custom guides */}
      {customGuides.map((guide, i) => {
        const key = `custom_${i}`;
        const isExpanded = expandedGuides[key];
        return (
          <div key={key} className="howto-accordion" style={{ marginBottom: 12 }}>
            <button className="howto-accordion-header" onClick={() => toggleGuide(key)}>
              <div className="howto-accordion-header-content">
                <div className="howto-accordion-title-row">
                  <span className="howto-custom-badge">Custom</span>
                  <h3>{guide.title}</h3>
                </div>
                <p className="howto-accordion-desc">{guide.description}</p>
                <div className="howto-accordion-meta">
                  <span>{guide.steps?.length || 0} steps</span>
                  {guide.confidence && (
                    <span className={`howto-confidence howto-confidence-${guide.confidence}`}>
                      {guide.confidence} confidence
                    </span>
                  )}
                </div>
              </div>
              <svg
                className={`howto-chevron ${isExpanded ? 'howto-chevron-open' : ''}`}
                width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>
            {isExpanded && (
              <div className="howto-accordion-body">
                {guide.note && (
                  <div className="howto-note">{guide.note}</div>
                )}
                {guide.steps && guide.steps.length > 0 ? (
                  <ol className="howto-steps">
                    {guide.steps.map((step, si) => (
                      <li key={si} className="howto-step">
                        <div className="howto-step-number">{step.step_number || si + 1}</div>
                        <div className="howto-step-content">
                          <div className="howto-step-instruction">{step.instruction}</div>
                          {step.detail && (
                            <div className="howto-step-detail">{step.detail}</div>
                          )}
                          {step.timestamp != null && (
                            <span className="howto-step-timestamp">
                              {formatTime(step.timestamp)}
                            </span>
                          )}
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                    No steps could be extracted for this topic.
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* Auto-generated guides */}
      {guides && guides.map((guide, i) => {
        const key = `auto_${i}`;
        const isExpanded = expandedGuides[key];
        return (
          <div key={key} className="howto-accordion" style={{ marginBottom: 12 }}>
            <button className="howto-accordion-header" onClick={() => toggleGuide(key)}>
              <div className="howto-accordion-header-content">
                <h3>{guide.title}</h3>
                <p className="howto-accordion-desc">{guide.description}</p>
                <div className="howto-accordion-meta">
                  <span>{guide.steps?.length || 0} steps</span>
                  {guide.timestamp_start != null && (
                    <span className="howto-accordion-time">
                      {formatTime(guide.timestamp_start)}
                      {guide.timestamp_end != null && ` - ${formatTime(guide.timestamp_end)}`}
                    </span>
                  )}
                </div>
              </div>
              <svg
                className={`howto-chevron ${isExpanded ? 'howto-chevron-open' : ''}`}
                width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>
            {isExpanded && (
              <div className="howto-accordion-body">
                <ol className="howto-steps">
                  {guide.steps.map((step, si) => (
                    <li key={si} className="howto-step">
                      <div className="howto-step-number">{step.step_number || si + 1}</div>
                      <div className="howto-step-content">
                        <div className="howto-step-instruction">{step.instruction}</div>
                        {step.detail && (
                          <div className="howto-step-detail">{step.detail}</div>
                        )}
                        {step.timestamp != null && (
                          <span className="howto-step-timestamp">
                            {formatTime(step.timestamp)}
                          </span>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        );
      })}

      {guides && guides.length === 0 && customGuides.length === 0 && (
        <div className="empty-state" style={{ padding: '40px 20px' }}>
          <p>No how-to guides could be extracted from this video.</p>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>
            Try asking for a specific guide using the input above.
          </p>
        </div>
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

export default HowToGuides;
