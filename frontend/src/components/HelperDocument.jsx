import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getHelperDocument, exportHelperDocumentPdf } from '../api/videoApi';

function HelperDocument({ userId, sessionId }) {
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showPdfModal, setShowPdfModal] = useState(false);
  const [selectedSections, setSelectedSections] = useState([]);
  const [pdfLoading, setPdfLoading] = useState(false);

  // ── Per-session cache ──
  const cache = useRef({});
  const prevSession = useRef(sessionId);

  useEffect(() => {
    if (prevSession.current !== sessionId) {
      // Save old session state (doc still holds old value in this render)
      cache.current[prevSession.current] = doc;
      // Restore new session state
      const saved = cache.current[sessionId];
      setDoc(saved || null);
      setError(null);
      setShowPdfModal(false);
      prevSession.current = sessionId;
    }
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-generate on mount / session change when no cached data
  useEffect(() => {
    if (!doc && !loading && !cache.current[sessionId]) {
      loadDocument();
    }
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const getAllSectionIds = () => {
    if (!doc) return [];
    const ids = [];
    if (doc.overview) ids.push('overview');
    doc.key_points.forEach((_, i) => ids.push(`key_point_${i}`));
    if (doc.action_items && doc.action_items.length > 0) ids.push('action_items');
    return ids;
  };

  const openPdfModal = () => {
    setSelectedSections(getAllSectionIds());
    setShowPdfModal(true);
  };

  const toggleSection = (sectionId) => {
    setSelectedSections((prev) =>
      prev.includes(sectionId)
        ? prev.filter((s) => s !== sectionId)
        : [...prev, sectionId]
    );
  };

  const toggleAll = () => {
    const all = getAllSectionIds();
    if (selectedSections.length === all.length) {
      setSelectedSections([]);
    } else {
      setSelectedSections(all);
    }
  };

  const handleExportPdf = async () => {
    console.log('[PDF] handleExportPdf called');
    console.log('[PDF] selectedSections:', selectedSections);
    console.log('[PDF] userId:', userId, 'sessionId:', sessionId);
    if (selectedSections.length === 0) {
      console.log('[PDF] No sections selected, aborting');
      return;
    }
    setPdfLoading(true);
    setError(null);

    try {
      console.log('[PDF] Sending request...');
      const blob = await exportHelperDocumentPdf(userId, sessionId, doc, selectedSections);
      console.log('[PDF] Response received, blob size:', blob?.size, 'type:', blob?.type);

      // Axios with responseType:'blob' returns a Blob on success,
      // but if the server returned an error JSON as a blob, detect it.
      if (blob.type && blob.type.includes('application/json')) {
        const text = await blob.text();
        const json = JSON.parse(text);
        throw new Error(json.detail || 'Server returned an error');
      }

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${doc.title || 'helper_document'}.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setShowPdfModal(false);
    } catch (err) {
      console.error('PDF export failed:', err);
      let message = 'Failed to generate PDF';
      if (err.response?.data instanceof Blob) {
        try {
          const text = await err.response.data.text();
          const json = JSON.parse(text);
          message = json.detail || message;
        } catch {}
      } else if (err.response?.data?.detail) {
        message = err.response.data.detail;
      } else if (err.message) {
        message = err.message;
      }
      setError(message);
      setShowPdfModal(false);
    } finally {
      setPdfLoading(false);
    }
  };

  if (loading || (!doc && !error)) {
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
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={openPdfModal} className="btn btn-primary btn-sm">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="12" y1="18" x2="12" y2="12"></line>
              <polyline points="9 15 12 12 15 15"></polyline>
            </svg>
            Save as PDF
          </button>
          <button onClick={loadDocument} className="btn btn-secondary btn-sm">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="23 4 23 10 17 10"></polyline>
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
            </svg>
            Regenerate
          </button>
        </div>
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

      {/* PDF Section Selection Modal — portaled to body for correct fixed positioning */}
      {showPdfModal && createPortal(
        <div className="pdf-modal-overlay" onClick={() => setShowPdfModal(false)}>
          <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
            <div className="pdf-modal-header">
              <h3 style={{ margin: 0, fontSize: '1.1rem' }}>Save as PDF</h3>
              <button
                onClick={() => setShowPdfModal(false)}
                className="pdf-modal-close"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18"></line>
                  <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
              </button>
            </div>

            <p style={{ color: 'var(--text-secondary)', fontSize: 14, marginBottom: 16 }}>
              Select the sections you want to include in the PDF:
            </p>

            <div className="pdf-section-list">
              <label className="pdf-section-item pdf-select-all">
                <input
                  type="checkbox"
                  checked={selectedSections.length === getAllSectionIds().length}
                  onChange={toggleAll}
                />
                <span style={{ fontWeight: 600 }}>Select All</span>
              </label>

              <div className="pdf-section-divider" />

              {doc.overview && (
                <label className="pdf-section-item">
                  <input
                    type="checkbox"
                    checked={selectedSections.includes('overview')}
                    onChange={() => toggleSection('overview')}
                  />
                  <span>Overview</span>
                </label>
              )}

              {doc.key_points.map((kp, i) => (
                <label key={i} className="pdf-section-item">
                  <input
                    type="checkbox"
                    checked={selectedSections.includes(`key_point_${i}`)}
                    onChange={() => toggleSection(`key_point_${i}`)}
                  />
                  <span>
                    {i + 1}. {kp.title}
                    <span className={`importance-badge ${kp.importance}`} style={{ marginLeft: 8, fontSize: 10 }}>
                      {kp.importance}
                    </span>
                  </span>
                </label>
              ))}

              {doc.action_items && doc.action_items.length > 0 && (
                <label className="pdf-section-item">
                  <input
                    type="checkbox"
                    checked={selectedSections.includes('action_items')}
                    onChange={() => toggleSection('action_items')}
                  />
                  <span>Action Items ({doc.action_items.length})</span>
                </label>
              )}
            </div>

            <div className="pdf-modal-footer">
              <button
                onClick={() => setShowPdfModal(false)}
                className="btn btn-secondary btn-sm"
              >
                Cancel
              </button>
              <button
                onClick={handleExportPdf}
                className="btn btn-primary btn-sm"
                disabled={selectedSections.length === 0 || pdfLoading}
              >
                {pdfLoading ? (
                  <>
                    <div className="btn-spinner"></div>
                    Generating PDF — this may take a moment...
                  </>
                ) : (
                  <>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                      <polyline points="7 10 12 15 17 10"></polyline>
                      <line x1="12" y1="15" x2="12" y2="3"></line>
                    </svg>
                    Download PDF ({selectedSections.length} section{selectedSections.length !== 1 ? 's' : ''})
                  </>
                )}
              </button>
            </div>
          </div>
        </div>,
        document.body
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
