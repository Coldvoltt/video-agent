import { useEffect, useState } from 'react';
import { api, stickerUrl } from '../lib/api';
import { timecode } from '../lib/format';
import LottieSticker from './LottieSticker';

const NAMES = { circle: 'Circle', underline: 'Underline', box: 'Box', arrow: 'Arrow', check: 'Check', cross: 'Cross', sparkle: 'Sparkle' };

/** Pick a callout or an animated emoji and drop it at the playhead (saves a version with a preview). */
export default function StickerPanel({ project, act, playhead }) {
  const timeline = project?.timeline;
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState(null);
  const [color, setColor] = useState('#FFD23F');
  const [custom, setCustom] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open && !catalog) api.stickers().then(setCatalog).catch(() => {});
  }, [open, catalog]);

  if (!timeline) return null;
  const count = (timeline.data.stickers || []).length;
  const animations = project.assets.filter((a) => a.kind === 'lottie' && a.status === 'ready');

  const add = async (sticker, isCallout) => {
    const list = timeline.data.stickers || [];
    const taken = new Set(list.map((x) => x.id));
    let n = 1;
    while (taken.has(`sticker${n}`)) n += 1;
    const at = Math.max(0, Math.round(playhead() * 100) / 100);
    const layer = { id: `sticker${n}`, sticker, start: at, end: Math.round((at + 1.5) * 100) / 100, x: 0.5, y: 0.3, scale: 0.25 };
    if (isCallout) layer.color = color;
    setSaving(true);
    await act(() => api.saveTimeline(project.id, { ...timeline.data, stickers: [...list, layer] },
      `Sticker at ${timecode(at)}`, true));
    setSaving(false);
  };

  return (
    <section className="colour-panel sticker-panel" aria-label="Stickers">
      <div className="captions-head">
        <h2 className="panel-title">Stickers</h2>
        <span className="timeline-total">{count ? `${count} on the video` : 'Callouts and animated emoji'}</span>
        <button className="button-quiet" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? 'Hide stickers' : 'Add stickers'}
        </button>
      </div>
      {open && catalog && (
        <>
          <p className="fine-print">Added at the playhead ({timecode(playhead())}) for 1.5 s. Click one on the timeline to move or resize it.</p>
          <div className="sticker-row">
            <label className="sticker-color">
              <span className="control-label">Callout colour</span>
              <input type="color" value={color} onChange={(e) => setColor(e.target.value.toUpperCase())} />
            </label>
            <ul className="sticker-grid">
              {catalog.callouts.map((c) => (
                <li key={c.id}>
                  <button className="sticker-pick" disabled={saving} title={c.description} onClick={() => add(c.id, true)}>
                    <LottieSticker url={stickerUrl(c.id, color)} autoplay className="sticker-thumb" />
                    <span>{NAMES[c.id] || c.id}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="sticker-row">
            <span className="control-label">Animated emoji</span>
            <ul className="emoji-grid">
              {catalog.popular_emoji.map((e) => (
                <li key={e}>
                  <button className="emoji-pick" disabled={saving} onClick={() => add(`emoji:${e}`, false)} aria-label={`Add ${e}`}>
                    {e}
                  </button>
                </li>
              ))}
            </ul>
            <form
              className="input-row emoji-custom"
              onSubmit={(ev) => {
                ev.preventDefault();
                if (custom.trim()) add(`emoji:${custom.trim()}`, false);
              }}
            >
              <label className="visually-hidden" htmlFor="emoji-custom">
                Any emoji
              </label>
              <input id="emoji-custom" value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="Any emoji, e.g. 🦒" maxLength={12} />
              <button className="button-secondary" disabled={!custom.trim() || saving}>
                Add
              </button>
            </form>
          </div>
          {animations.length > 0 && (
            <div className="sticker-row">
              <span className="control-label">Your animations</span>
              <div className="inspector-actions">
                {animations.map((a) => (
                  <button key={a.id} className="button-secondary" disabled={saving} onClick={() => add(`asset:${a.id}`, false)}>
                    {a.filename}
                  </button>
                ))}
              </div>
            </div>
          )}
          <p className="fine-print">{catalog.emoji_credit}.</p>
        </>
      )}
    </section>
  );
}
