import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { timecode } from '../lib/format';

const STYLES = [
  ['karaoke', 'Karaoke (highlight each word)'],
  ['pop', 'Pop (one or two big words)'],
  ['boxed', 'Boxed'],
  ['minimal', 'Minimal subtitles'],
];
const POSITIONS = [
  ['bottom', 'Bottom'],
  ['middle', 'Middle'],
  ['top', 'Top'],
];

export default function CaptionEditor({ project, act, onSeek }) {
  const version = project?.timeline?.version;
  const [data, setData] = useState(null); // {version, settings, lines}
  const [drafts, setDrafts] = useState({}); // line index -> new text
  const [settings, setSettings] = useState({}); // pending setting changes
  const [saving, setSaving] = useState(false);
  const [newStyle, setNewStyle] = useState('karaoke');

  useEffect(() => {
    if (!project?.id || !version) {
      setData(null);
      return;
    }
    let live = true;
    api
      .getCaptions(project.id)
      .then((d) => live && setData(d))
      .catch(() => live && setData(null));
    setDrafts({});
    setSettings({});
    return () => {
      live = false;
    };
  }, [project?.id, version]);

  if (!project?.timeline || !data) return null;

  const hasTranscript = project.assets.some((a) => a.transcript_status === 'done');

  if (!data.settings) {
    return (
      <section className="captions-editor captions-off" aria-label="Captions">
        <h2 className="panel-title">Captions</h2>
        {hasTranscript ? (
          <div className="caption-controls">
            <label>
              <span className="visually-hidden">Caption style</span>
              <select value={newStyle} onChange={(e) => setNewStyle(e.target.value)}>
                {STYLES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button-secondary"
              onClick={() => act(() => api.updateCaptions(project.id, { settings: { style: newStyle } }))}
            >
              Add captions
            </button>
          </div>
        ) : (
          <p className="empty-hint">Captions need a transcript. Upload footage with speech to add them.</p>
        )}
      </section>
    );
  }

  const current = { ...data.settings, ...settings };
  const changedLines = Object.entries(drafts).filter(([i, text]) => data.lines[i] && text !== data.lines[i].text);
  const dirty = changedLines.length > 0 || Object.keys(settings).length > 0;
  const setSetting = (key, value) =>
    setSettings((s) => {
      const next = { ...s, [key]: value };
      if (data.settings[key] === value) delete next[key];
      return next;
    });

  const save = async () => {
    setSaving(true);
    const body = {
      settings: Object.keys(settings).length ? settings : undefined,
      edits: changedLines.map(([i, text]) => {
        const line = data.lines[i];
        return { asset_id: line.asset_id, start: line.src_start, end: line.src_end, text: text.trim() };
      }),
    };
    await act(() => api.updateCaptions(project.id, body));
    setSaving(false);
  };

  return (
    <section className="captions-editor" aria-label="Captions">
      <div className="captions-head">
        <h2 className="panel-title">Captions</h2>
        <span className="timeline-total">
          {data.lines.length} {data.lines.length === 1 ? 'line' : 'lines'}
        </span>
        <button
          className="button-quiet"
          onClick={() => act(() => api.updateCaptions(project.id, { enabled: false }))}
          disabled={saving}
        >
          Turn off captions
        </button>
      </div>

      <div className="caption-controls">
        <label>
          <span className="control-label">Style</span>
          <select value={current.style} onChange={(e) => setSetting('style', e.target.value)}>
            {STYLES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="control-label">Position</span>
          <select value={current.position} onChange={(e) => setSetting('position', e.target.value)}>
            {POSITIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="control-label">Text colour</span>
          <input type="color" value={current.color} onChange={(e) => setSetting('color', e.target.value.toUpperCase())} />
        </label>
        <label>
          <span className="control-label">Highlight</span>
          <input
            type="color"
            value={current.highlight_color}
            onChange={(e) => setSetting('highlight_color', e.target.value.toUpperCase())}
          />
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={!!current.uppercase} onChange={(e) => setSetting('uppercase', e.target.checked)} />
          <span>All caps</span>
        </label>
      </div>

      {data.lines.length === 0 ? (
        <p className="empty-hint">No speech in this edit, so there are no caption lines.</p>
      ) : (
        <ol className="caption-lines">
          {data.lines.map((line, i) => {
            const value = drafts[i] ?? line.text;
            const changed = drafts[i] !== undefined && drafts[i] !== line.text;
            const hidden = changed && !value.trim();
            return (
              <li key={`${line.asset_id}-${line.src_start}`} className={`caption-line ${changed ? 'is-changed' : ''}`}>
                <button className="caption-time" onClick={() => onSeek(line.start)} title="Play from here">
                  {timecode(line.start)}
                </button>
                <label className="visually-hidden" htmlFor={`cap-${i}`}>
                  Caption at {timecode(line.start)}
                </label>
                <input
                  id={`cap-${i}`}
                  value={value}
                  maxLength={300}
                  placeholder="Hidden"
                  onChange={(e) => setDrafts((d) => ({ ...d, [i]: e.target.value }))}
                />
                <span className="caption-state">
                  {hidden ? 'Will be hidden' : changed ? 'Changed' : line.edited ? 'Edited' : ''}
                </span>
                {value.trim() ? (
                  <button className="button-quiet" onClick={() => setDrafts((d) => ({ ...d, [i]: '' }))}>
                    Hide
                  </button>
                ) : (
                  <button
                    className="button-quiet"
                    onClick={() =>
                      setDrafts((d) => {
                        const next = { ...d };
                        delete next[i];
                        return next;
                      })
                    }
                  >
                    Undo
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {dirty && (
        <div className="caption-actions">
          <button className="button-primary" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save caption changes'}
          </button>
          <button
            className="button-quiet"
            disabled={saving}
            onClick={() => {
              setDrafts({});
              setSettings({});
            }}
          >
            Discard
          </button>
          <span className="fine-print">Saves a new version and renders a preview.</span>
        </div>
      )}
    </section>
  );
}
