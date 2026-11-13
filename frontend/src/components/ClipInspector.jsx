import { useState } from 'react';
import { api } from '../lib/api';
import { shortDuration, timecode } from '../lib/format';
import { clipSpans, deleteClip, moveClip, setClipTimes, splitAt, updateClip } from '../lib/timelineEdit';

const TRANSITIONS = [
  ['', 'Cut (no transition)'],
  ['fade', 'Crossfade'],
  ['dissolve', 'Dissolve'],
  ['fadeblack', 'Fade through black'],
  ['fadewhite', 'Fade through white'],
  ['wipeleft', 'Wipe left'],
  ['slideleft', 'Slide left'],
  ['slideright', 'Slide right'],
  ['smoothleft', 'Smooth left'],
  ['circleopen', 'Circle open'],
  ['radial', 'Radial'],
  ['pixelize', 'Pixelize'],
];
const ZOOMS = [
  ['', 'None'],
  ['1.1', '1.1× closer'],
  ['1.2', '1.2× closer'],
  ['1.3', '1.3× closer'],
  ['1.5', '1.5× closer'],
];

const BACKGROUNDS = [
  ['', 'Original'],
  ['blur', 'Blurred'],
  ['color', 'Solid colour'],
  ['media', 'Image or video'],
];

/** Replace what's behind the person: AI cut-out by default, or a colour key for green/blue screen footage. */
function BackgroundControls({ project, timeline, clip, asset, change }) {
  const [detecting, setDetecting] = useState(false);
  const [note, setNote] = useState('');
  const bg = clip.background;
  if (asset?.kind !== 'video' && asset?.kind !== 'multicam') return null;
  // multicam: the picture (and so the screen colour) comes from the chosen camera's own file
  const angles = asset.multicam?.angles;
  const pictureAngle = angles?.length ? angles[Math.min(clip.angle || 0, angles.length - 1)] : null;
  const pictureId = pictureAngle ? pictureAngle.asset_id : clip.asset_id;
  const pictureAt = clip.in + (pictureAngle ? pictureAngle.offset : 0) + 0.5;
  const media = project.assets.filter(
    (a) => a.status === 'ready' && (a.kind === 'image' || a.kind === 'video') && a.role !== 'reference' && a.id !== clip.asset_id
      && !(asset.multicam?.angles || []).some((x) => x.asset_id === a.id),
  );
  const set = (next) => change(updateClip(timeline, clip.id, { background: next }));
  const pick = (kind) => {
    if (!kind) return set(null);
    const base = { method: bg?.method || 'ai', ...(bg?.method === 'chroma' ? { key_color: bg.key_color, key_tolerance: bg.key_tolerance } : {}) };
    if (kind === 'blur') return set({ ...base, replace_with: 'blur', blur: 0.5 });
    if (kind === 'color') return set({ ...base, replace_with: 'color', color: '#1E2A44' });
    if (!media.length) return setNote('Add B-roll, a stock clip or an image first.');
    return set({ ...base, replace_with: 'media', asset_id: media[0].id });
  };
  const greenScreen = async (on) => {
    setNote('');
    if (!on) return set({ ...bg, method: 'ai', key_color: undefined, key_tolerance: undefined });
    setDetecting(true);
    try {
      const color = await api.screenColor(project.id, pictureId, pictureAt);
      if (!color) setNote("This clip doesn't look like a green or blue screen; pick the screen colour yourself.");
      set({ ...bg, method: 'chroma', key_color: color || '#00FF00', key_tolerance: bg.key_tolerance ?? 0.12 });
    } finally {
      setDetecting(false);
    }
  };

  return (
    <div className="background-controls">
      <label>
        <span className="control-label">Background</span>
        <select value={bg?.replace_with || ''} onChange={(e) => pick(e.target.value)}>
          {BACKGROUNDS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </label>
      {bg?.replace_with === 'blur' && (
        <label>
          <span className="control-label">Blur {Math.round(bg.blur * 100)}%</span>
          <input type="range" min="0.05" max="1" step="0.05" value={bg.blur} onChange={(e) => set({ ...bg, blur: Number(e.target.value) })} />
        </label>
      )}
      {bg?.replace_with === 'color' && (
        <label>
          <span className="control-label">Colour</span>
          <input type="color" value={bg.color} onChange={(e) => set({ ...bg, color: e.target.value.toUpperCase() })} />
        </label>
      )}
      {bg?.replace_with === 'media' && (
        <label>
          <span className="control-label">Show behind you</span>
          <select value={bg.asset_id} onChange={(e) => set({ ...bg, asset_id: e.target.value })}>
            {media.map((a) => (
              <option key={a.id} value={a.id}>
                {a.filename || a.id} ({a.kind === 'image' ? 'image' : 'video'})
              </option>
            ))}
          </select>
        </label>
      )}
      {bg && (
        <label className="check-row">
          <input type="checkbox" checked={bg.method === 'chroma'} disabled={detecting} onChange={(e) => greenScreen(e.target.checked)} />
          <span>{detecting ? 'Finding the screen colour…' : 'Shot on a green or blue screen'}</span>
        </label>
      )}
      {bg?.method === 'chroma' && (
        <>
          <label>
            <span className="control-label">Screen colour</span>
            <input type="color" value={bg.key_color} onChange={(e) => set({ ...bg, key_color: e.target.value.toUpperCase() })} />
          </label>
          <label>
            <span className="control-label">Key strength {Math.round(bg.key_tolerance * 100)}</span>
            <input type="range" min="0.03" max="0.5" step="0.01" value={bg.key_tolerance}
              onChange={(e) => set({ ...bg, key_tolerance: Number(e.target.value) })} />
          </label>
        </>
      )}
      <p className="fine-print">
        {note ||
          (bg && bg.method !== 'chroma'
            ? 'The person is cut out with AI (MODNet). The first preview after this takes a few minutes; later ones reuse it.'
            : bg
              ? 'Raise the key strength if green edges remain; lower it if parts of you disappear.'
              : 'Blur, recolour or replace what is behind you.')}
      </p>
    </div>
  );
}

export default function ClipInspector({ project, timeline, clipId, onChange, onSelect, playhead }) {
  const clips = timeline.clips;
  const index = clips.findIndex((c) => c.id === clipId);
  if (index < 0) return null;
  const clip = clips[index];
  const asset = project.assets.find((a) => a.id === clip.asset_id);
  const span = clipSpans(clips)[index];
  const sourceEnd = asset?.kind === 'video' ? asset.duration : undefined;
  const t = playhead();
  const playheadInClip = t > span.start + 0.05 && t < span.end - 0.05;
  const sourceAtPlayhead = clip.in + (t - span.start);

  const change = (next) => next && next !== timeline && onChange(next);

  return (
    <section className="clip-inspector" aria-label="Selected clip">
      <div className="inspector-head">
        <h3 className="panel-title">
          Clip {index + 1} of {clips.length}
        </h3>
        <span className="timeline-total">
          {asset?.filename || 'media'}, {shortDuration(clip.out - clip.in)}, plays {timecode(span.start)}–{timecode(span.end)}
        </span>
        <button className="button-quiet" onClick={() => onSelect(null)}>
          Close
        </button>
      </div>

      <div className="inspector-grid">
        <label>
          <span className="control-label">Starts at (in the source, seconds)</span>
          <input
            type="number"
            step="0.1"
            min="0"
            value={clip.in}
            onChange={(e) => change(setClipTimes(timeline, clip.id, Number(e.target.value), clip.out, sourceEnd))}
          />
        </label>
        <label>
          <span className="control-label">Ends at (in the source, seconds)</span>
          <input
            type="number"
            step="0.1"
            min="0"
            max={sourceEnd}
            value={clip.out}
            onChange={(e) => change(setClipTimes(timeline, clip.id, clip.in, Number(e.target.value), sourceEnd))}
          />
        </label>
        {asset?.multicam?.angles?.length > 0 && (
          <label>
            <span className="control-label">Camera</span>
            <select value={clip.angle || 0} onChange={(e) => change(updateClip(timeline, clip.id, { angle: Number(e.target.value) }))}>
              {asset.multicam.angles.map((x, i) => (
                <option key={x.asset_id} value={i}>
                  {x.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          <span className="control-label">Punch-in</span>
          <select
            value={clip.zoom ? String(clip.zoom) : ''}
            onChange={(e) => change(updateClip(timeline, clip.id, { zoom: e.target.value ? Number(e.target.value) : null }))}
          >
            {ZOOMS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
            {clip.zoom && !ZOOMS.some(([v]) => Number(v) === clip.zoom) && (
              <option value={String(clip.zoom)}>{clip.zoom}× closer</option>
            )}
          </select>
        </label>
        <label>
          <span className="control-label">Transition into this clip</span>
          <select
            disabled={index === 0}
            value={clip.transition_in?.type || ''}
            onChange={(e) =>
              change(
                updateClip(timeline, clip.id, {
                  transition_in: e.target.value ? { type: e.target.value, duration: clip.transition_in?.duration || 0.5 } : null,
                }),
              )
            }
          >
            {TRANSITIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="control-label">Volume {Math.round((clip.volume ?? 1) * 100)}%</span>
          <input
            type="range"
            min="0"
            max="2"
            step="0.05"
            value={clip.volume ?? 1}
            onChange={(e) => change(updateClip(timeline, clip.id, { volume: Number(e.target.value) === 1 ? null : Number(e.target.value) }))}
          />
        </label>
      </div>

      <BackgroundControls project={project} timeline={timeline} clip={clip} asset={asset} change={change} />

      <div className="inspector-actions">
        <button
          className="button-secondary"
          disabled={!playheadInClip}
          title={playheadInClip ? undefined : 'Move the playhead inside this clip first'}
          onClick={() => change(splitAt(timeline, t))}
        >
          Split at playhead
        </button>
        <button
          className="button-secondary"
          disabled={!playheadInClip}
          onClick={() => change(setClipTimes(timeline, clip.id, sourceAtPlayhead, clip.out, sourceEnd))}
        >
          Start here
        </button>
        <button
          className="button-secondary"
          disabled={!playheadInClip}
          onClick={() => change(setClipTimes(timeline, clip.id, clip.in, sourceAtPlayhead, sourceEnd))}
        >
          End here
        </button>
        <button className="button-secondary" disabled={index === 0} onClick={() => change(moveClip(timeline, clip.id, -1))}>
          Move earlier
        </button>
        <button
          className="button-secondary"
          disabled={index === clips.length - 1}
          onClick={() => change(moveClip(timeline, clip.id, 1))}
        >
          Move later
        </button>
        <button
          className="button-quiet button-danger"
          disabled={clips.length <= 1}
          onClick={() => {
            change(deleteClip(timeline, clip.id));
            onSelect(null);
          }}
        >
          Delete clip
        </button>
      </div>
    </section>
  );
}
