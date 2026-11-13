import { useState } from 'react';
import { api } from '../lib/api';
import { timecode } from '../lib/format';
import { BLUR_LEVELS, EFFECT_NAMES } from './EffectInspector';

const HELP = {
  denoise: 'Cleans up grainy or low-light footage.',
  grain: 'A filmic, analogue texture.',
  blur: 'Soften the picture, or hide part of it.',
};

/** Add a picture effect to the whole video or from the playhead (saves a version with a preview). */
export default function EffectPanel({ project, act, playhead }) {
  const timeline = project?.timeline;
  const [saving, setSaving] = useState(false);
  const [blurLevel, setBlurLevel] = useState(0.5);
  if (!timeline) return null;
  const list = timeline.data.effects || [];

  const add = async (type, fromPlayhead) => {
    const taken = new Set(list.map((x) => x.id));
    let n = 1;
    while (taken.has(`${type}${n}`)) n += 1;
    const layer = { id: `${type}${n}`, type, strength: type === 'grain' ? 0.3 : type === 'blur' ? blurLevel : 0.5 };
    let note = `${EFFECT_NAMES[type]} on the whole video`;
    if (fromPlayhead) {
      const at = Math.max(0, Math.round(playhead() * 100) / 100);
      Object.assign(layer, { start: at, end: Math.round((at + 3) * 100) / 100 });
      note = `${EFFECT_NAMES[type]} at ${timecode(at)}`;
    }
    setSaving(true);
    await act(() => api.saveTimeline(project.id, { ...timeline.data, effects: [...list, layer] }, note, true));
    setSaving(false);
  };

  return (
    <section className="colour-panel effect-panel" aria-label="Effects">
      <div className="captions-head">
        <h2 className="panel-title">Effects</h2>
        <span className="timeline-total">
          {list.length ? `${list.length} on the video. Click one on the timeline to adjust it.` : 'Noise removal, grain, blur'}
        </span>
      </div>
      <ul className="effect-list">
        {Object.keys(EFFECT_NAMES).map((type) => (
          <li key={type}>
            <div>
              <span className="effect-name">{EFFECT_NAMES[type]}</span>
              <span className="fine-print">{HELP[type]}</span>
              {type === 'blur' && (
                <label className="blur-level">
                  <span className="control-label">Blur level</span>
                  <select value={blurLevel} onChange={(e) => setBlurLevel(Number(e.target.value))}>
                    {BLUR_LEVELS.map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            <button className="button-secondary" disabled={saving} onClick={() => add(type, false)}>
              Whole video
            </button>
            <button className="button-quiet" disabled={saving} onClick={() => add(type, true)}>
              3 s at playhead
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
