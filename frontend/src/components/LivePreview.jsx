import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { API_BASE, assetFileUrl, stickerUrl } from '../lib/api';
import LottieSticker from './LottieSticker';
import { timecode } from '../lib/format';
import { clipIndexAt, clipSpans } from '../lib/timelineEdit';

/*
 * Instant, approximate playback of a timeline in the browser: plays the source clips
 * (browser-friendly proxies) in order and draws captions, text, image overlays, punch-ins
 * and basic colour on top. The rendered preview stays the exact result.
 */

// Same numbers as the renderer's presets (editor/renderer.py), translated to CSS filters.
const GRADE_PRESETS = {
  natural: { contrast: 1.03, saturation: 1.05 },
  warm: { temperature: 0.5, saturation: 1.08, contrast: 1.03 },
  cool: { temperature: -0.5, saturation: 0.95, contrast: 1.03 },
  punchy: { contrast: 1.15, saturation: 1.3, brightness: 0.01 },
  film: { contrast: 0.92, saturation: 0.85, temperature: 0.2, brightness: 0.02 },
  moody: { contrast: 1.12, saturation: 0.75, temperature: -0.3, brightness: -0.04 },
  bw: { saturation: 0, contrast: 1.12 },
  vintage: { contrast: 0.9, saturation: 0.7, temperature: 0.45, brightness: 0.03 },
};

function cssFilter(grade, looks) {
  if (!grade) return undefined;
  const look = grade.lut_preset && (looks || []).find((l) => l.id === grade.lut_preset);
  const v = { ...(GRADE_PRESETS[grade.preset] || {}) };
  for (const k of ['brightness', 'contrast', 'saturation', 'temperature']) if (grade[k] != null) v[k] = grade[k];
  const parts = [];
  if (v.brightness) parts.push(`brightness(${1 + v.brightness * 1.5})`);
  if (v.contrast && v.contrast !== 1) parts.push(`contrast(${v.contrast})`);
  if (v.saturation != null && v.saturation !== 1) parts.push(`saturate(${v.saturation})`);
  if (v.temperature > 0) parts.push(`sepia(${Math.min(0.5, v.temperature * 0.35)})`);
  if (v.temperature < 0) parts.push(`hue-rotate(${Math.round(v.temperature * -12)}deg)`);
  // A built-in look: its rough CSS twin (strength below ~40% is closer to no filter at all).
  if (look && (grade.lut_strength ?? 1) >= 0.4) parts.push(look.css);
  return parts.join(' ') || undefined;
}

// The renderer (libass) sizes text by the font's full line height; CSS sizes it by the em box.
// These are unitsPerEm / (winAscent + winDescent) of the bundled fonts, so both draw the same size.
const EM = { anton: 0.577, montserrat: 0.64 };

const CAPTION_LOOK = {
  karaoke: { font: "'Montserrat', sans-serif", rel: 1.4 * EM.montserrat },
  pop: { font: "'Anton', sans-serif", rel: 1.9 * EM.anton },
  boxed: { font: "'Montserrat', sans-serif", rel: 1.15 * EM.montserrat },
  minimal: { font: "'Montserrat', sans-serif", rel: 0.85 * EM.montserrat },
};
const TEXT_SIZE = {
  title: 0.16 * EM.anton,
  label: 0.07 * EM.montserrat,
  lower_third: 0.06 * EM.montserrat,
  keyword: 0.3 * EM.anton,
};

const GRID = {
  'top-left': ['flex-start', 'flex-start'], top: ['flex-start', 'center'], 'top-right': ['flex-start', 'flex-end'],
  left: ['center', 'flex-start'], center: ['center', 'center'], right: ['center', 'flex-end'],
  'bottom-left': ['flex-end', 'flex-start'], bottom: ['flex-end', 'center'], 'bottom-right': ['flex-end', 'flex-end'],
};

/** One B-roll cutaway, kept in step with the timeline clock. */
function BrollCutaway({ layer, asset, url, t, playing, fit }) {
  const ref = useRef(null);
  const target = (layer.in || 0) + Math.max(0, t - layer.start);
  useEffect(() => {
    const el = ref.current;
    if (!el || asset.kind !== 'video') return;
    if (el.readyState >= 1 && Math.abs(el.currentTime - target) > 0.3) el.currentTime = target;
    el.volume = Math.min(1, layer.volume || 0);
    el.muted = !(layer.volume > 0);
    if (playing && el.paused) el.play().catch(() => {});
    if (!playing && !el.paused) el.pause();
  });
  const p = Math.min(1, Math.max(0, (t - layer.start) / layer.duration));
  const motion = {
    push_in: `scale(${1 + 0.1 * p})`,
    pull_out: `scale(${1.1 - 0.1 * p})`,
    pan_left: `scale(1.12) translateX(${(0.5 - p) * 8}%)`,
    pan_right: `scale(1.12) translateX(${(p - 0.5) * 8}%)`,
  }[layer.motion || 'push_in'];
  const fade = layer.fade ?? 0.15;
  const opacity = fade > 0 ? Math.min(1, (t - layer.start) / fade, (layer.start + layer.duration - t) / fade) : 1;
  // No z-index: page order already puts cutaways above the speaker and below logos, text and captions.
  const style = { objectFit: fit === 'contain' ? 'contain' : 'cover', transform: motion, opacity };
  return asset.kind === 'image' ? (
    <img className="is-shown" alt="" src={url} style={style} />
  ) : (
    <video ref={ref} className="is-shown" src={url} playsInline preload="auto" style={{ ...style, objectFit: 'cover' }}
      onLoadedMetadata={(e) => (e.currentTarget.currentTime = target)} />
  );
}

function setTime(el, time) {
  if (el.readyState >= 1) el.currentTime = time;
  else el.addEventListener('loadedmetadata', () => (el.currentTime = time), { once: true });
}

const LivePreview = forwardRef(function LivePreview({ project, timeline, lines, looks, onTime }, ref) {
  const clips = timeline.clips;
  const spans = useMemo(() => clipSpans(clips), [clips]);
  const total = spans.length ? spans[spans.length - 1].end : 0;
  const assets = useMemo(() => Object.fromEntries(project.assets.map((a) => [a.id, a])), [project.assets]);
  const out = timeline.output || { width: 1920, height: 1080, fit: 'contain' };

  const vids = [useRef(null), useRef(null)];
  const audios = useRef({});
  const frameRef = useRef(null);
  const raf = useRef(0);
  const st = useRef({ index: 0, active: 0, t: 0, playing: false, last: 0 });
  const [view, setView] = useState({ index: 0, active: 0, t: 0, playing: false });
  const [scale, setScale] = useState(0.3); // frame pixels per output pixel

  const sync = () => {
    setView({ index: st.current.index, active: st.current.active, t: st.current.t, playing: st.current.playing });
    onTime?.(st.current.t);
  };
  const isImage = (i) => assets[clips[i]?.asset_id]?.kind === 'image';

  const prepare = useCallback(
    (el, i, offset) => {
      const clip = clips[i];
      const asset = clip && assets[clip.asset_id];
      if (!el || !asset || asset.kind !== 'video') return;
      const url = assetFileUrl(project.id, asset);
      if (el.dataset.src !== url) {
        el.src = url;
        el.dataset.src = url;
      }
      el.volume = Math.min(1, clip.volume ?? 1);
      setTime(el, clip.in + offset);
    },
    [clips, assets, project.id],
  );

  const show = useCallback(
    (i, offset) => {
      const s = st.current;
      s.index = i;
      const el = vids[s.active].current;
      prepare(el, i, offset);
      if (s.playing && !isImage(i)) el?.play().catch(() => {});
      const other = vids[1 - s.active].current;
      if (other) {
        other.pause();
        if (clips[i + 1]) prepare(other, i + 1, 0); // preload the next clip
      }
    },
    [prepare, clips], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const pauseAudio = () => Object.values(audios.current).forEach((a) => a && a.pause());

  const seek = useCallback(
    (time) => {
      const s = st.current;
      s.t = Math.max(0, Math.min(total, time));
      const i = clipIndexAt(clips, s.t);
      pauseAudio();
      show(i, s.t - spans[i].start);
      sync();
    },
    [clips, spans, total, show],
  );

  const pause = useCallback(() => {
    const s = st.current;
    s.playing = false;
    vids.forEach((v) => v.current?.pause());
    pauseAudio();
    cancelAnimationFrame(raf.current);
    sync();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const tick = useCallback(
    (now) => {
      const s = st.current;
      if (!s.playing) return;
      const dt = Math.min(0.25, (now - s.last) / 1000);
      s.last = now;
      const clip = clips[s.index];
      const el = vids[s.active].current;
      if (isImage(s.index) || !el || el.readyState < 2) s.t += isImage(s.index) ? dt : 0;
      else s.t = spans[s.index].start + (el.currentTime - clip.in);

      const next = s.index + 1;
      const switchAt = next < clips.length ? spans[next].start : total;
      if (s.t >= switchAt - 0.02 || (el && el.ended && !isImage(s.index))) {
        if (next >= clips.length) {
          s.t = total;
          pause();
          return;
        }
        s.active = 1 - s.active;
        show(next, Math.max(0, s.t - spans[next].start));
        el?.pause();
      }

      // music and sound effects
      for (const tr of timeline.audio || []) {
        const a = audios.current[tr.id];
        if (!a) continue;
        const len = tr.loop ? Infinity : (tr.out ?? a.duration ?? Infinity) - (tr.in || 0);
        const inside = s.t >= tr.start && s.t < tr.start + len;
        if (inside && a.paused) {
          const pos = s.t - tr.start;
          a.currentTime = (tr.in || 0) + (tr.loop && a.duration ? pos % Math.max(0.1, a.duration - (tr.in || 0)) : pos);
          a.volume = Math.min(1, tr.volume ?? 0.8);
          a.play().catch(() => {});
        } else if (!inside && !a.paused) a.pause();
      }
      sync();
      raf.current = requestAnimationFrame(tick);
    },
    [clips, spans, total, show, pause, timeline.audio], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const play = useCallback(() => {
    const s = st.current;
    if (s.t >= total - 0.05) seek(0);
    s.playing = true;
    s.last = performance.now();
    if (!isImage(s.index)) vids[s.active].current?.play().catch(() => {});
    cancelAnimationFrame(raf.current);
    raf.current = requestAnimationFrame(tick);
    sync();
  }, [seek, tick, total]); // eslint-disable-line react-hooks/exhaustive-deps

  useImperativeHandle(ref, () => ({ seek: (t) => { seek(t); play(); }, currentTime: () => st.current.t, pause }), [
    seek,
    play,
    pause,
  ]);

  // Reload at the same moment whenever the timeline changes (hand edits show immediately).
  useEffect(() => {
    seek(Math.min(st.current.t, total));
    if (st.current.playing) {
      cancelAnimationFrame(raf.current);
      raf.current = requestAnimationFrame(tick);
    }
  }, [timeline]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    cancelAnimationFrame(raf.current);
    vids.forEach((v) => v.current?.pause());
    pauseAudio();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!frameRef.current) return undefined;
    const ro = new ResizeObserver(([e]) => setScale(e.contentRect.height / out.height));
    ro.observe(frameRef.current);
    return () => ro.disconnect();
  }, [out.height]);

  const t = view.t;
  const clip = clips[view.index];
  const emphasis = (timeline.zooms || []).reduce(
    (z, e) => z + (e.scale - 1) * Math.max(0, Math.min(1, (t - e.start) / 0.1, (e.end - t) / 0.18)),
    0,
  );
  const scaleNow = (clip?.zoom && clip.zoom > 1 ? clip.zoom : 1) * (1 + emphasis);
  const zoom = scaleNow > 1.001 ? `scale(${scaleNow})` : undefined;
  const base = Math.min(out.width, out.height) * scale;

  // caption on screen now, with an estimated word highlight
  const cap = timeline.captions;
  const keywordOn = (timeline.text || []).some((x) => x.style === 'keyword' && t >= x.start && t < x.end);
  const line = cap && !keywordOn && (lines || []).find((l) => t >= l.start && t < l.end);
  let caption = null;
  if (line) {
    const look = CAPTION_LOOK[cap.style] || CAPTION_LOOK.karaoke;
    const words = line.text.split(' ');
    const hi = cap.style === 'minimal' ? -1 : Math.min(words.length - 1, Math.floor(((t - line.start) / (line.end - line.start)) * words.length));
    const upper = cap.uppercase || cap.style === 'pop';
    const pos = { bottom: { bottom: '17%' }, top: { top: '10%' }, middle: { top: '50%', transform: 'translateY(-50%)' } }[cap.position || 'bottom'];
    caption = (
      <div className={`live-caption ${cap.style === 'boxed' ? 'is-boxed' : ''}`} style={{ ...pos, fontFamily: look.font, fontSize: base * 0.075 * look.rel * (cap.size || 1), color: cap.color || '#fff' }}>
        <span>
          {words.map((w, i) => (
            <span key={i} style={i === hi ? { color: cap.highlight_color || '#FFD23F' } : undefined}>
              {upper ? w.toUpperCase() : w}{i < words.length - 1 ? ' ' : ''}
            </span>
          ))}
        </span>
      </div>
    );
  }

  const textNow = (timeline.text || []).filter((x) => t >= x.start && t < x.end);
  const stickersNow = (timeline.stickers || []).filter((x) => t >= x.start && t < x.end);
  const brollNow = (timeline.broll || []).filter((b) => t >= b.start && t < b.start + b.duration && assets[b.asset_id]);
  const overlaysNow = (timeline.overlays || []).filter((o) => t >= o.start && t < o.end && assets[o.asset_id]?.kind === 'image');
  const imageClip = isImage(view.index) ? assets[clip.asset_id] : null;
  const unsupported = [];
  if (clips.some((c) => c.transition_in)) unsupported.push('transitions');
  if (timeline.grade?.lut_asset_id) unsupported.push('custom colour LUTs');
  if (timeline.grade?.lut_preset) unsupported.push('the exact look');
  if ((timeline.overlays || []).some((o) => assets[o.asset_id]?.kind === 'video')) unsupported.push('video overlays');
  if ((timeline.audio || []).some((a) => a.duck)) unsupported.push('music ducking');
  if (timeline.audio_polish) unsupported.push('audio polish');
  if (clips.some((c) => c.background)) unsupported.push('background changes');

  return (
    <div className="live-preview">
      <div className="frame live-frame" ref={frameRef} style={{ aspectRatio: `${out.width} / ${out.height}` }}>
        <div className="live-picture" style={{ filter: cssFilter(timeline.grade, looks) }}>
          {[0, 1].map((k) => (
            <video
              key={k}
              ref={vids[k]}
              playsInline
              preload="auto"
              className={k === view.active && !imageClip ? 'is-shown' : 'is-hidden'}
              style={{ objectFit: out.fit === 'cover' ? 'cover' : 'contain', transform: k === view.active ? zoom : undefined }}
              onEnded={() => st.current.playing && (st.current.t = spans[st.current.index].end)}
            />
          ))}
          {imageClip && (
            <img className="is-shown" alt="" src={assetFileUrl(project.id, imageClip)} style={{ objectFit: out.fit === 'cover' ? 'cover' : 'contain', transform: zoom }} />
          )}
          {brollNow.map((b) => (
            <BrollCutaway key={b.id} layer={b} asset={assets[b.asset_id]} url={assetFileUrl(project.id, assets[b.asset_id])}
              t={t} playing={view.playing} fit="cover" />
          ))}
          {overlaysNow.map((o) => {
            const [v, h] = GRID[o.position] || GRID['top-right'];
            return (
              <div key={o.id} className="live-layer" style={{ alignItems: v, justifyContent: h, padding: '4%' }}>
                <img alt="" src={assetFileUrl(project.id, assets[o.asset_id])} style={{ width: `${o.scale * 100}%`, opacity: o.opacity }} />
              </div>
            );
          })}
        </div>
        {stickersNow.map((st) => {
          const asset = st.sticker.startsWith('asset:') ? assets[st.sticker.slice(6)] : null;
          const url = asset ? assetFileUrl(project.id, asset) : stickerUrl(st.sticker, st.color);
          const loop = st.loop ?? (st.sticker.startsWith('emoji:') || st.sticker.startsWith('asset:'));
          return (
            <LottieSticker key={st.id} url={url} elapsed={t - st.start} loop={loop} className="live-sticker"
              style={{ left: `${(st.x ?? 0.5) * 100}%`, top: `${(st.y ?? 0.35) * 100}%`, width: `${(st.scale ?? 0.25) * 100}%`,
                transform: `translate(-50%, -50%) rotate(${st.rotation || 0}deg)` }} />
          );
        })}
        {textNow.map((x) => {
          const [v, h] = GRID[x.position] || GRID.center;
          const size = TEXT_SIZE[x.style] * (x.size || 1) * base;
          if (x.style === 'keyword') {
            const top = x.position?.startsWith('bottom') ? '80%' : x.position?.startsWith('top') ? '16%' : '64%';
            return (
              <div key={x.id} className="live-keyword" style={{ top, fontSize: size, color: x.color }}>
                {x.text.toUpperCase()}
              </div>
            );
          }
          return (
            <div key={x.id} className="live-layer" style={{ alignItems: v, justifyContent: h, padding: '6%' }}>
              <span className={`live-text live-${x.style}`} style={{ fontSize: size, color: x.color }}>{x.text}</span>
            </div>
          );
        })}
        {caption}
        {(timeline.audio || []).map((a) => (
          <audio key={a.id} ref={(el) => (audios.current[a.id] = el)}
            src={a.sfx ? `${API_BASE}/v2/sfx/${a.sfx}.wav` : assets[a.asset_id] ? assetFileUrl(project.id, assets[a.asset_id]) : undefined}
            preload="auto" loop={a.loop} />
        ))}
      </div>
      <div className="live-controls">
        <button className="button-secondary live-play" onClick={view.playing ? pause : play} aria-label={view.playing ? 'Pause' : 'Play'}>
          {view.playing ? 'Pause' : 'Play'}
        </button>
        <input
          type="range"
          min={0}
          max={total || 0}
          step={0.01}
          value={Math.min(t, total)}
          onChange={(e) => seek(Number(e.target.value))}
          aria-label="Position"
        />
        <span className="live-time">
          {timecode(t)} / {timecode(total)}
        </span>
      </div>
      <p className="fine-print live-note">
        Live preview, approximate.{unsupported.length ? ` Only in the rendered preview: ${unsupported.join(', ')}.` : ''}
      </p>
    </div>
  );
});

export default LivePreview;
