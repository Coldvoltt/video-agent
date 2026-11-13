import { useState } from 'react';
import { api, outputUrl } from '../lib/api';
import { FONTS } from '../lib/fonts';
import { isActive, timecode } from '../lib/format';
import { clipSpans } from '../lib/timelineEdit';

const LOOK_NAMES = { karaoke: 'Karaoke', pop: 'Pop', boxed: 'Boxed', minimal: 'Minimal' };

function totalOf(data) {
  const spans = clipSpans(data?.clips || []);
  return spans.length ? spans[spans.length - 1].end : 0;
}

/** "3 min ago" from the server's UTC timestamps. */
export function ago(stamp) {
  if (!stamp) return '';
  const t = new Date(`${stamp.replace(' ', 'T')}Z`).getTime();
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function wordsIn(project) {
  const text = Object.values(project?.timeline?.clip_text || {}).join(' ');
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

/** The edit in numbers, under the preview. */
export function AtAGlance({ project, draft }) {
  const data = draft || project?.timeline?.data;
  if (!data) return null;
  const total = totalOf(data);
  const clips = data.clips || [];
  const broll = (data.broll || []).reduce((a, b) => a + Math.min(b.duration, Math.max(0, total - b.start)), 0);
  const words = draft ? 0 : wordsIn(project);
  const layers = ['text', 'overlays', 'audio', 'broll', 'zooms', 'stickers', 'effects']
    .reduce((n, k) => n + (data[k]?.length || 0), 0);
  const cap = data.captions;
  const stats = [
    { value: timecode(total), label: 'long', tone: 'yellow' },
    { value: clips.length, label: `${clips.length === 1 ? 'shot' : 'shots'}, ${clips.length ? (total / clips.length).toFixed(1) : 0} s each`, tone: 'tangerine' },
    { value: words && total ? Math.round(words / (total / 60)) : '–', label: 'words a minute', tone: 'pink' },
    { value: `${total ? Math.round((broll / total) * 100) : 0}%`, label: `B-roll (${data.broll?.length || 0} cutaways)`, tone: 'blue' },
    { value: cap ? LOOK_NAMES[cap.style] || cap.style : 'Off', label: cap ? `captions${cap.font ? `, ${FONTS[cap.font]?.label}` : ''}${cap.emphasis ? ', key words styled' : ''}` : 'captions', tone: 'mint' },
    { value: layers, label: layers === 1 ? 'layer on top' : 'layers on top', tone: 'paper' },
  ];
  return (
    <section className="glance" aria-label="The edit at a glance">
      {stats.map((s) => (
        <div key={s.label} className={`glance-stat tone-${s.tone}`}>
          <span className="glance-value">{s.value}</span>
          <span className="glance-label">{s.label}</span>
        </div>
      ))}
    </section>
  );
}

/** The script of the edit, shot by shot. Click a line to jump there. */
export function ScriptPanel({ project, draft, playhead, onSeek }) {
  const [query, setQuery] = useState('');
  const row = project?.timeline;
  if (!row) return <p className="empty-hint">The script appears once there's an edit.</p>;
  const data = draft || row.data;
  const clips = data.clips || [];
  const spans = clipSpans(clips);
  const t = playhead();
  const names = Object.fromEntries(project.assets.map((a) => [a.id, a]));
  const q = query.trim().toLowerCase();
  const words = wordsIn(project);
  const lines = clips.map((c, i) => {
    const asset = names[c.asset_id];
    const angle = asset?.multicam?.angles?.[c.angle || 0];
    return { c, span: spans[i], text: row.clip_text?.[c.id] || '', source: angle ? angle.label : asset?.filename || 'Clip' };
  });
  const shown = q ? lines.filter((l) => l.text.toLowerCase().includes(q)) : lines;

  return (
    <section aria-label="Script">
      <div className="captions-head">
        <h2 className="panel-title">Script</h2>
        <span className="timeline-total">
          {words} words, {clips.length} shots{draft ? ' (unsaved edits: text shows after saving)' : ''}
        </span>
      </div>
      <label className="script-search">
        <span className="visually-hidden">Find in the script</span>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a word or phrase" />
      </label>
      <ol className="script">
        {shown.map(({ c, span, text, source }) => {
          const now = t >= span.start && t < span.end;
          return (
            <li key={c.id} className={now ? 'is-now' : ''}>
              <button className="script-line" onClick={() => onSeek(span.start)}>
                <span className="script-time">{timecode(span.start)}</span>
                <span className="script-text">{text || <em>No speech</em>}</span>
                <span className="script-source">{source}</span>
              </button>
            </li>
          );
        })}
      </ol>
      {q && !shown.length && <p className="empty-hint">“{query}” isn't said in this edit.</p>}
    </section>
  );
}

const LAYER_KINDS = [
  ['broll', 'B-roll', 'blue', (x, a) => a[x.asset_id]?.filename || 'Cutaway', (x) => [x.start, x.start + x.duration]],
  ['text', 'Text', 'paper', (x) => `“${x.text}”${x.style === 'keyword' ? ' (keyword)' : ''}`, (x) => [x.start, x.end]],
  ['stickers', 'Stickers', 'pink', (x) => x.sticker.replace('emoji:', '').replace(/^asset:.*/, 'Animation'), (x) => [x.start, x.end]],
  ['zooms', 'Zooms', 'tangerine', (x) => `Punch-in ${x.scale}×`, (x) => [x.start, x.end]],
  ['effects', 'Effects', 'mint', (x) => ({ denoise: 'Video noise removal', grain: 'Film grain', blur: 'Blur' }[x.type] + (x.region ? ' (area)' : '')), (x) => [x.start || 0, x.end]],
  ['audio', 'Sound', 'blue', (x, a) => (x.sfx ? `Sound effect: ${x.sfx}` : a[x.asset_id]?.filename || 'Audio'), (x) => [x.start, null]],
  ['overlays', 'Overlays', 'paper', (x, a) => a[x.asset_id]?.filename || 'Overlay', (x) => [x.start, x.end]],
];

/** Everything on top of the clips, in time order per kind. Click one to jump to it. */
export function LayersPanel({ project, draft, onSeek, onSelect }) {
  const data = draft || project?.timeline?.data;
  if (!data) return <p className="empty-hint">Layers appear once there's an edit.</p>;
  const assets = Object.fromEntries(project.assets.map((a) => [a.id, a]));
  const total = totalOf(data);
  const groups = LAYER_KINDS.map(([key, label, tone, name, when]) => ({
    key, label, tone,
    items: [...(data[key] || [])].sort((a, b) => (a.start || 0) - (b.start || 0)).map((x) => {
      const [a, b] = when(x);
      return { id: x.id, name: name(x, assets), start: a, end: b ?? null };
    }),
  })).filter((g) => g.items.length);
  const selectable = new Set(['broll', 'stickers', 'effects']);

  return (
    <section aria-label="Layers">
      <div className="captions-head">
        <h2 className="panel-title">Layers</h2>
        <span className="timeline-total">
          {groups.reduce((n, g) => n + g.items.length, 0)} on top of {data.clips.length} clips
        </span>
      </div>
      {!groups.length && <p className="empty-hint">No layers yet. Ask for B-roll, stickers, text or effects in the chat.</p>}
      {groups.map((g) => (
        <div key={g.key} className="layer-group">
          <h3 className={`layer-group-title tone-${g.tone}`}>
            <span className="tool-dot" aria-hidden="true" />
            {g.label} <span className="timeline-total">{g.items.length}</span>
          </h3>
          <ul className="layer-list">
            {g.items.map((x) => (
              <li key={x.id}>
                <button className="layer-row" onClick={() => {
                  onSeek(Math.max(0, x.start - 0.3));
                  if (selectable.has(g.key) && !draft) onSelect?.(x.id);
                }}>
                  <span className="layer-name">{x.name}</span>
                  <span className="layer-time">
                    {timecode(x.start)}{x.end != null ? `–${timecode(Math.min(x.end, total))}` : ''}
                    {g.key === 'effects' && x.end == null ? '–end' : ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

/** Every saved version with what it was, newest first; restore any of them. */
export function HistoryPanel({ project, act }) {
  const versions = project?.timeline_versions || [];
  const current = project?.timeline?.version;
  const [open, setOpen] = useState(null);
  const [changes, setChanges] = useState({});
  if (!versions.length) return <p className="empty-hint">Versions appear here as you edit.</p>;

  const toggle = async (v) => {
    setOpen(open === v ? null : v);
    if (!changes[v]) {
      try {
        const c = await api.changes(project.id, v);
        setChanges((m) => ({ ...m, [v]: c?.changes || [] }));
      } catch {
        setChanges((m) => ({ ...m, [v]: [] }));
      }
    }
  };

  return (
    <section aria-label="History">
      <div className="captions-head">
        <h2 className="panel-title">History</h2>
        <span className="timeline-total">{versions.length} versions. Restoring one saves it as the newest.</span>
      </div>
      <ol className="history">
        {versions.map((v) => (
          <li key={v.version} className={v.version === current ? 'is-current' : ''}>
            <div className="history-row">
              <span className="history-version">v{v.version}</span>
              <button className="history-note" onClick={() => toggle(v.version)} aria-expanded={open === v.version}>
                {v.note || 'Edit'}
              </button>
              <span className="history-when">{ago(v.created_at)}</span>
              {v.version === current ? (
                <span className="chip chip-yellow">Current</span>
              ) : (
                <button className="button-quiet" onClick={() => act(() => api.restoreTimeline(project.id, v.version))}>
                  Restore
                </button>
              )}
            </div>
            {open === v.version && (
              <ul className="history-changes">
                {(changes[v.version] || []).length
                  ? changes[v.version].map((c) => <li key={c}>{c}</li>)
                  : <li>{changes[v.version] ? 'No changes recorded.' : 'Loading…'}</li>}
              </ul>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

/** Rendered previews and full-quality exports, with downloads. */
export function ExportsPanel({ jobs, act, project }) {
  const renders = jobs.filter((j) => j.kind === 'render');
  const current = project?.timeline?.version;
  return (
    <section aria-label="Exports">
      <div className="captions-head">
        <h2 className="panel-title">Renders and exports</h2>
        {current && (
          <button className="button-primary" onClick={() => act(() => api.render(project.id, false, current))}>
            Export version {current} in full quality
          </button>
        )}
      </div>
      {!renders.length && <p className="empty-hint">Nothing rendered yet. Previews appear here after each edit.</p>}
      <ul className="exports">
        {renders.map((j) => {
          const r = j.result || {};
          const full = j.input?.preview === false;
          return (
            <li key={j.id} className="export-row">
              <span className={`chip ${full ? 'chip-yellow' : ''}`}>{full ? 'Full quality' : 'Preview'}</span>
              <span className="export-what">
                Version {j.input?.timeline_version ?? r.timeline_version}
                {r.width ? `, ${r.width}×${r.height}` : ''}
                {r.duration ? `, ${timecode(r.duration)}` : ''}
                {r.size_bytes ? `, ${(r.size_bytes / 1e6).toFixed(1)} MB` : ''}
              </span>
              <span className="export-when">
                {isActive(j) ? `${j.message || 'Rendering'} ${Math.round((j.progress || 0) * 100)}%`
                  : j.status === 'failed' ? 'Failed' : ago(j.finished_at || j.created_at)}
              </span>
              {j.status === 'succeeded' && (
                <a className="button-secondary" href={outputUrl(j.id)} download>
                  Download
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
