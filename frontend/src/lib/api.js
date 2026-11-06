import axios from 'axios';

export const API_BASE = import.meta.env.VITE_API_URL || '/api';

const http = axios.create({ baseURL: API_BASE });

function getUserId() {
  let id = null;
  try {
    id = localStorage.getItem('userId');
  } catch {
    /* storage unavailable: fall through to a session-only id */
  }
  if (!id || !/^[A-Za-z0-9_.-]{1,64}$/.test(id)) {
    id = 'user_' + Math.random().toString(36).slice(2, 11);
    try {
      localStorage.setItem('userId', id);
    } catch {
      /* ignore */
    }
  }
  return id;
}

export const userId = getUserId();
const withUser = (params = {}) => ({ params: { user_id: userId, ...params } });

/** Human-readable message from an API error. */
export function errorMessage(err) {
  const detail = err?.response?.data?.detail;
  if (Array.isArray(detail)) return detail.map((d) => (typeof d === 'string' ? d : d.msg)).join('; ');
  if (typeof detail === 'string') return detail;
  if (err?.code === 'ERR_NETWORK') return 'Can’t reach the server. Check that the backend is running.';
  return err?.message || 'Something went wrong.';
}

export const api = {
  health: () => http.get('/health').then((r) => r.data),

  listProjects: () => http.get('/v2/projects', withUser()).then((r) => r.data.projects),
  createProject: (name) => http.post('/v2/projects', { name }, withUser()).then((r) => r.data),
  getProject: (pid) => http.get(`/v2/projects/${pid}`, withUser()).then((r) => r.data),
  deleteProject: (pid) => http.delete(`/v2/projects/${pid}`, withUser()).then((r) => r.data),

  uploadAsset: (pid, file, onProgress, role = 'footage') => {
    const form = new FormData();
    form.append('file', file);
    return http
      .post(`/v2/projects/${pid}/assets/upload`, form, {
        ...withUser({ transcribe: true, role }),
        onUploadProgress: (e) => e.total && onProgress?.(e.loaded / e.total),
      })
      .then((r) => r.data);
  },
  addYoutube: (pid, url, role = 'footage') =>
    http.post(`/v2/projects/${pid}/assets/url`, { url, role }, withUser()).then((r) => r.data),

  getTimeline: (pid, version) => http.get(`/v2/projects/${pid}/timeline`, withUser({ version })).then((r) => r.data),
  restoreTimeline: (pid, version) =>
    http.post(`/v2/projects/${pid}/timeline/restore`, { version }, withUser()).then((r) => r.data),

  saveTimeline: (pid, timeline, note, renderPreview = true) =>
    http
      .put(`/v2/projects/${pid}/timeline`, { timeline, note, render_preview: renderPreview }, withUser())
      .then((r) => r.data),
  undo: (pid) => http.post(`/v2/projects/${pid}/timeline/undo`, {}, withUser()).then((r) => r.data),
  redo: (pid) => http.post(`/v2/projects/${pid}/timeline/redo`, {}, withUser()).then((r) => r.data),
  changes: (pid, version) =>
    http.get(`/v2/projects/${pid}/timeline/changes`, withUser({ version })).then((r) => r.data),
  previewCaptions: (pid, timeline) =>
    http.post(`/v2/projects/${pid}/captions/preview`, { timeline }, withUser()).then((r) => r.data.lines),

  stockSearch: (pid, q, orientation) =>
    http.get(`/v2/projects/${pid}/stock/search`, withUser({ q, orientation })).then((r) => r.data),
  stockAdd: (pid, provider, id) =>
    http.post(`/v2/projects/${pid}/stock/add`, { provider, id }, withUser()).then((r) => r.data),
  generateBroll: (pid, prompt) =>
    http.post(`/v2/projects/${pid}/broll/generate`, { prompt }, withUser()).then((r) => r.data),
  stickers: () => http.get('/v2/stickers').then((r) => r.data),
  screenColor: (projectId, assetId, t) =>
    http.get(`/v2/projects/${projectId}/assets/${assetId}/screen-color`, withUser({ t })).then((r) => r.data.color),
  sounds: () => http.get('/v2/sfx').then((r) => r.data.sounds),
  looks: () => http.get('/v2/luts').then((r) => r.data.looks),
  lookThumbnails: (pid) => http.get(`/v2/projects/${pid}/luts/thumbnails`, withUser()).then((r) => r.data),

  getCaptions: (pid) => http.get(`/v2/projects/${pid}/captions`, withUser()).then((r) => r.data),
  updateCaptions: (pid, body) => http.post(`/v2/projects/${pid}/captions`, body, withUser()).then((r) => r.data),

  render: (pid, preview, timelineVersion) =>
    http
      .post(`/v2/projects/${pid}/renders`, { preview, timeline_version: timelineVersion }, withUser())
      .then((r) => r.data),
  listJobs: (pid) => http.get(`/v2/projects/${pid}/jobs`, withUser()).then((r) => r.data.jobs),

  messages: (pid) => http.get(`/v2/projects/${pid}/messages`, withUser()).then((r) => r.data.messages),
  chat: (pid, message) => http.post(`/v2/projects/${pid}/chat`, { message }, withUser()).then((r) => r.data),
};

export const outputUrl = (jobId) => `${API_BASE}/v2/jobs/${jobId}/output?user_id=${encodeURIComponent(userId)}`;

export const assetFileUrl = (pid, asset) =>
  `${API_BASE}/v2/projects/${pid}/assets/${asset.id}/file?user_id=${encodeURIComponent(userId)}${
    asset.has_proxy ? '&proxy=true' : ''
  }`;

export const stickerUrl = (sticker, color) =>
  `${API_BASE}/v2/stickers/lottie?sticker=${encodeURIComponent(sticker)}${color ? `&color=${encodeURIComponent(color)}` : ''}`;
