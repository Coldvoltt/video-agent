import { useState } from 'react';

export default function TopBar({ projects, project, projectId, onSelect, onCreate, onDelete }) {
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
      <span className="brand">VidAgent</span>

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
          <button className="button-quiet" onClick={() => setCreating(true)}>
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
    </header>
  );
}
