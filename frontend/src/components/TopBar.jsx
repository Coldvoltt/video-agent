import { useState } from 'react';
import { isActive, timecode } from '../lib/format';
import { clipSpans } from '../lib/timelineEdit';

function shapeName(w, h) {
  const r = w / h;
  if (Math.abs(r - 9 / 16) < 0.02) return 'Vertical 9:16';
  if (Math.abs(r - 16 / 9) < 0.02) return 'Landscape 16:9';
  if (Math.abs(r - 1) < 0.02) return 'Square 1:1';
  if (Math.abs(r - 4 / 5) < 0.02) return 'Portrait 4:5';
  return `${w}×${h}`;
}

/** What the project is right now: format, length, version, and anything running. */
function StatusChips({ project, jobs }) {
  const tl = project?.timeline;
  const chips = [];
  if (tl) {
    const out = tl.data.output || {};
    const spans = clipSpans(tl.data.clips || []);
    const total = spans.length ? spans[spans.length - 1].end : 0;
    chips.push({ text: shapeName(out.width || 1920, out.height || 1080), tone: 'plain' });
    chips.push({ text: timecode(total), tone: 'plain' });
    chips.push({ text: `Version ${tl.version}`, tone: 'plain' });
  }
  const agent = jobs.find((j) => j.kind === 'agent' && isActive(j));
  const render = jobs.find((j) => j.kind === 'render' && isActive(j));
  const cut = jobs.find((j) => j.kind === 'multicam' && isActive(j));
  if (agent) chips.push({ text: 'Editor working', tone: 'blue', live: true });
  if (cut) chips.push({ text: 'Syncing cameras', tone: 'mint', live: true });
  if (render) {
    const pct = render.progress ? ` ${Math.round(render.progress * 100)}%` : '';
    chips.push({ text: `${render.input?.preview ? 'Rendering preview' : 'Exporting'}${pct}`, tone: 'yellow', live: true });
  }
  if (!chips.length) return null;
  return (
    <ul className="status-chips" aria-label="Project status">
      {chips.map((c) => (
        <li key={c.text} className={`chip chip-${c.tone}`}>
          {c.live && <span className="chip-dot" aria-hidden="true" />}
          {c.text}
        </li>
      ))}
    </ul>
  );
}

export default function TopBar({ projects, project, projectId, jobs = [], user, billing, onSignOut, onSelect, onCreate, onNew, onDelete }) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');

  const submit = (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    onCreate(name.trim());
    setName('');
    setCreating(false);
  };

  return (
    <header className="topbar">
      <a className="brand" href="/" title="VidAgent home">
        <span className="brand-mark" aria-hidden="true">V</span>
        VidAgent
      </a>

      {projects.length > 0 && !creating && (
        <div className="project-picker">
          <label className="visually-hidden" htmlFor="project-select">
            Project
          </label>
          <select id="project-select" value={projectId ?? ''} onChange={(e) => onSelect(e.target.value)}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button className="button-quiet" onClick={() => (onNew ? onNew() : setCreating(true))}>
            New project
          </button>
          {project && (
            <button className="button-quiet button-danger" onClick={onDelete}>
              Delete project
            </button>
          )}
        </div>
      )}

      {creating && (
        <form className="inline-form" onSubmit={submit}>
          <label className="visually-hidden" htmlFor="new-project">
            Project name
          </label>
          <input
            id="new-project"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Project name"
            maxLength={200}
            onKeyDown={(e) => e.key === 'Escape' && setCreating(false)}
          />
          <button className="button-primary" disabled={!name.trim()}>
            Create project
          </button>
          <button type="button" className="button-quiet" onClick={() => setCreating(false)}>
            Cancel
          </button>
        </form>
      )}
      <StatusChips project={project} jobs={jobs} />
      {billing?.billing && (
        <a className="coin-chip" href="/dashboard#plan" title="Your vid-coins and plan">
          <span className="coin" aria-hidden="true">V</span>
          {billing.coins}
          <span className="coin-chip-plan">{billing.plan_name}</span>
        </a>
      )}
      {user ? (
        <div className="user-menu">
          <a className="user-chip" href="/dashboard" title="Your projects and settings">
            <span className="user-avatar" aria-hidden="true">{(user.name || user.email)[0].toUpperCase()}</span>
            <span className="user-name">{user.name || user.email}</span>
          </a>
          <button className="button-quiet" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      ) : user === null ? (
        <a className="button-secondary" href="/dashboard">
          Sign in
        </a>
      ) : null}
    </header>
  );
}
