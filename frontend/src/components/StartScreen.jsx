import { useRef, useState } from 'react';
import { api } from '../lib/api';
import { ago } from './ProjectInfo';
import AuthModal from './AuthModal';

const FORMATS = [
  { id: 'vertical', label: 'Vertical', hint: '9:16, Shorts, Reels, TikTok', shape: [9, 16], say: 'Make it a vertical 9:16 video.' },
  { id: 'landscape', label: 'Landscape', hint: '16:9, YouTube', shape: [16, 9], say: 'Keep it landscape 16:9.' },
  { id: 'square', label: 'Square', hint: '1:1, feeds', shape: [1, 1], say: 'Make it square 1:1.' },
  { id: 'original', label: 'As filmed', hint: 'Keep the shape', shape: null, say: '' },
];

const STARTERS = [
  { text: 'A 30-second short of the best moment', format: 'vertical', tone: 'yellow' },
  { text: 'Clean it up: cut the pauses and ums, and add captions', tone: 'mint' },
  { text: 'Karaoke captions with the key words in a comic font, plus B-roll of what I talk about', tone: 'blue' },
  { text: 'These are camera angles of one take: make a multicam edit', tone: 'pink' },
  { text: 'Make it punchy: zooms on the key points, sound effects and a warm look', tone: 'tangerine' },
];

const VIDEO_TYPES = '.mp4,.mov,.mkv,.webm,.avi,.m4v,.mp3,.wav,.m4a,.aac';
export const briefKey = (pid) => `brief:${pid}`;

const nameFrom = (files, url) => {
  const first = files[0]?.name?.replace(/\.[a-z0-9]+$/i, '').replace(/[_-]+/g, ' ').trim();
  if (first) return first.slice(0, 80);
  if (url) return 'YouTube edit';
  return '';
};

/**
 * Starting a project the way the product works: say what you're making, add the footage, go.
 * The project is named after the footage, and the brief waits in the chat until the footage is ready.
 */
export default function StartScreen({ projects, onOpen, onCancel, act, user, onSignedIn }) {
  const [brief, setBrief] = useState('');
  const [format, setFormat] = useState('original');
  const [files, setFiles] = useState([]);
  const [url, setUrl] = useState('');
  const [name, setName] = useState('');
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(null); // {done, total, current, pct}
  const [askAccount, setAskAccount] = useState(false);
  const fileInput = useRef(null);

  const addFiles = (list) => {
    const picked = Array.from(list || []); // copy now: the input is cleared before React applies the update
    setFiles((f) => [...f, ...picked].slice(0, 6));
  };
  const ready = (files.length > 0 || url.trim()) && !progress;
  const shownName = name || nameFrom(files, url.trim());

  const start = async (signedIn = !!user) => {
    if (!ready) return;
    if (!signedIn) {
      setAskAccount(true); // sign in first; everything picked here stays and the start carries on after
      return;
    }
    const say = FORMATS.find((f) => f.id === format)?.say || '';
    const message = [brief.trim(), say].filter(Boolean).join(' ');
    setProgress({ done: 0, total: files.length, current: '', pct: 0 });
    const project = await act(() => api.createProject(shownName || 'Untitled edit'));
    if (!project?.id) {
      setProgress(null);
      return;
    }
    try {
      if (message) localStorage.setItem(briefKey(project.id), message);
    } catch {
      /* without storage the brief just isn't sent automatically */
    }
    for (let i = 0; i < files.length; i += 1) {
      setProgress({ done: i, total: files.length, current: files[i].name, pct: 0 });
      await act(() => api.uploadAsset(project.id, files[i], (pct) => setProgress((p) => ({ ...p, pct }))));
    }
    if (url.trim()) await act(() => api.addYoutube(project.id, url.trim()));
    onOpen(project);
  };

  const pct = progress ? Math.round(((progress.done + (progress.pct || 0)) / Math.max(1, progress.total)) * 100) : 0;

  return (
    <main className="start">
      <div className="start-head">
        <h1>What are we making?</h1>
        {onCancel && (
          <button className="button-quiet" onClick={onCancel}>
            Back to my project
          </button>
        )}
      </div>

      <div className="start-grid">
        <section className="start-card start-brief" aria-labelledby="brief-title">
          <h2 id="brief-title" className="start-card-title">Describe the edit</h2>
          <p className="start-card-hint">Optional. It’s sent to the editor as soon as your footage is ready.</p>
          <label className="visually-hidden" htmlFor="start-brief">Describe the edit</label>
          <textarea id="start-brief" rows={4} value={brief} maxLength={2000}
            placeholder="e.g. A tight 45-second short: cut the ums, bold captions, and B-roll when I mention the city"
            onChange={(e) => setBrief(e.target.value)} />
          <ul className="starters" aria-label="Starting points">
            {STARTERS.map((s) => (
              <li key={s.text}>
                <button type="button" className={`suggestion tone-${s.tone}`} onClick={() => {
                  setBrief(s.text);
                  if (s.format) setFormat(s.format);
                }}>
                  {s.text}
                </button>
              </li>
            ))}
          </ul>

          <fieldset className="formats">
            <legend className="start-card-title">Format</legend>
            {FORMATS.map((f) => (
              <label key={f.id} className={`format ${format === f.id ? 'is-picked' : ''}`}>
                <input type="radio" name="format" value={f.id} checked={format === f.id} onChange={() => setFormat(f.id)} />
                <span className="format-shape" aria-hidden="true"
                  style={f.shape ? { aspectRatio: `${f.shape[0]} / ${f.shape[1]}` } : undefined} data-free={!f.shape || undefined} />
                <span className="format-text">
                  <strong>{f.label}</strong>
                  <span>{f.hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
        </section>

        <section className="start-card start-footage" aria-labelledby="footage-title">
          <h2 id="footage-title" className="start-card-title">Add your footage</h2>
          <div
            className={`start-drop ${dragging ? 'is-dragging' : ''} ${files.length ? 'has-files' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
          >
            {files.length === 0 ? (
              <>
                <p className="start-drop-title">Drop videos here</p>
                <p className="start-card-hint">One clip, or up to three camera angles of the same take, plus a separate audio file if you have one.</p>
              </>
            ) : (
              <ul className="start-files">
                {files.map((f, i) => (
                  <li key={`${f.name}-${i}`}>
                    <span className="start-file-name">{f.name}</span>
                    <span className="start-file-size">{(f.size / 1e6).toFixed(1)} MB</span>
                    {!progress && (
                      <button type="button" className="button-quiet" aria-label={`Remove ${f.name}`}
                        onClick={() => setFiles((list) => list.filter((_, k) => k !== i))}>
                        Remove
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <button type="button" className="button-secondary" disabled={!!progress} onClick={() => fileInput.current?.click()}>
              {files.length ? 'Add more' : 'Choose files'}
            </button>
            <input ref={fileInput} type="file" multiple accept={VIDEO_TYPES} hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ''; }} />
          </div>

          <label className="start-field">
            <span className="control-label">Or a YouTube link</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://youtube.com/watch?v=…" disabled={!!progress} />
            <span className="start-card-hint">Only videos you own or have the rights to edit.</span>
          </label>

          <label className="start-field">
            <span className="control-label">Project name</span>
            <input value={shownName} onChange={(e) => setName(e.target.value)} placeholder="Named after your first video"
              maxLength={200} disabled={!!progress} />
          </label>

          <div className="start-go">
            <button className="button-primary start-button" disabled={!ready} onClick={() => start()}>
              {progress ? 'Uploading…' : 'Start editing'}
            </button>
            {progress ? (
              <div className="start-progress" role="status">
                <progress value={pct} max={100} aria-label="Upload progress" />
                <span>
                  {progress.total ? `Uploading ${progress.current} (${progress.done + 1} of ${progress.total}), ${pct}%` : 'Adding your link…'}
                </span>
              </div>
            ) : (
              <span className="start-card-hint">{ready ? 'Opens the editor while your footage is prepared.' : 'Add a video or a link to start.'}</span>
            )}
          </div>
        </section>
      </div>

      {projects.length > 0 && (
        <section className="start-recent" aria-labelledby="recent-title">
          <h2 id="recent-title" className="start-card-title">Or pick up where you left off</h2>
          <ul>
            {projects.slice(0, 8).map((p) => (
              <li key={p.id}>
                <button className="recent" onClick={() => onOpen(p)}>
                  <strong>{p.name}</strong>
                  <span>{p.created_at ? `Started ${ago(p.created_at)}` : ''}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {askAccount && (
        <AuthModal
          reason="Sign in or create a free account to start editing. Your videos and brief are kept."
          onClose={() => setAskAccount(false)}
          onDone={(u) => {
            setAskAccount(false);
            onSignedIn?.(u);
            start(true);
          }}
        />
      )}
    </main>
  );
}
