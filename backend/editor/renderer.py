"""
Timeline -> ffmpeg.

Graph, in order:
  1. each clip: its own seeked input, normalised to output size / fps / format,
     plus a stereo audio track (silence when the source has none)
  2. join clips: concat for hard cuts, xfade + acrossfade for transitions
  3. colour grade (eq / colorbalance / hue / LUT)
  4. overlays (images or video), timed, positioned, faded
  5. captions + text layers burned in from an ASS script (libass)
  6. audio: dialogue + music/SFX tracks, music ducked under speech, limiter

ffmpeg runs inside a scratch directory holding the filter script, the ASS file,
the LUT and the fonts, so the graph only references relative names; this avoids
escaping Windows drive letters inside filter arguments.
"""

import functools
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import captions as captions_mod
from . import colormatch
from . import luts as luts_mod
from . import multicam as multicam_mod
from . import sfx as sfx_mod
from . import stickers as stickers_mod
from .config import require_ffmpeg
from .timeline import Grade, Timeline

SAMPLE_RATE = 48000
PREVIEW_MAX_SIDE = 854


class RenderError(RuntimeError):
    pass


@functools.lru_cache(maxsize=4)
def _ffmpeg_major(ffmpeg: str) -> int:
    out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout
    m = re.search(r"ffmpeg version n?(\d+)", out)
    return int(m.group(1)) if m else 0


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def output_size(timeline: Timeline, preview: bool) -> tuple[int, int, float]:
    w, h, fps = timeline.output.width, timeline.output.height, timeline.output.fps
    if preview:
        scale = min(1.0, PREVIEW_MAX_SIDE / max(w, h))
        w, h, fps = _even(w * scale), _even(h * scale), min(fps, 30)
    return w, h, fps


def _f(x: float) -> str:
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


# ---------------------------------------------------------------- grade

GRADE_PRESETS: dict[str, dict[str, float]] = {
    "natural": {"contrast": 1.03, "saturation": 1.05},
    "warm": {"temperature": 0.5, "saturation": 1.08, "contrast": 1.03},
    "cool": {"temperature": -0.5, "saturation": 0.95, "contrast": 1.03},
    "punchy": {"contrast": 1.15, "saturation": 1.3, "brightness": 0.01},
    "film": {"contrast": 0.92, "saturation": 0.85, "temperature": 0.2, "brightness": 0.02},
    "moody": {"contrast": 1.12, "saturation": 0.75, "temperature": -0.3, "brightness": -0.04},
    "bw": {"saturation": 0.0, "contrast": 1.12},
    "vintage": {"contrast": 0.9, "saturation": 0.7, "temperature": 0.45, "brightness": 0.03},
}


def grade_values(grade: Grade) -> dict[str, float]:
    values = dict(GRADE_PRESETS.get(grade.preset or "", {}))
    for k in ("brightness", "contrast", "saturation", "temperature"):
        v = getattr(grade, k)
        if v is not None:
            values[k] = v
    return values


def grade_filters(grade: Grade, lut_name: Optional[str]) -> list[str]:
    v = grade_values(grade)
    filters = []
    if lut_name:
        filters.append(f"lut3d=file={lut_name}")
    b, c, s = v.get("brightness", 0.0), v.get("contrast", 1.0), v.get("saturation", 1.0)
    if (b, c, s) != (0.0, 1.0, 1.0):
        filters.append(f"eq=brightness={_f(b)}:contrast={_f(c)}:saturation={_f(s)}")
    t = v.get("temperature", 0.0)
    if t:
        k = 0.12 * t  # warm: more red, less blue; cool: the opposite
        filters.append(
            f"colorbalance=rs={_f(k * 0.6)}:bs={_f(-k * 0.6)}:rm={_f(k)}:bm={_f(-k)}:rh={_f(k * 0.4)}:bh={_f(-k * 0.4)}"
        )
    return filters


# ---------------------------------------------------------------- plan

@dataclass
class RenderPlan:
    args: list[str]
    filter_graph: str
    duration: float
    width: int
    height: int
    fps: float
    files: dict[str, Path | str | bytes] = field(default_factory=dict)  # name in work dir -> source path or text


class _Graph:
    def __init__(self) -> None:
        self.inputs: list[list[str]] = []
        self.lines: list[str] = []
        self._n = 0

    def input(self, *args: str) -> int:
        self.inputs.append(list(args))
        return len(self.inputs) - 1

    def label(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def add(self, line: str) -> None:
        self.lines.append(line)


_POS_XY = {
    "top-left": ("M", "M"), "top": ("(W-w)/2", "M"), "top-right": ("W-w-M", "M"),
    "left": ("M", "(H-h)/2"), "center": ("(W-w)/2", "(H-h)/2"), "right": ("W-w-M", "(H-h)/2"),
    "bottom-left": ("M", "H-h-M"), "bottom": ("(W-w)/2", "H-h-M"), "bottom-right": ("W-w-M", "H-h-M"),
}


def _hex(c: str) -> str:
    return "0x" + c.lstrip("#").upper()


def _background(g: "_Graph", clip, asset: dict, video_in: float, i: int, w: int, h: int, fps: float, fit: str,
                assets: dict[str, dict], asset_paths: dict[str, Path],
                mattes: dict[str, tuple[Path, Path, float]]) -> str:
    """Composite the subject of one clip over a new background; returns a label at w x h.

    The subject comes from the cached cut-out (`matte.ensure`): a foreground video with clean edge
    colours and an alpha video, both starting at `seg_start` in source time."""
    bg = clip.background
    dur = _f(clip.duration)
    sw, sh = _even(asset.get("width") or w), _even(asset.get("height") or h)
    cover = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    mask_fit = re.sub(r":color=[^,]*$", ":color=black", fit)  # letterbox bars count as background
    if clip.id not in mattes:
        raise RenderError(f"clip {clip.id}: background matte is missing")
    fg_path, alpha_path, seg_start = mattes[clip.id]
    offset = _f(max(0.0, video_in - seg_start))
    fi = g.input("-ss", offset, "-t", dur, "-i", str(fg_path))
    ai = g.input("-ss", offset, "-t", dur, "-i", str(alpha_path))
    hold = f"tpad=stop_mode=clone:stop_duration={dur}"   # a cut-out a frame short never shows a gap
    fg, mk = g.label("fgf"), g.label("mkf")
    g.add(f"[{fi}:v:0]setpts=PTS-STARTPTS,scale={sw}:{sh}:flags=bicubic,fps={_f(fps)},{hold},setsar=1,"
          f"{fit},setsar=1,format=yuv444p[{fg}]")
    g.add(f"[{ai}:v:0]setpts=PTS-STARTPTS,format=gray,scale={sw}:{sh}:flags=bicubic,fps={_f(fps)},{hold},"
          f"setsar=1,{mask_fit},setsar=1[{mk}]")
    fga = g.label("fga")
    g.add(f"[{fg}][{mk}]alphamerge[{fga}]")

    back = g.label("bg")
    if bg.replace_with == "blur":
        sigma = max(4.0, bg.blur * w * 0.035)
        g.add(f"[{i}:v:0]setpts=PTS-STARTPTS,fps={_f(fps)},{cover},gblur=sigma={_f(sigma)},setsar=1,"
              f"format=yuv420p[{back}]")
    elif bg.replace_with == "color":
        g.add(f"color=c={_hex(bg.color)}:s={w}x{h}:r={_f(fps)}:d={dur},format=yuv420p[{back}]")
    else:
        media = assets[bg.asset_id]
        msrc = str(asset_paths[bg.asset_id])
        if media["kind"] == "image":
            bi = g.input("-loop", "1", "-framerate", _f(fps), "-t", dur, "-i", msrc)
        else:
            bi = g.input("-stream_loop", "-1", "-t", dur, "-i", msrc)
        g.add(f"[{bi}:v:0]setpts=PTS-STARTPTS,{cover},fps={_f(fps)},setsar=1,format=yuv420p[{back}]")
    comp = g.label("comp")
    g.add(f"[{back}][{fga}]overlay=format=auto:shortest=1,format=yuv420p[{comp}]")
    return comp


def build_plan(timeline: Timeline, assets: dict[str, dict], asset_paths: dict[str, Path],
               out_path: Path, preview: bool, transcripts: Optional[dict[str, dict | None]] = None,
               mattes: Optional[dict[str, tuple[Path, Path, float]]] = None) -> RenderPlan:
    ffmpeg = require_ffmpeg()
    w, h, fps = output_size(timeline, preview)
    total = timeline.duration
    g = _Graph()
    files: dict[str, Path | str | bytes] = {}

    bg = timeline.output.background
    if timeline.output.fit == "cover":
        fit = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    else:
        fit = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={bg}"
    afmt = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"

    broll_audio: list[str] = []

    # 1. clips
    segs: list[tuple[str, str, float]] = []
    for clip in timeline.clips:
        asset = assets[clip.asset_id]
        src = str(asset_paths[clip.asset_id])
        dur = _f(clip.duration)
        picture, video_in = asset, clip.in_
        if asset["kind"] == "image":
            i = g.input("-loop", "1", "-framerate", _f(fps), "-t", dur, "-i", src)
            ia = i
        elif asset["kind"] == "multicam":
            # picture from the chosen angle (its own time = group time + offset), sound from the group audio
            angle_id, offset = multicam_mod.angle_source(asset, clip.angle)
            picture, video_in = assets[angle_id], clip.in_ + offset
            i = g.input("-ss", _f(video_in), "-t", dur, "-i", str(asset_paths[angle_id]))
            ia = g.input("-ss", _f(clip.in_), "-t", dur, "-i", src)
        else:
            i = g.input("-ss", _f(clip.in_), "-t", dur, "-i", src)
            ia = i
        v, a = g.label("v"), g.label("a")
        punch = ""
        if clip.zoom and clip.zoom > 1.0:
            # Centre crop then scale back up: a closer framing of the same shot (punch-in).
            punch = f",crop=iw/{_f(clip.zoom)}:ih/{_f(clip.zoom)},scale={w}:{h}"
        if clip.background and asset["kind"] in ("video", "multicam"):
            comp = _background(g, clip, picture, video_in, i, w, h, fps, fit, assets, asset_paths, mattes or {})
            g.add(f"[{comp}]null{punch},fps={_f(fps)},format=yuv420p,setsar=1,"
                  f"trim=duration={dur},setpts=PTS-STARTPTS,settb=AVTB[{v}]")
        else:
            g.add(f"[{i}:v:0]setpts=PTS-STARTPTS,{fit}{punch},fps={_f(fps)},format=yuv420p,setsar=1,"
                  f"trim=duration={dur},setpts=PTS-STARTPTS,settb=AVTB[{v}]")
        if asset["kind"] in ("video", "multicam") and asset.get("has_audio"):
            g.add(f"[{ia}:a:0]asetpts=PTS-STARTPTS,{afmt},volume={_f(clip.volume)},"
                  f"apad,atrim=duration={dur},asetpts=PTS-STARTPTS[{a}]")
        else:
            g.add(f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE},atrim=duration={dur},"
                  f"aformat=sample_fmts=fltp[{a}]")
        segs.append((v, a, clip.duration))

    # 2. join
    cur_v, cur_a, cur_len = segs[0]
    for clip, (v, a, length) in zip(timeline.clips[1:], segs[1:]):
        nv, na = g.label("jv"), g.label("ja")
        t = clip.transition_in
        if t:
            d = _f(t.duration)
            g.add(f"[{cur_v}][{v}]xfade=transition={t.type}:duration={d}:offset={_f(cur_len - t.duration)}[{nv}]")
            g.add(f"[{cur_a}][{a}]acrossfade=d={d}:c1=tri:c2=tri[{na}]")
            cur_len += length - t.duration
        else:
            g.add(f"[{cur_v}][{cur_a}][{v}][{a}]concat=n=2:v=1:a=1[{nv}][{na}]")
            cur_len += length
        cur_v, cur_a = nv, na

    def window(e) -> tuple[float, float] | None:
        end = total if e.end is None else min(e.end, total)
        return (e.start, end) if e.start < end else None

    def enable(e) -> str:
        a, b = window(e)
        return f":enable='between(t,{_f(a)},{_f(b)})'"

    effects = [e for e in timeline.effects if window(e)]

    # 2b. video noise removal on the main footage (before the grade, which would sharpen the noise)
    for e in (e for e in effects if e.type == "denoise"):
        k = e.strength
        nv = g.label("dn")
        if k > 0.6 and not preview:
            # Heavy noise: non-local means is far better (+10 dB vs +3 dB on very noisy footage) but ~5x slower,
            # so exports use it and previews keep the fast filter.
            g.add(f"[{cur_v}]nlmeans=s={_f(2 + 4 * k)}:p=5:r=11{enable(e)}[{nv}]")
        else:
            k = min(k, 0.6) / 0.6     # hqdn3d tops out at the settings that measured best on ordinary noise
            g.add(f"[{cur_v}]hqdn3d={_f(2 + 6 * k)}:{_f(1.5 + 4.5 * k)}:{_f(3 + 9 * k)}:{_f(2.5 + 7 * k)}"
                  f"{enable(e)}[{nv}]")
        cur_v = nv

    # 3. grade
    if timeline.grade:
        lut_name = None
        grade = timeline.grade
        strength = 1.0 if grade.lut_strength is None else grade.lut_strength
        if (grade.lut_asset_id or grade.lut_preset) and strength > 0:
            lut_name = "grade.cube"
            source = asset_paths[grade.lut_asset_id] if grade.lut_asset_id else luts_mod.preset_path(grade.lut_preset)
            if strength >= 0.999:
                files[lut_name] = source
            else:   # partial strength: mix the look with "no change"
                mixed = luts_mod.blend_with_identity(luts_mod.read_cube(source), strength)
                files[lut_name] = colormatch.cube_text(mixed, "Look")
        chain = grade_filters(timeline.grade, lut_name)
        if chain:
            nv = g.label("gv")
            g.add(f"[{cur_v}]{','.join(chain)}[{nv}]")
            cur_v = nv

    # 3b. emphasis zooms on the main video (B-roll, graphics and captions are not zoomed)
    zooms = [z for z in timeline.zooms if z.start < total]
    if zooms:
        ramp_in, ramp_out = 0.10, 0.18
        terms = "+".join(f"{_f(z.scale - 1)}*clip(min((it-{_f(z.start)})/{ramp_in},({_f(z.end)}-it)/{ramp_out}),0,1)"
                         for z in zooms)
        focus = "0.4"
        for z in reversed(zooms):
            focus = f"if(between(it,{_f(z.start - 0.2)},{_f(z.end + 0.3)}),{_f(z.focus_y)},{focus})"
        nv = g.label("zv")
        g.add(f"[{cur_v}]zoompan=z='1+{terms}':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*({focus})'"
              f":d=1:s={w}x{h}:fps={_f(fps)},setsar=1[{nv}]")
        cur_v = nv

    # 4a. B-roll cutaways (full frame, over the main video, under graphics and captions)
    for br in sorted(timeline.broll, key=lambda x: x.start):
        if br.start >= total:
            continue
        dur = min(br.duration, total - br.start)
        asset = assets[br.asset_id]
        src = str(asset_paths[br.asset_id])
        if asset["kind"] == "image":
            i = g.input("-loop", "1", "-framerate", _f(fps), "-t", _f(dur), "-i", src)
        else:
            i = g.input("-ss", _f(br.in_), "-t", _f(dur), "-i", src)
        frames = max(1, int(round(dur * fps)))
        # Fill the frame, then add gentle camera motion with zoompan (one output frame per input frame).
        zoom = {"none": "1", "push_in": f"1+0.10*on/{frames}", "pull_out": f"1.10-0.10*on/{frames}",
                "pan_left": "1.12", "pan_right": "1.12"}[br.motion]
        x = {"pan_left": f"(iw-iw/zoom)*(1-on/{frames})", "pan_right": f"(iw-iw/zoom)*on/{frames}"}.get(
            br.motion, "iw/2-(iw/zoom/2)")
        chain = [f"[{i}:v:0]setpts=PTS-STARTPTS,fps={_f(fps)}",
                 f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2}",
                 f"zoompan=z='{zoom}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={_f(fps)}",
                 "format=yuva420p", f"trim=duration={_f(dur)}"]
        fade = min(br.fade, dur / 3)
        if fade > 0:
            chain.append(f"fade=t=in:st=0:d={_f(fade)}:alpha=1")
            chain.append(f"fade=t=out:st={_f(dur - fade)}:d={_f(fade)}:alpha=1")
        chain.append(f"setpts=PTS-STARTPTS+{_f(br.start)}/TB")
        bl, nv = g.label("bl"), g.label("bv")
        g.add(",".join(chain) + f"[{bl}]")
        g.add(f"[{cur_v}][{bl}]overlay=x=0:y=0:eof_action=pass:"
              f"enable='between(t,{_f(br.start)},{_f(br.start + dur)})'[{nv}]")
        cur_v = nv
        if br.volume > 0 and asset["kind"] == "video" and asset.get("has_audio"):
            al, ms = g.label("ba"), int(round(br.start * 1000))
            g.add(f"[{i}:a:0]asetpts=PTS-STARTPTS,{afmt},volume={_f(br.volume)},atrim=duration={_f(dur)},"
                  f"adelay={ms}|{ms}[{al}]")
            broll_audio.append(al)

    # 4. overlays
    margin = _f(round(w * 0.04))
    for ov in timeline.overlays:
        if ov.start >= total:
            continue
        end = min(ov.end, total)
        asset = assets[ov.asset_id]
        src = str(asset_paths[ov.asset_id])
        if asset["kind"] == "image":
            i = g.input("-loop", "1", "-framerate", _f(fps), "-t", _f(end), "-i", src)
            shift = ""
        else:
            i = g.input("-t", _f(end - ov.start), "-i", src)
            shift = f",setpts=PTS-STARTPTS+{_f(ov.start)}/TB"
        ow = _even(w * ov.scale)
        chain = [f"[{i}:v:0]fps={_f(fps)}{shift},scale={ow}:-2,format=rgba"]
        if ov.opacity < 1:
            chain.append(f"colorchannelmixer=aa={_f(ov.opacity)}")
        fade = min(ov.fade, (end - ov.start) / 2)
        if fade > 0:
            chain.append(f"fade=t=in:st={_f(ov.start)}:d={_f(fade)}:alpha=1")
            chain.append(f"fade=t=out:st={_f(end - fade)}:d={_f(fade)}:alpha=1")
        ol, nv = g.label("ol"), g.label("ov")
        g.add(",".join(chain) + f"[{ol}]")
        x, y = (e.replace("M", margin) for e in _POS_XY[ov.position])
        g.add(f"[{cur_v}][{ol}]overlay=x={x}:y={y}:eof_action=pass:"
              f"enable='between(t,{_f(ov.start)},{_f(end)})'[{nv}]")
        cur_v = nv

    # 4b. animated stickers and callouts (Lottie frames rendered here, overlaid by ffmpeg)
    for si, st in enumerate(sorted(timeline.stickers, key=lambda x: x.start)):
        if st.start >= total:
            continue
        dur = min(st.end, total) - st.start
        try:
            data = stickers_mod.resolve(st.sticker, st.color, asset_paths)
            loop = stickers_mod.default_loop(st.sticker) if st.loop is None else st.loop
            pngs = stickers_mod.render_frames(data, dur, fps, _even(w * st.scale), loop, st.rotation)
        except stickers_mod.StickerError as exc:
            raise RenderError(f"Sticker {st.id}: {exc}") from exc
        folder = f"stk{si}"
        for k, png in enumerate(pngs):
            files[f"{folder}/{k:05d}.png"] = png
        i = g.input("-framerate", _f(fps), "-i", f"{folder}/%05d.png")
        sl, nv = g.label("sl"), g.label("sv")
        g.add(f"[{i}:v:0]format=rgba,setpts=PTS-STARTPTS+{_f(st.start)}/TB[{sl}]")
        g.add(f"[{cur_v}][{sl}]overlay=x={_f(st.x * w)}-w/2:y={_f(st.y * h)}-h/2:eof_action=pass:"
              f"enable='between(t,{_f(st.start)},{_f(st.start + dur)})'[{nv}]")
        cur_v = nv

    # 4c. blur and film grain over the picture (B-roll and graphics included), under captions and text
    for e in (e for e in effects if e.type == "blur"):
        # blur level -> radius, eased so Light stays gentle and Maximum makes a face unrecognisable
        # (sigma at a 1920-px long side: light 0.2 ~9, medium 0.5 ~26, strong 0.8 ~46, maximum ~60)
        sigma = (2 + 58 * e.strength ** 1.3) * (w / 1920 if w >= h else h / 1920)
        nv = g.label("bl")
        if e.region is None:
            g.add(f"[{cur_v}]gblur=sigma={_f(sigma)}:steps=2{enable(e)}[{nv}]")
        else:
            r = e.region
            rx, ry = int(round(r.x * w / 2)) * 2, int(round(r.y * h / 2)) * 2
            rw, rh = max(2, min(_even(r.w * w), w - rx)), max(2, min(_even(r.h * h), h - ry))
            base, cut, soft = g.label("blb"), g.label("blc"), g.label("bls")
            g.add(f"[{cur_v}]split[{base}][{cut}]")
            g.add(f"[{cut}]crop={rw}:{rh}:{rx}:{ry},gblur=sigma={_f(max(sigma, 12))}:steps=3[{soft}]")
            g.add(f"[{base}][{soft}]overlay=x={rx}:y={ry}{enable(e)}[{nv}]")
        cur_v = nv
    for e in (e for e in effects if e.type == "grain"):
        nv = g.label("gr")
        g.add(f"[{cur_v}]noise=c0s={_f(6 + 22 * e.strength)}:c0f=t+u{enable(e)}[{nv}]")
        cur_v = nv

    # 5. captions + text
    ass = captions_mod.build_ass(timeline, transcripts or {})
    if ass:
        files["captions.ass"] = ass
        for font in captions_mod.FONTS_DIR.glob("*.ttf"):
            files[f"fonts/{font.name}"] = font
        nv = g.label("tv")
        g.add(f"[{cur_v}]ass=captions.ass:fontsdir=fonts[{nv}]")
        cur_v = nv

    # 6. audio
    polish = timeline.audio_polish
    if polish and (polish.denoise != "off" or polish.voice):
        chain = []
        if polish.denoise != "off":
            chain.append(f"afftdn=nr={12 if polish.denoise == 'light' else 22}:nf=-35:tn=1")
        if polish.voice:
            chain += ["highpass=f=80", "equalizer=f=250:t=q:w=1.2:g=-2.5", "equalizer=f=3200:t=q:w=1.0:g=3",
                      "acompressor=threshold=-21dB:ratio=3:attack=8:release=160:makeup=2"]
        na = g.label("vox")
        g.add(f"[{cur_a}]{','.join(chain)}[{na}]")
        cur_a = na

    music, plain = [], []
    for tr in timeline.audio:
        if tr.start >= total:
            continue
        if tr.sfx:
            src = str(sfx_mod.path(tr.sfx))
            asset = {"duration": sfx_mod.SOUNDS[tr.sfx].duration}
        else:
            asset = assets[tr.asset_id]
            src = str(asset_paths[tr.asset_id])
        remaining = total - tr.start
        src_end = tr.out if tr.out is not None else asset.get("duration")
        span = (src_end - tr.in_) if src_end else remaining
        length = remaining if tr.loop else min(span, remaining)
        if length <= 0.05:
            continue
        if tr.loop:
            i = g.input("-stream_loop", "-1", "-ss", _f(tr.in_), "-t", _f(length), "-i", src)
        else:
            i = g.input("-ss", _f(tr.in_), "-t", _f(length), "-i", src)
        chain = [f"[{i}:a:0]asetpts=PTS-STARTPTS", afmt, f"volume={_f(tr.volume)}"]
        if tr.fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={_f(min(tr.fade_in, length))}")
        if tr.fade_out > 0:
            fo = min(tr.fade_out, length)
            chain.append(f"afade=t=out:st={_f(length - fo)}:d={_f(fo)}")
        if tr.start > 0:
            ms = int(round(tr.start * 1000))
            chain.append(f"adelay={ms}|{ms}")
        al = g.label("al")
        g.add(",".join(chain) + f"[{al}]")
        (music if tr.duck else plain).append(al)

    if music or plain or broll_audio:
        mix_inputs = []
        if music:
            dialogue, side = g.label("dlg"), g.label("sc")
            g.add(f"[{cur_a}]asplit=2[{dialogue}][{side}]")
            bed = music[0]
            if len(music) > 1:
                bed = g.label("bed")
                g.add("".join(f"[{m}]" for m in music) + f"amix=inputs={len(music)}:duration=longest:normalize=0[{bed}]")
            ducked = g.label("duck")
            g.add(f"[{bed}][{side}]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350:makeup=1[{ducked}]")
            mix_inputs = [dialogue, ducked]
        else:
            mix_inputs = [cur_a]
        mix_inputs += plain + broll_audio
        na = g.label("mix")
        # Summing can clip; a limiter catches peaks (its `level` option needs ffmpeg 5+).
        limiter = ",alimiter=limit=0.95:level=0" if _ffmpeg_major(ffmpeg) >= 5 else ""
        g.add("".join(f"[{m}]" for m in mix_inputs)
              + f"amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0:normalize=0{limiter}[{na}]")
        cur_a = na

    if polish and polish.normalize:
        na = g.label("loud")
        g.add(f"[{cur_a}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample={SAMPLE_RATE}[{na}]")
        cur_a = na

    g.add(f"[{cur_v}]null[vout]")
    g.add(f"[{cur_a}]anull[aout]")
    filter_graph = ";\n".join(g.lines)

    args = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-v", "error"]
    for inp in g.inputs:
        args += inp
    script_flag = "-/filter_complex" if _ffmpeg_major(ffmpeg) >= 7 else "-filter_complex_script"
    args += [script_flag, "graph.txt", "-map", "[vout]", "-map", "[aout]"]
    if preview:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-b:a", "96k"]
    else:
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k"]
    args += ["-t", _f(total), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-progress", "pipe:1", "-nostats", str(Path(out_path).resolve())]

    files["graph.txt"] = filter_graph
    return RenderPlan(args=args, filter_graph=filter_graph, duration=total, width=w, height=h, fps=fps, files=files)


def render(timeline: Timeline, assets: dict[str, dict], asset_paths: dict[str, Path], out_path: Path,
           preview: bool = False, on_progress: Optional[Callable[[float], None]] = None,
           transcripts: Optional[dict[str, dict | None]] = None,
           mattes: Optional[dict[str, tuple[Path, Path, float]]] = None) -> dict:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    asset_paths = {k: Path(v).resolve() for k, v in asset_paths.items()}
    with tempfile.TemporaryDirectory(prefix="render_") as tmp:
        work = Path(tmp)
        plan = build_plan(timeline, assets, asset_paths, out_path, preview, transcripts, mattes)
        for name, content in plan.files.items():
            dest = work / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, Path):
                shutil.copyfile(content, dest)
            elif isinstance(content, bytes):
                dest.write_bytes(content)
            else:
                dest.write_text(content, encoding="utf-8")
        err_path = work / "stderr.txt"

        with open(err_path, "w", encoding="utf-8", errors="replace") as err:
            proc = subprocess.Popen(plan.args, stdout=subprocess.PIPE, stderr=err, text=True,
                                    encoding="utf-8", errors="replace", cwd=work)
            assert proc.stdout is not None
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                # out_time_us is microseconds (out_time_ms is too, despite the name).
                if key == "out_time_us" and on_progress and plan.duration > 0:
                    try:
                        on_progress(min(0.99, int(value) / 1e6 / plan.duration))
                    except ValueError:
                        pass
            code = proc.wait()

        if code != 0:
            tail = err_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RenderError(f"ffmpeg exited with {code}: {tail.strip()}")

    if on_progress:
        on_progress(1.0)
    return {
        "duration": round(plan.duration, 3),
        "width": plan.width,
        "height": plan.height,
        "fps": plan.fps,
        "size_bytes": out_path.stat().st_size,
        "preview": preview,
    }
