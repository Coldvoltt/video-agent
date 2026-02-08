import axios from 'axios';

const API_BASE = '/api';

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Video Processing
export const processVideoUrl = async (userId, videoUrl, language = 'en') => {
  const response = await api.post('/process/url', {
    user_id: userId,
    video_url: videoUrl,
    language,
  });
  return response.data;
};

export const processVideoUpload = async (userId, file, language = null) => {
  const formData = new FormData();
  formData.append('file', file);

  const params = new URLSearchParams({ user_id: userId });
  if (language) params.append('language', language);

  const response = await api.post(`/process/upload?${params}`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
};

// Sessions
export const listSessions = async (userId) => {
  const response = await api.get('/sessions', {
    params: { user_id: userId },
  });
  return response.data;
};

export const deleteSession = async (sessionId, userId) => {
  const response = await api.delete(`/sessions/${sessionId}`, {
    params: { user_id: userId },
  });
  return response.data;
};

// Helper Documents
export const getHelperDocument = async (sessionId, userId) => {
  const response = await api.get(`/document/${sessionId}`, {
    params: { user_id: userId },
  });
  return response.data;
};

// Search & Query
export const searchVideo = async (userId, sessionId, query, nResults = 5) => {
  const response = await api.post('/search', {
    user_id: userId,
    session_id: sessionId,
    query,
    n_results: nResults,
  });
  return response.data;
};

export const queryVideo = async (userId, sessionId, conversationId, query) => {
  const response = await api.post('/query', {
    user_id: userId,
    session_id: sessionId,
    conversation_id: conversationId,
    query,
  });
  return response.data;
};

export const getConversationMessages = async (userId, conversationId, limit = 50) => {
  const response = await api.get(`/conversation/${conversationId}/messages`, {
    params: { user_id: userId, limit },
  });
  return response.data;
};

// Snippets
export const createSnippetFromQuery = async (userId, sessionId, query, maxDuration = 60, nResults = 5) => {
  const response = await api.post('/snippet/query', {
    user_id: userId,
    session_id: sessionId,
    query,
    max_duration: maxDuration,
    n_results: nResults,
  });
  return response.data;
};

export const createSnippetFromTimestamp = async (userId, sessionId, startTime, endTime) => {
  const response = await api.post('/snippet/timestamp', {
    user_id: userId,
    session_id: sessionId,
    start_time: startTime,
    end_time: endTime,
  });
  return response.data;
};

// Snippet Download URL
export const getSnippetUrl = (userId, snippetPath) => {
  const filename = snippetPath.split(/[/\\]/).pop();
  return `${API_BASE}/snippet/download/${userId}/${filename}`;
};

// Transcript
export const getTranscript = async (sessionId, userId, withTimestamps = true) => {
  const response = await api.get(`/transcript/${sessionId}`, {
    params: { user_id: userId, with_timestamps: withTimestamps },
  });
  return response.data;
};

// Health
export const checkHealth = async () => {
  const response = await api.get('/health');
  return response.data;
};

export default api;
