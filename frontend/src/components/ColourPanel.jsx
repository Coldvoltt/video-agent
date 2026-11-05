import { useEffect, useState } from 'react';
import { API_BASE, api } from '../lib/api';

/** Pick a built-in look by seeing your own footage through each one. */
export default function ColourPanel({ project, looks, act }) {
  const timeline = project?.timeline;
  const grade = timeline?.data?.grade || {};
  const [open, setOpen] = useState(false);
  const [thumbs, setThumbs] = useState(null);
  const [loading, setLoading] = useState(false);
  const [choice, setChoice] = useState(null); // look id or 'none'
  const [strength, setStrength] = useState(1);
  const [saving, setSaving] = useState(false);

  const current = grade.lut_preset || 'none';
  const currentStrength = grade.lut_strength ?? 1;

  useEffect(() => {
    setChoice(null);
    setStrength(currentStrength);
  }, [timeline?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open || !project?.id) return undefined;
    let live = true;
    setLoading(true);
    act(() => api.lookThumbnails(project.id)).then((t) => {
      if (live) {
        setThumbs(t);
        setLoading(false);
      }
    });
    return () => {
      live = false;
    };
  }, [open, project?.id, timeline?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!timeline) return null;

  const byId = Object.fromEntries((looks || []).map((l) => [l.id, l]));
  const selected = choice ?? current;
  const dirty = selected !== current || (selected !== 'none' && Math.abs(strength - currentStrength) > 0.001);
  const customLut = grade.lut_asset_id && project.assets.find((a) => a.id === grade.lut_asset_id);

  const apply = async () => {
    setSaving(true);
    const next = { ...grade };
    delete next.lut_asset_id;
    if (selected === 'none') {
      delete next.lut_preset;
      delete next.lut_strength;
    } else {
      next.lut_preset = selected;
      if (strength < 0.999) next.lut_strength = Math.round(strength * 100) / 100;
      else delete next.lut_strength;
    }
    const data = { ...timeline.data };
    if (Object.keys(next).length) data.grade = next;
    else delete data.grade;
    const note = selected === 'none' ? 'Removed the look' : `Look: ${byId[selected]?.name || selected}`;
    await act(() => api.saveTimeline(project.id, data, note, true));
    setSaving(false);
  };

  return (
    <section className="colour-panel" aria-label="Colour">
      <div className="captions-head">
        <h2 className="panel-title">Colour</h2>
        <span className="timeline-total">
          {current !== 'none'
            ? `${byId[current]?.name || current}${currentStrength < 1 ? ` at ${Math.round(currentStrength * 100)}%` : ''}`
            : customLut
              ? (customLut.filename || 'Custom LUT').replace(/\.cube$/i, '')
              : 'No look applied'}
        </span>
        <button className="button-quiet" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? 'Hide looks' : 'Choose a look'}
        </button>
      </div>

      {open && (
        <>
          {customLut && (
            <p className="fine-print">
              Choosing a look replaces the current LUT ({(customLut.filename || 'custom').replace(/\.cube$/i, '')}).
            </p>
          )}
          {loading && !thumbs ? (
            <p className="empty-hint">Making previews of each look from your footage…</p>
          ) : (
            <ul className="look-grid">
              {(thumbs?.looks || []).map((t) => {
                const info = byId[t.id];
                return (
                  <li key={t.id}>
                    <button
                      className={`look ${selected === t.id ? 'is-selected' : ''}`}
                      aria-pressed={selected === t.id}
                      title={info?.description || 'No look'}
                      onClick={() => setChoice(t.id)}
                    >
                      <img src={t.image} alt="" />
                      <span>{t.name}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="look-actions">
            {selected !== 'none' && (
              <label className="look-strength">
                <span className="control-label">Strength {Math.round(strength * 100)}%</span>
                <input
                  type="range"
                  min="0.1"
                  max="1"
                  step="0.05"
                  value={strength}
                  onChange={(e) => setStrength(Number(e.target.value))}
                />
              </label>
            )}
            {selected !== 'none' && byId[selected] && (
              <p className="fine-print look-description">{byId[selected].description}</p>
            )}
            <button className="button-primary" disabled={!dirty || saving} onClick={apply}>
              {saving ? 'Applying…' : selected === 'none' ? 'Remove look' : 'Apply look'}
            </button>
            {selected !== 'none' && byId[selected] && (
              <a className="link-button" href={`${API_BASE}/v2/luts/${selected}.cube`} download>
                Download .cube
              </a>
            )}
          </div>
        </>
      )}
    </section>
  );
}
