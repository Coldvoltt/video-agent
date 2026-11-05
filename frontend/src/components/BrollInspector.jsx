import { timecode } from '../lib/format';

const MOTIONS = [
  ['push_in', 'Slow push in'],
  ['pull_out', 'Slow pull out'],
  ['pan_left', 'Pan left'],
  ['pan_right', 'Pan right'],
  ['none', 'Still'],
];

/** Hand edits for one B-roll cutaway (output-time placement over the main video). */
export default function BrollInspector({ project, timeline, layerId, onChange, onSelect, onSeek }) {
  const layers = timeline.broll || [];
  const layer = layers.find((b) => b.id === layerId);
  if (!layer) return null;
  const asset = project.assets.find((a) => a.id === layer.asset_id);
  const sourceLen = asset?.kind === 'video' ? asset.duration : undefined;

  const update = (changes) => {
    const next = { ...layer, ...changes };
    if (sourceLen) {
      next.in = Math.max(0, Math.min(next.in ?? 0, sourceLen - 0.3));
      next.duration = Math.min(next.duration, sourceLen - next.in);
    }
    next.start = Math.max(0, Math.round(next.start * 100) / 100);
    next.duration = Math.max(0.3, Math.round(next.duration * 100) / 100);
    onChange({ ...timeline, broll: layers.map((b) => (b.id === layer.id ? next : b)) });
  };

  return (
    <section className="clip-inspector" aria-label="Selected B-roll">
      <div className="inspector-head">
        <h3 className="panel-title">B-roll cutaway</h3>
        <span className="timeline-total">
          {asset?.filename || 'media'}, on screen {timecode(layer.start)}–{timecode(layer.start + layer.duration)}
        </span>
        <button className="button-quiet" onClick={() => onSelect(null)}>
          Close
        </button>
      </div>
      <div className="inspector-grid">
        <label>
          <span className="control-label">Appears at (seconds into the video)</span>
          <input type="number" step="0.1" min="0" value={layer.start} onChange={(e) => update({ start: Number(e.target.value) })} />
        </label>
        <label>
          <span className="control-label">Length (seconds)</span>
          <input type="number" step="0.1" min="0.3" value={layer.duration} onChange={(e) => update({ duration: Number(e.target.value) })} />
        </label>
        {asset?.kind === 'video' && (
          <label>
            <span className="control-label">Starts from (in the B-roll clip, seconds)</span>
            <input type="number" step="0.1" min="0" max={sourceLen} value={layer.in ?? 0} onChange={(e) => update({ in: Number(e.target.value) })} />
          </label>
        )}
        <label>
          <span className="control-label">Camera motion</span>
          <select value={layer.motion || 'push_in'} onChange={(e) => update({ motion: e.target.value })}>
            {MOTIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        {asset?.kind === 'video' && !!asset.has_audio && (
          <label>
            <span className="control-label">Its own sound {Math.round((layer.volume ?? 0) * 100)}%</span>
            <input type="range" min="0" max="1" step="0.05" value={layer.volume ?? 0} onChange={(e) => update({ volume: Number(e.target.value) })} />
          </label>
        )}
      </div>
      {asset?.shots?.length > 1 && (
        <div className="shot-picker">
          <span className="control-label">Shots in this clip</span>
          <div className="inspector-actions">
            {asset.shots.map((s, i) => (
              <button
                key={s.start}
                className={`button-quiet shot ${Math.abs((layer.in ?? 0) - s.start) < 0.05 ? 'is-on' : ''}`}
                title={s.description || undefined}
                onClick={() => update({ in: s.start, duration: Math.min(layer.duration, s.end - s.start) })}
              >
                {i + 1}. {s.description ? s.description.slice(0, 40) : `${timecode(s.start)}–${timecode(s.end)}`}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="inspector-actions">
        <button className="button-secondary" onClick={() => onSeek(Math.max(0, layer.start - 0.5))}>
          Play from here
        </button>
        <button
          className="button-quiet button-danger"
          onClick={() => {
            onChange({ ...timeline, broll: layers.filter((b) => b.id !== layer.id) });
            onSelect(null);
          }}
        >
          Remove cutaway
        </button>
      </div>
    </section>
  );
}
