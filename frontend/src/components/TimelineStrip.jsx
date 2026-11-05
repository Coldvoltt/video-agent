import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { shortDuration, timecode } from '../lib/format';
import ClipInspector from './ClipInspector';
import BrollInspector from './BrollInspector';

const CAPTION_NAMES = { karaoke: 'Karaoke', pop: 'Pop', boxed: 'Boxed', minimal: 'Minimal' };
const TEXT_STYLE_NAMES = { title: 'Title', label: 'Label', lower_third: 'Lower third' };

function gradeName(grade, names) {
  if (!grade) return null;
  const parts = [];
  if (grade.lut_preset) parts.push(grade.lut_preset.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()));
  if (grade.preset) parts.push(grade.preset === 'bw' ? 'Black and white' : grade.preset[0].toUpperCase() + grade.preset.slice(1));
  if (grade.lut_asset_id) parts.push((names[grade.lut_asset_id] || 'LUT').replace(/\.cube$/i, ''));
  const tweaks = ['brightness', 'contrast', 'saturation', 'temperature'].filter((k) => grade[k] != null);
  if (tweaks.length) parts.push(`adjusted ${tweaks.join(', ')}`);
  return parts.join(', ') || null;
}

/** Output-time spans of each clip, accounting for transition overlaps. */
function placeClips(clips) {
  let end = 0;
  return clips.map((c) => {
    const t = c.transition_in?.duration || 0;
    const start = end - t;
    end = start + (c.out - c.in);
    return { ...c, start, length: c.out - c.in, overlap: t };
  });
}

/** Greedy interval stacking: overlapping items go on separate rows. */
function stackRows(items) {
  const rowEnds = [];
  return items
    .slice()
    .sort((a, b) => a.start - b.start)
    .map((item) => {
      let row = rowEnds.findIndex((end) => end <= item.start);
      if (row === -1) row = rowEnds.length;
      rowEnds[row] = item.end;
      return { ...item, row };
    });
}

/** One labelled lane; bars are placed by output time on the same axis as the clips. */
function Lane({ name, items, total, tone, selectedId, onPick }) {
  const stacked = stackRows(items);
  const rows = Math.max(1, ...stacked.map((i) => i.row + 1));
  return (
    <div className="lane">
      <span className="lane-name">{name}</span>
      <div className="lane-track" style={{ height: rows * 22 }}>
        {stacked.map((item) => {
          const left = Math.max(0, Math.min(100, (item.start / total) * 100));
          const right = Math.max(left, Math.min(100, (item.end / total) * 100));
          return (
            onPick ? (
              <button
                key={item.id}
                className={`lane-bar lane-${tone} lane-pickable ${item.id === selectedId ? 'is-selected' : ''}`}
                style={{ left: `${left}%`, width: `${Math.max(right - left, 1.5)}%`, top: item.row * 22 + 2 }}
                title={item.title}
                onClick={() => onPick(item.id)}
              >
                {item.label}
              </button>
            ) : (
            <span
              key={item.id}
              className={`lane-bar lane-${tone}`}
              style={{ left: `${left}%`, width: `${Math.max(right - left, 1.5)}%`, top: item.row * 22 + 2 }}
              title={item.title}
            >
              {item.label}
            </span>
            )
          );
        })}
      </div>
    </div>
  );
}

export default function TimelineStrip({
  project, colors, act, onSeek, draft, onDraftChange, onSaveDraft, onDiscardDraft, selectedId, onSelect, playhead,
  onUndo, onRedo, saving,
}) {
  const current = project?.timeline;
  const versions = project?.timeline_versions || [];
  const [viewing, setViewing] = useState(null); // an older version's timeline row, or null = current
  const [changes, setChanges] = useState(null);

  useEffect(() => setViewing(null), [current?.version]);

  const shownVersion = viewing?.version ?? current?.version;
  useEffect(() => {
    if (!project?.id || !shownVersion) return undefined;
    let live = true;
    api.changes(project.id, shownVersion).then((c) => live && setChanges(c)).catch(() => live && setChanges(null));
    return () => {
      live = false;
    };
  }, [project?.id, shownVersion]);

  if (!project || !current) return null;

  const row = viewing || current;
  const editing = !!draft && !viewing;
  const data = editing ? draft : row.data;
  const history = project.history || {};
  const placed = placeClips(data.clips);
  const total = placed.length ? placed[placed.length - 1].start + placed[placed.length - 1].length : 0;
  const isOld = viewing && viewing.version !== current.version;
  const names = Object.fromEntries((project.assets || []).map((a) => [a.id, a.filename || 'media']));
  const look = gradeName(data.grade, names);
  const hasLayers = data.captions || data.text?.length || data.overlays?.length || data.audio?.length
    || data.broll?.length || data.zooms?.length;
  const selectedBroll = (data.broll || []).some((b) => b.id === selectedId);

  const showVersion = async (v) => {
    if (Number(v) === current.version) return setViewing(null);
    const r = await act(() => api.getTimeline(project.id, Number(v)));
    if (r) setViewing(r);
  };

  return (
    <section className="timeline" aria-label="Timeline">
      <div className="timeline-head">
        <h2 className="panel-title">Timeline</h2>
        <span className="timeline-total">
          {placed.length} {placed.length === 1 ? 'clip' : 'clips'}, {timecode(total)}
        </span>
        {look && <span className="look">Colour: {look}</span>}
        <span className="history-buttons">
          <button className="button-quiet" onClick={onUndo} disabled={!draft && !history.can_undo} title="Undo (Ctrl+Z)">
            Undo
          </button>
          <button className="button-quiet" onClick={onRedo} disabled={!!draft || !history.can_redo} title="Redo (Ctrl+Shift+Z)">
            Redo
          </button>
        </span>
        <label className="version-picker">
          <span className="visually-hidden">Version</span>
          <select value={row.version} disabled={!!draft} onChange={(e) => showVersion(e.target.value)}>
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                Version {v.version}
                {v.version === current.version ? ' (current)' : ''}
                {v.note ? `: ${v.note.replace(/^Agent: /, '')}`.slice(0, 70) : ''}
              </option>
            ))}
          </select>
        </label>
      </div>

      {editing && (
        <div className="draft-bar" role="status">
          <span>Unsaved timeline edits. The live preview already shows them.</span>
          <button className="button-primary" onClick={onSaveDraft} disabled={saving}>
            {saving ? 'Saving…' : 'Save edits'}
          </button>
          <button className="button-quiet" onClick={onDiscardDraft} disabled={saving}>
            Discard
          </button>
        </div>
      )}

      {!editing && changes?.changes?.length > 0 && (
        <details className="changes">
          <summary>
            What changed in version {changes.version}
            {changes.against ? ` (since version ${changes.against})` : ''}
          </summary>
          <ul>
            {changes.changes.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </details>
      )}

      {isOld && (
        <p className="notice notice-inline">
          You’re looking at version {viewing.version}.{' '}
          <button className="link-button" onClick={() => act(() => api.restoreTimeline(project.id, viewing.version))}>
            Make it current
          </button>{' '}
          <button className="link-button" onClick={() => setViewing(null)}>
            Back to current
          </button>
        </p>
      )}

      <div className="lane lane-clips">
      <span className="lane-name">Clips</span>
      <ol className="strip">
        {placed.map((c) => {
          const words = row.clip_text?.[c.id];
          return (
            <li
              key={c.id}
              className={`clip ${c.overlap ? 'has-transition' : ''} ${c.id === selectedId && !isOld ? 'is-selected' : ''}`}
              style={{ flexGrow: c.length - c.overlap / 2, '--clip': colors[c.asset_id] || '#888' }}
            >
              {c.overlap > 0 && (
                <span className="transition-mark" title={`${c.transition_in.type}, ${c.overlap}s`} aria-hidden="true" />
              )}
              <button
                className="clip-button"
                disabled={isOld}
                aria-pressed={c.id === selectedId}
                onClick={() => {
                  onSelect(c.id);
                  onSeek(c.start);
                }}
                title={`${timecode(c.start)}–${timecode(c.start + c.length)}${c.zoom > 1 ? `
Punch-in ${c.zoom}×` : ''}${
                  c.overlap ? `\nTransition in: ${c.transition_in.type}` : ''
                }${words ? `\n${words}` : ''}`}
              >
                <span className="clip-words">{words || (editing ? 'Edited' : 'No speech')}</span>
                <span className="clip-length">
                  {shortDuration(c.length)}
                  {c.zoom > 1 && <span className="clip-zoom"> {c.zoom.toFixed(2)}×</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      </div>

      {selectedId && !isOld && selectedBroll && (
        <BrollInspector
          project={project}
          timeline={data}
          layerId={selectedId}
          onChange={onDraftChange}
          onSelect={onSelect}
          onSeek={onSeek}
        />
      )}

      {selectedId && !isOld && !selectedBroll && (
        <ClipInspector
          project={project}
          timeline={data}
          clipId={selectedId}
          onChange={onDraftChange}
          onSelect={onSelect}
          playhead={playhead}
        />
      )}

      {hasLayers && total > 0 && (
        <div className="lanes">
          {data.captions && (
            <Lane name="Captions" tone="captions" total={total} items={[{
              id: 'captions', start: 0, end: total,
              label: `${CAPTION_NAMES[data.captions.style] || data.captions.style}, ${data.captions.position || 'bottom'}`,
            }]} />
          )}
          {data.text?.length > 0 && (
            <Lane name="Text" tone="text" total={total} items={data.text.map((t) => ({
              id: t.id, start: t.start, end: t.end, label: t.style === 'keyword' ? t.text.toUpperCase() : t.text,
              title: `${TEXT_STYLE_NAMES[t.style] || t.style}: “${t.text}” ${timecode(t.start)}–${timecode(t.end)}`,
            }))} />
          )}
          {data.overlays?.length > 0 && (
            <Lane name="Overlays" tone="overlay" total={total} items={data.overlays.map((o) => ({
              id: o.id, start: o.start, end: o.end, label: names[o.asset_id],
              title: `${names[o.asset_id]} ${timecode(o.start)}–${timecode(o.end)}, ${o.position}`,
            }))} />
          )}
          {data.broll?.length > 0 && (
            <Lane name="B-roll" tone="broll" total={total} selectedId={!isOld ? selectedId : null}
              onPick={isOld ? undefined : (id) => {
                onSelect(id);
                const b = data.broll.find((x) => x.id === id);
                if (b) onSeek(Math.max(0, b.start - 0.3));
              }}
              items={data.broll.map((b) => ({
                id: b.id, start: b.start, end: b.start + b.duration, label: names[b.asset_id],
                title: `${names[b.asset_id]} ${timecode(b.start)}–${timecode(b.start + b.duration)}`,
              }))} />
          )}
          {data.zooms?.length > 0 && (
            <Lane name="Zoom" tone="zoom" total={total} items={data.zooms.map((z) => ({
              id: z.id, start: z.start, end: z.end, label: `${z.scale}×`,
              title: `Punch-in ${z.scale}× ${timecode(z.start)}–${timecode(z.end)}`,
            }))} />
          )}
          {data.audio?.length > 0 && (
            <Lane name="Audio" tone="audio" total={total} items={data.audio.map((a) => {
              const sfx = a.sfx ? a.sfx[0].toUpperCase() + a.sfx.slice(1) : null;
              const length = sfx ? 0.6 : a.loop || a.out == null ? total - a.start : a.out - (a.in || 0);
              const name = sfx || names[a.asset_id];
              return {
                id: a.id, start: a.start, end: a.start + length,
                label: `${name}${a.duck ? ', lowered under speech' : ''}`,
                title: `${sfx ? 'Sound effect ' : ''}${name} at ${timecode(a.start)}, volume ${Math.round(a.volume * 100)}%`,
              };
            })} />
          )}
        </div>
      )}

      <div className="ruler" aria-hidden="true">
        <span />
        <span>0:00</span>
        <span>{timecode(total / 2)}</span>
        <span>{timecode(total)}</span>
      </div>
    </section>
  );
}
