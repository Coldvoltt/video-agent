import { useState } from 'react';
import { processVideoUrl, processVideoUpload } from '../api/videoApi';

function VideoProcessor({ userId, onSessionCreated, onError }) {
  const [videoUrl, setVideoUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState('url'); // 'url' or 'upload'

  const handleProcessUrl = async (e) => {
    e.preventDefault();
    if (!videoUrl.trim()) return;

    setLoading(true);
    onError(null);

    try {
      const result = await processVideoUrl(userId, videoUrl);
      onSessionCreated({
        session_id: result.session_id,
        title: result.title,
        duration: result.duration,
        source: 'youtube',
        message: result.message,
      });
      setVideoUrl('');
    } catch (err) {
      onError(err.response?.data?.detail || 'Failed to process video');
    } finally {
      setLoading(false);
    }
  };

  const handleFileUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    setLoading(true);
    onError(null);

    try {
      const result = await processVideoUpload(userId, file);
      onSessionCreated({
        session_id: result.session_id,
        title: result.title,
        duration: result.duration,
        source: 'local',
        message: result.message,
      });
    } catch (err) {
      onError(err.response?.data?.detail || 'Failed to upload video');
    } finally {
      setLoading(false);
      e.target.value = '';
    }
  };

  return (
    <div className="section">
      <h3 className="section-title">Process Video</h3>

      <div className="mode-toggle">
        <button
          className={`btn btn-sm ${mode === 'url' ? 'active' : 'btn-secondary'}`}
          onClick={() => setMode('url')}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M22.54 6.42a2.78 2.78 0 0 0-1.94-2C18.88 4 12 4 12 4s-6.88 0-8.6.46a2.78 2.78 0 0 0-1.94 2A29 29 0 0 0 1 11.75a29 29 0 0 0 .46 5.33A2.78 2.78 0 0 0 3.4 19c1.72.46 8.6.46 8.6.46s6.88 0 8.6-.46a2.78 2.78 0 0 0 1.94-2 29 29 0 0 0 .46-5.25 29 29 0 0 0-.46-5.33z"></path>
            <polygon points="9.75 15.02 15.5 11.75 9.75 8.48 9.75 15.02"></polygon>
          </svg>
          YouTube
        </button>
        <button
          className={`btn btn-sm ${mode === 'upload' ? 'active' : 'btn-secondary'}`}
          onClick={() => setMode('upload')}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="17 8 12 3 7 8"></polyline>
            <line x1="12" y1="3" x2="12" y2="15"></line>
          </svg>
          Upload
        </button>
      </div>

      {mode === 'url' ? (
        <form onSubmit={handleProcessUrl}>
          <div className="input-group">
            <input
              type="text"
              placeholder="Paste YouTube URL here..."
              value={videoUrl}
              onChange={(e) => setVideoUrl(e.target.value)}
              disabled={loading}
            />
          </div>
          <button
            type="submit"
            className="btn btn-primary btn-full"
            disabled={loading || !videoUrl.trim()}
          >
            {loading ? (
              <>
                <span className="btn-spinner"></span>
                Processing...
              </>
            ) : (
              <>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
                Process Video
              </>
            )}
          </button>
        </form>
      ) : (
        <div>
          <label className="file-upload-area">
            <input
              type="file"
              accept="video/*"
              onChange={handleFileUpload}
              disabled={loading}
            />
            {loading ? (
              <div className="file-upload-content">
                <div className="loading-spinner" style={{ width: 32, height: 32 }}></div>
                <span>Processing video...</span>
              </div>
            ) : (
              <div className="file-upload-content">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                  <polyline points="17 8 12 3 7 8"></polyline>
                  <line x1="12" y1="3" x2="12" y2="15"></line>
                </svg>
                <span>Click to upload video</span>
                <span className="file-upload-hint">MP4, MOV, AVI, etc.</span>
              </div>
            )}
          </label>
        </div>
      )}
    </div>
  );
}

export default VideoProcessor;
