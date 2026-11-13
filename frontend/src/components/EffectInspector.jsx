import { timecode } from '../lib/format';

export const EFFECT_NAMES = { denoise: 'Video noise removal', grain: 'Film grain', blur: 'Blur' };
export const BLUR_LEVELS = [[0.2, 'Light'], [0.5, 'Medium'], [0.8, 'Strong'], [1, 'Maximum']];
const levelName = (v) => BLUR_LEVELS.reduce((best, x) => (Math.abs(x[0] - v) < Math.abs(best[0] - v) ? x : best))[1];

/** Hand edits for one picture effect: when, how strong, and (for blur) which part of the frame. */
export default function EffectInspector({ timeline, layerId, total, onChange, onSelect, onSeek }) {
  const list = timeline.effects || [];
  const fx = list.find((x) => x.id === layerId);
  if (!fx) return null;

  const update = (changes) => {
    const next = { ...fx, ...changes };
    for (const k of Object.keys(next)) if (next[k] === null || next[k] === undefined) delete next[k];
    onChange({ ...timeline, effects: list.map((x) => (x.id === fx.id ? next : x)) });
  };
  const region = fx.region;
  const setRegion = (k, v) => {
    const r = { ...region, [k]: v };
    r.w = Math.min(r.w, Math.round((1 - r.x) * 100) / 100);
    r.h = Math.min(r.h, Math.round((1 - r.y) * 100) / 100);
    update({ region: r });
  };
  const whole = (fx.start || 0) === 0 && fx.end == null;
  const pct = (v) => Math.round(v * 100);

  return (
    <section className="clip-inspector" aria-label="Selected effect">
      <div className="inspector-head">
        <h3 className="panel-title">{EFFECT_NAMES[fx.type]}</h3>
        <span className="timeline-total">
          {whole ? 'whole video' : `${timecode(fx.start || 0)}–${fx.end == null ? 'end' : timecode(fx.end)}`}
        </span>
        <button className="button-quiet" onClick={() => onSelect(null)}>
          Close
        </button>
      </div>
      <div className="inspector-grid">
        <label>
          <span className="control-label">
            {fx.type === 'blur'
              ? `Blur level: ${levelName(fx.strength ?? 0.5)} (${pct(fx.strength ?? 0.5)}%)`
              : `Strength ${pct(fx.strength ?? 0.5)}%`}
          </span>
          <input type="range" min="0.05" max="1" step="0.05" value={fx.strength ?? 0.5}
            onChange={(e) => update({ strength: Number(e.target.value) })} />
          {fx.type === 'blur' && (
            <span className="level-buttons">
              {BLUR_LEVELS.map(([v, l]) => (
                <button key={v} type="button" className={`level ${Math.abs((fx.strength ?? 0.5) - v) < 0.03 ? 'is-on' : ''}`}
                  onClick={() => update({ strength: v })}>
                  {l}
                </button>
              ))}
            </span>
          )}
        </label>
        <label>
          <span className="control-label">From (seconds)</span>
          <input type="number" step="0.1" min="0" value={fx.start || 0}
            onChange={(e) => update({ start: Math.max(0, Number(e.target.value)) })} />
        </label>
        <label>
          <span className="control-label">Until (seconds, empty = the end)</span>
          <input type="number" step="0.1" min="0" max={total} value={fx.end ?? ''} placeholder="the end"
            onChange={(e) => update({ end: e.target.value === '' ? null : Number(e.target.value) })} />
        </label>
        {fx.type === 'blur' && (
          <label className="check-row">
            <input type="checkbox" checked={!!region}
              onChange={(e) => update({ region: e.target.checked ? { x: 0.35, y: 0.35, w: 0.3, h: 0.3 } : null })} />
            <span>Only part of the frame</span>
          </label>
        )}
      </div>
      {fx.type === 'blur' && region && (
        <div className="inspector-grid region-grid">
          {[['x', 'From the left'], ['y', 'From the top'], ['w', 'Width'], ['h', 'Height']].map(([k, label]) => (
            <label key={k}>
              <span className="control-label">{label} {pct(region[k])}%</span>
              <input type="range" min={k === 'w' || k === 'h' ? 0.02 : 0} max="1" step="0.01" value={region[k]}
                onChange={(e) => setRegion(k, Number(e.target.value))} />
            </label>
          ))}
        </div>
      )}
      {fx.type === 'denoise' && (
        <p className="fine-print">
          Shows in the rendered preview, not the live one. For hiss or room noise in the sound, use Sound → noise reduction.
        </p>
      )}
      <div className="inspector-actions">
        <button className="button-secondary" onClick={() => onSeek(Math.max(0, (fx.start || 0) - 0.3))}>
          Play from here
        </button>
        <button className="button-quiet button-danger" onClick={() => {
          onChange({ ...timeline, effects: list.filter((x) => x.id !== fx.id) });
          onSelect(null);
        }}>
          Remove effect
        </button>
      </div>
    </section>
  );
}
