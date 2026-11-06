/**
 * Pure helpers for editing timeline JSON by hand. Each returns a new timeline (never mutates).
 * Clip times are source seconds; spans are output seconds (transitions overlap neighbours).
 */

export const MIN_CLIP = 0.1;

export function clipSpans(clips) {
  let end = 0;
  return clips.map((c) => {
    const start = end - (c.transition_in?.duration || 0);
    end = start + (c.out - c.in);
    return { start, end };
  });
}

export function totalDuration(clips) {
  const spans = clipSpans(clips);
  return spans.length ? spans[spans.length - 1].end : 0;
}

/** Index of the clip playing at output time t (the later clip wins inside a transition). */
export function clipIndexAt(clips, t) {
  const spans = clipSpans(clips);
  for (let i = spans.length - 1; i >= 0; i--) if (t >= spans[i].start) return Math.min(i, clips.length - 1);
  return 0;
}

function uniqueId(clips, base) {
  const taken = new Set(clips.map((c) => c.id));
  const stem = base.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 56) || 'clip';
  let n = 2;
  let id = `${stem}-${n}`;
  while (taken.has(id)) id = `${stem}-${++n}`;
  return id;
}

const round = (x) => Math.round(x * 1000) / 1000;

/** Shorten transitions that no longer fit after a clip got shorter. */
function fitTransitions(clips) {
  return clips.map((c, i) => {
    if (!c.transition_in) return c;
    if (i === 0) {
      const { transition_in, ...rest } = c; // eslint-disable-line no-unused-vars
      return rest;
    }
    const shortest = Math.min(c.out - c.in, clips[i - 1].out - clips[i - 1].in);
    const d = Math.min(c.transition_in.duration, round(shortest * 0.45));
    if (d < 0.1) {
      const { transition_in, ...rest } = c; // eslint-disable-line no-unused-vars
      return rest;
    }
    return { ...c, transition_in: { ...c.transition_in, duration: d } };
  });
}

const withClips = (timeline, clips) => ({ ...timeline, clips: fitTransitions(clips) });

/** Move one edge of a clip by `delta` seconds, clamped to the source and a minimum length. */
export function trimClip(timeline, id, edge, delta, sourceDuration) {
  const clips = timeline.clips.map((c) => {
    if (c.id !== id) return c;
    if (edge === 'in') {
      const v = Math.min(Math.max(0, c.in + delta), c.out - MIN_CLIP);
      return { ...c, in: round(v) };
    }
    const max = sourceDuration ?? Infinity;
    const v = Math.max(Math.min(max, c.out + delta), c.in + MIN_CLIP);
    return { ...c, out: round(v) };
  });
  return withClips(timeline, clips);
}

export function setClipTimes(timeline, id, inS, outS, sourceDuration) {
  const clips = timeline.clips.map((c) => {
    if (c.id !== id) return c;
    const a = Math.max(0, Math.min(inS, outS - MIN_CLIP));
    const b = Math.max(a + MIN_CLIP, Math.min(sourceDuration ?? Infinity, outS));
    return { ...c, in: round(a), out: round(b) };
  });
  return withClips(timeline, clips);
}

/** Split the clip under output time t into two clips. Returns null if t is too close to an edge. */
export function splitAt(timeline, t) {
  const clips = timeline.clips;
  const i = clipIndexAt(clips, t);
  const c = clips[i];
  const span = clipSpans(clips)[i];
  const at = round(c.in + (t - span.start));
  if (at - c.in < MIN_CLIP || c.out - at < MIN_CLIP) return null;
  const { transition_in, ...rest } = c; // eslint-disable-line no-unused-vars
  const first = { ...c, out: at };
  const second = { ...rest, id: uniqueId(clips, c.id), in: at };
  return withClips(timeline, [...clips.slice(0, i), first, second, ...clips.slice(i + 1)]);
}

export function moveClip(timeline, id, direction) {
  const clips = [...timeline.clips];
  const i = clips.findIndex((c) => c.id === id);
  const j = i + direction;
  if (i < 0 || j < 0 || j >= clips.length) return timeline;
  [clips[i], clips[j]] = [clips[j], clips[i]];
  return withClips(timeline, clips);
}

export function deleteClip(timeline, id) {
  if (timeline.clips.length <= 1) return timeline;
  return withClips(
    timeline,
    timeline.clips.filter((c) => c.id !== id),
  );
}

export function updateClip(timeline, id, changes) {
  const clips = timeline.clips.map((c) => {
    if (c.id !== id) return c;
    const next = { ...c, ...changes };
    for (const k of Object.keys(next)) if (next[k] === null || next[k] === undefined) delete next[k];
    return next;
  });
  return withClips(timeline, clips);
}

export function removeLayer(timeline, kind, id) {
  return { ...timeline, [kind]: (timeline[kind] || []).filter((x) => x.id !== id) };
}
