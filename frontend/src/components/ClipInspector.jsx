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
