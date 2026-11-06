import { timecode } from '../lib/format';

/** Hand edits for one sticker: where, how big, when, and colour for callouts. */
export default function StickerInspector({ timeline, layerId, onChange, onSelect, onSeek }) {
  const list = timeline.stickers || [];
  const st = list.find((x) => x.id === layerId);
  if (!st) return null;
  const isCallout = !st.sticker.startsWith('emoji:') && !st.sticker.startsWith('asset:');
  const label = st.sticker.startsWith('emoji:') ? st.sticker.slice(6) : st.sticker.startsWith('asset:') ? 'Animation' : st.sticker;

  const update = (changes) => {
    const next = { ...st, ...changes };
    const length = Math.max(0.2, (changes.length ?? st.end - st.start));
    delete next.length;
    next.start = Math.max(0, Math.round(next.start * 100) / 100);
    next.end = Math.round((next.start + length) * 100) / 100;
    onChange({ ...timeline, stickers: list.map((x) => (x.id === st.id ? next : x)) });
  };
  const pct = (v) => Math.round(v * 100);

  return (
    <section className="clip-inspector" aria-label="Selected sticker">
      <div className="inspector-head">
        <h3 className="panel-title">Sticker: {label}</h3>
        <span className="timeline-total">
          on screen {timecode(st.start)}–{timecode(st.end)}
        </span>
        <button className="button-quiet" onClick={() => onSelect(null)}>
          Close
        </button>
      </div>
      <div className="inspector-grid">
        <label>
          <span className="control-label">Across {pct(st.x ?? 0.5)}%</span>
          <input type="range" min="0" max="1" step="0.01" value={st.x ?? 0.5} onChange={(e) => update({ x: Number(e.target.value) })} />
        </label>
        <label>
          <span className="control-label">Down {pct(st.y ?? 0.35)}%</span>
          <input type="range" min="0" max="1" step="0.01" value={st.y ?? 0.35} onChange={(e) => update({ y: Number(e.target.value) })} />
        </label>
        <label>
          <span className="control-label">Size {pct(st.scale ?? 0.25)}% of the width</span>
          <input type="range" min="0.05" max="0.8" step="0.01" value={st.scale ?? 0.25} onChange={(e) => update({ scale: Number(e.target.value) })} />
        </label>
        <label>
          <span className="control-label">Rotation {Math.round(st.rotation || 0)}°</span>
          <input type="range" min="-180" max="180" step="5" value={st.rotation || 0} onChange={(e) => update({ rotation: Number(e.target.value) })} />
        </label>
        <label>
          <span className="control-label">Appears at (seconds)</span>
          <input type="number" step="0.1" min="0" value={st.start} onChange={(e) => update({ start: Number(e.target.value) })} />
        </label>
        <label>
          <span className="control-label">Length (seconds)</span>
          <input type="number" step="0.1" min="0.2" value={Math.round((st.end - st.start) * 100) / 100}
            onChange={(e) => update({ length: Number(e.target.value) })} />
        </label>
        {isCallout && (
          <label>
            <span className="control-label">Colour</span>
            <input type="color" value={st.color || '#FFD23F'} onChange={(e) => update({ color: e.target.value.toUpperCase() })} />
          </label>
        )}
      </div>
      <div className="inspector-actions">
        <button className="button-secondary" onClick={() => onSeek(Math.max(0, st.start - 0.3))}>
          Play from here
        </button>
        <button
          className="button-quiet button-danger"
          onClick={() => {
            onChange({ ...timeline, stickers: list.filter((x) => x.id !== st.id) });
            onSelect(null);
          }}
        >
          Remove sticker
        </button>
      </div>
    </section>
  );
}
