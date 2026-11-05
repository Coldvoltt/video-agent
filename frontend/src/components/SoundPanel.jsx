import { useEffect, useRef, useState } from 'react';
import { API_BASE, api } from '../lib/api';
import { timecode } from '../lib/format';

const DENOISE = [
  ['off', 'Off'],
  ['light', 'Light'],
  ['strong', 'Strong'],
];

/** Audio polish switches and the built-in sound effects. Changes save a new version (with a preview). */
export default function SoundPanel({ project, act, playhead }) {
  const timeline = project?.timeline;
  const saved = timeline?.data?.audio_polish || {};
  const [open, setOpen] = useState(false);
  const [sounds, setSounds] = useState([]);
  const [polish, setPolish] = useState(saved);
  const [saving, setSaving] = useState(false);
  const player = useRef(null);

  useEffect(() => setPolish(timeline?.data?.audio_polish || {}), [timeline?.version]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (open && !sounds.length) api.sounds().then(setSounds).catch(() => {});
  }, [open, sounds.length]);

  if (!timeline) return null;

  const norm = (p) => ({ normalize: !!p.normalize, denoise: p.denoise || 'off', voice: !!p.voice });
  const dirty = JSON.stringify(norm(polish)) !== JSON.stringify(norm(saved));
  const summary = [
    saved.normalize && 'loudness for social',
    saved.denoise && saved.denoise !== 'off' && `${saved.denoise} noise reduction`,
    saved.voice && 'clearer voice',
  ].filter(Boolean);

  const save = async (data, note) => {
    setSaving(true);
    await act(() => api.saveTimeline(project.id, data, note, true));
    setSaving(false);
  };

  const applyPolish = () => {
    const p = norm(polish);
    const data = { ...timeline.data };
    if (p.normalize || p.voice || p.denoise !== 'off') data.audio_polish = p;
    else delete data.audio_polish;
    save(data, 'Audio polish');
  };

  const addSound = (s) => {
    const audio = timeline.data.audio || [];
    const taken = new Set(audio.map((a) => a.id));
    let n = 1;
    while (taken.has(`sfx${n}`)) n += 1;
    const at = Math.max(0, Math.round(playhead() * 100) / 100);
    save({ ...timeline.data, audio: [...audio, { id: `sfx${n}`, sfx: s.id, start: at, volume: 0.6 }] },
      `Sound effect: ${s.name} at ${timecode(at)}`);
  };

  const preview = (s) => {
    const el = player.current;
    if (!el) return;
    el.src = `${API_BASE}/v2/sfx/${s.id}.wav`;
    el.play().catch(() => {});
  };

  return (
    <section className="colour-panel sound-panel" aria-label="Sound">
      <div className="captions-head">
        <h2 className="panel-title">Sound</h2>
        <span className="timeline-total">{summary.length ? summary.join(', ') : 'No audio polish'}</span>
        <button className="button-quiet" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? 'Hide sound' : 'Polish and effects'}
        </button>
      </div>
      {open && (
        <>
          <div className="caption-controls">
            <label className="checkbox">
              <input type="checkbox" checked={!!polish.normalize} onChange={(e) => setPolish({ ...polish, normalize: e.target.checked })} />
              <span>Loudness for social (−14 LUFS)</span>
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={!!polish.voice} onChange={(e) => setPolish({ ...polish, voice: e.target.checked })} />
              <span>Clearer voice</span>
            </label>
            <label>
              <span className="control-label">Background noise reduction</span>
              <select value={polish.denoise || 'off'} onChange={(e) => setPolish({ ...polish, denoise: e.target.value })}>
                {DENOISE.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <button className="button-primary" disabled={!dirty || saving} onClick={applyPolish}>
              {saving ? 'Saving…' : 'Apply'}
            </button>
          </div>

          <div className="sfx-list">
            <span className="control-label">Sound effects (added at the playhead, {timecode(playhead())})</span>
            <ul>
              {sounds.map((s) => (
                <li key={s.id} className="sfx">
                  <button className="button-quiet" onClick={() => preview(s)} aria-label={`Play ${s.name}`}>
                    ▶
                  </button>
                  <span className="sfx-name" title={s.description}>
                    {s.name}
                  </span>
                  <span className="fine-print sfx-desc">{s.description}</span>
                  <button className="button-secondary" disabled={saving} onClick={() => addSound(s)}>
                    Add
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <audio ref={player} preload="none" />
        </>
      )}
    </section>
  );
}
