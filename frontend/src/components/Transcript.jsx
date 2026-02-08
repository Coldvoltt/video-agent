import { useState, useEffect } from 'react';
import { getTranscript } from '../api/videoApi';

function Transcript({ userId, sessionId }) {
  const [transcript, setTranscript] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showTimestamps, setShowTimestamps] = useState(true);

  useEffect(() => {
    loadTranscript();
  }, [sessionId]);

  const loadTranscript = async () => {
    setLoading(true);
    setError(null);

    try {
      const data = await getTranscript(sessionId, userId, true);
      setTranscript(data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load transcript');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="loading">
        <div className="loading-spinner"></div>
        <span className="loading-text">Loading transcript...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <div className="error">{error}</div>
        <button onClick={loadTranscript} className="btn btn-secondary">
          Try Again
        </button>
      </div>
    );
  }

  if (!transcript) {
    return null;
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 15 }}>
        <div>
          <span style={{ color: '#888', fontSize: 14 }}>
            Language: {transcript.language || 'Unknown'} |{' '}
            Duration: {formatDuration(transcript.duration)}
          </span>
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 14 }}>
          <input
            type="checkbox"
            checked={showTimestamps}
            onChange={(e) => setShowTimestamps(e.target.checked)}
          />
          Show timestamps
        </label>
      </div>

      <div style={{ maxHeight: 500, overflowY: 'auto' }}>
        {transcript.segments.map((segment, i) => (
          <div key={i} className="transcript-segment">
            {showTimestamps && (
              <div className="time">
                {formatTime(segment.start)} - {formatTime(segment.end)}
              </div>
            )}
            <div className="text">{segment.text}</div>
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

function formatDuration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}h ${mins}m ${secs}s`;
  }
  return `${mins}m ${secs}s`;
}

export default Transcript;
