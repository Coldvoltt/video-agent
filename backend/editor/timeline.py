"""
Timeline JSON: the contract between the agent (or the user) and the renderer.

Times:
- `clips[].in/out` are seconds in the clip's *source* asset.
- Everything else (`text`, `overlays`, `audio`) is in *output* time: seconds into
  the finished video. Transitions overlap neighbouring clips, so output time is
  shorter than the sum of clip lengths; `clip_spans()` gives the mapping.

Unknown fields are rejected so that unsupported features fail loudly instead of
being silently ignored. See docs/PLAN.md section 5.
"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .typefaces import FONT_IDS

MIN_CLIP_SECONDS = 0.05
DURATION_TOLERANCE = 0.05  # container durations are approximate

HEX_COLOR = r"^#[0-9a-fA-F]{6}$"
LAYER_ID = r"^[A-Za-z0-9_-]{1,64}$"

# xfade transitions available since ffmpeg 4.3 (the oldest we support), plus a hard cut.
TRANSITIONS = (
    "fade", "fadeblack", "fadewhite", "dissolve", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "radial", "pixelize", "hblur", "squeezeh", "squeezev",
)
Position = Literal["top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right"]
GRADE_PRESETS = ("natural", "warm", "cool", "punchy", "film", "moody", "bw", "vintage")
LUT_PRESETS = ("teal_orange", "warm_film", "cool_cinema", "golden_hour", "bleach_bypass", "moody_green",
               "vintage_fade", "soft_pastel", "noir", "clean_bright", "neon_night", "rosy")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OutputSpec(_Strict):
    width: int = Field(1920, ge=64, le=4096)
    height: int = Field(1080, ge=64, le=4096)
    fps: float = Field(30, gt=0, le=120)
    fit: Literal["cover", "contain"] = Field(
        "contain", description="cover = crop to fill the frame; contain = letterbox"
    )
    background: str = Field("black", pattern=r"^(#[0-9a-fA-F]{6}|[a-z]{3,20})$")

    @field_validator("width", "height")
    @classmethod
    def _even(cls, v: int) -> int:
        if v % 2:
            raise ValueError("must be even (required by H.264 4:2:0)")
        return v


class Transition(_Strict):
    type: Literal[TRANSITIONS] = "fade"  # type: ignore[valid-type]
    duration: float = Field(0.5, ge=0.1, le=2.0)


class Background(_Strict):
    """Replace what's behind the subject in one clip."""
    replace_with: Literal["blur", "color", "media"] = Field(
        description="blur = the original background blurred; color = a solid colour; media = an image or video asset")
    method: Literal["ai", "chroma"] = Field(
        "ai", description="ai = cut the person out with a matting model; chroma = key out a green/blue screen")
    color: Optional[str] = Field(None, pattern=HEX_COLOR, description="For replace_with=color")
    asset_id: Optional[str] = Field(None, max_length=64, description="For replace_with=media: image or video (loops)")
    blur: float = Field(0.5, ge=0.05, le=1.0, description="For replace_with=blur: how strong")
    key_color: str = Field("#00FF00", pattern=HEX_COLOR, description="For method=chroma: the screen colour")
    key_tolerance: float = Field(0.12, ge=0.03, le=0.5, description="For method=chroma: how much of the screen colour to key")

    @model_validator(mode="after")
    def _needs(self) -> "Background":
        if self.replace_with == "color" and not self.color:
            raise ValueError("background: replace_with=color needs a color")
        if self.replace_with == "media" and not self.asset_id:
            raise ValueError("background: replace_with=media needs an asset_id")
        if self.replace_with != "media" and self.asset_id:
            raise ValueError("background: asset_id only applies to replace_with=media")
        return self


class Clip(_Strict):
    id: str = Field(pattern=LAYER_ID)
    asset_id: str = Field(min_length=1, max_length=64)
    in_: float = Field(alias="in", ge=0, description="Start, seconds into the source")
    out: float = Field(gt=0, description="End, seconds into the source")
    volume: float = Field(1.0, ge=0, le=4)
    zoom: Optional[float] = Field(None, ge=1.0, le=2.0, description="Punch-in: 1.15 = 15% closer, centred")
    transition_in: Optional[Transition] = Field(None, description="Transition from the previous clip into this one")
    background: Optional[Background] = Field(None, description="Replace the background behind the subject")
    angle: Optional[int] = Field(None, ge=0, le=2, description="Multicam clips: which camera (0, 1, 2) is shown")

    @model_validator(mode="after")
    def _order(self) -> "Clip":
        if self.out - self.in_ < MIN_CLIP_SECONDS:
            raise ValueError(f"clip {self.id}: out must be at least {MIN_CLIP_SECONDS}s after in")
        return self

    @property
    def duration(self) -> float:
        return self.out - self.in_


class Grade(_Strict):
    preset: Optional[Literal[GRADE_PRESETS]] = None  # type: ignore[valid-type]
    brightness: Optional[float] = Field(None, ge=-0.3, le=0.3)
    contrast: Optional[float] = Field(None, ge=0.5, le=1.6)
    saturation: Optional[float] = Field(None, ge=0, le=2)
    temperature: Optional[float] = Field(None, ge=-1, le=1, description="-1 cool ... +1 warm")
    lut_asset_id: Optional[str] = Field(None, max_length=64, description="A .cube LUT uploaded to the project")
    lut_preset: Optional[Literal[LUT_PRESETS]] = Field(None, description="A built-in look")  # type: ignore[valid-type]
    lut_strength: Optional[float] = Field(None, ge=0.0, le=1.0, description="How strongly the LUT applies (default 1)")

    @model_validator(mode="after")
    def _one_lut(self) -> "Grade":
        if self.lut_asset_id and self.lut_preset:
            raise ValueError("choose either an uploaded LUT or a built-in look, not both")
        return self


CaptionStyle = Literal["karaoke", "pop", "boxed", "minimal"]


class CaptionCorrection(_Strict):
    """
    Manual caption text for a stretch of a source asset (source time, so it survives re-cuts).
    Transcript words whose midpoint falls inside [start, end] are replaced by `text`;
    an empty `text` hides them.
    """
    asset_id: str = Field(min_length=1, max_length=64)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field("", max_length=300)

    @model_validator(mode="after")
    def _order(self) -> "CaptionCorrection":
        if self.end <= self.start:
            raise ValueError("caption correction: end must be after start")
        return self


CaptionFont = Literal[FONT_IDS]  # type: ignore[valid-type]


class CaptionEmphasis(_Strict):
    """Important words drawn in their own font, colour and size inside the captions."""
    words: list[str] = Field(default_factory=list, max_length=300,
                             description="Words or short phrases to emphasise wherever they are said")
    numbers: bool = Field(False, description="Also emphasise numbers, prices and percentages")
    font: CaptionFont = "luckiest_guy"
    color: str = Field("#FFD23F", pattern=HEX_COLOR)
    size: float = Field(1.25, ge=0.8, le=2.0, description="Relative to the caption size")
    uppercase: bool = True

    @field_validator("words")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out = []
        for w in v:
            w = " ".join(str(w).split())[:60]
            if w and w.lower() not in (x.lower() for x in out):
                out.append(w)
        return out


class Captions(_Strict):
    style: CaptionStyle = "karaoke"
    position: Literal["top", "middle", "bottom"] = "bottom"
    size: float = Field(1.0, ge=0.5, le=2.0, description="Relative to the style's default size")
    color: str = Field("#FFFFFF", pattern=HEX_COLOR)
    highlight_color: str = Field("#FFD23F", pattern=HEX_COLOR)
    uppercase: bool = False
    max_words: Optional[int] = Field(None, ge=1, le=10, description="Words on screen at once (style default if unset)")
    font: Optional[CaptionFont] = Field(None, description="Font for all caption words (style default if unset)")
    emphasis: Optional[CaptionEmphasis] = None
    corrections: list[CaptionCorrection] = Field(default_factory=list, max_length=2000)


class TextLayer(_Strict):
    id: str = Field(pattern=LAYER_ID)
    text: str = Field(min_length=1, max_length=200)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    style: Literal["title", "label", "lower_third", "keyword"] = "title"
    position: Position = "center"
    size: float = Field(1.0, ge=0.3, le=3.0)
    color: str = Field("#FFFFFF", pattern=HEX_COLOR)
    animation: Literal["none", "fade", "pop", "slide_up", "typewriter"] = "fade"
    font: Optional[CaptionFont] = Field(None, description="Font (the style's default if unset)")

    @model_validator(mode="after")
    def _order(self) -> "TextLayer":
        if self.end - self.start < 0.2:
            raise ValueError(f"text {self.id}: must last at least 0.2s")
        return self


class Overlay(_Strict):
    id: str = Field(pattern=LAYER_ID)
    asset_id: str = Field(min_length=1, max_length=64)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    position: Position = "top-right"
    scale: float = Field(0.2, ge=0.03, le=1.0, description="Width as a fraction of the frame width")
    opacity: float = Field(1.0, ge=0.05, le=1.0)
    fade: float = Field(0.3, ge=0, le=2.0)

    @model_validator(mode="after")
    def _order(self) -> "Overlay":
        if self.end - self.start < 0.1:
            raise ValueError(f"overlay {self.id}: must last at least 0.1s")
        return self


SFX_IDS = ("whoosh", "swipe", "riser", "pop", "ding", "click", "impact")


class AudioTrack(_Strict):
    id: str = Field(pattern=LAYER_ID)
    asset_id: Optional[str] = Field(None, min_length=1, max_length=64, description="The user's music or sound")
    sfx: Optional[Literal[SFX_IDS]] = Field(None, description="A built-in sound effect instead of an asset")  # type: ignore[valid-type]
    start: float = Field(0, ge=0, description="When it starts in the output")
    in_: float = Field(0, alias="in", ge=0, description="Start point in the source")
    out: Optional[float] = Field(None, gt=0, description="End point in the source (default: source end)")
    volume: float = Field(0.8, ge=0, le=2)
    fade_in: float = Field(0, ge=0, le=10)
    fade_out: float = Field(0, ge=0, le=10)
    loop: bool = Field(False, description="Repeat until the video ends (background music)")
    duck: bool = Field(False, description="Lower this track while people speak")

    @model_validator(mode="after")
    def _order(self) -> "AudioTrack":
        if self.out is not None and self.out - self.in_ < MIN_CLIP_SECONDS:
            raise ValueError(f"audio {self.id}: out must be after in")
        if bool(self.asset_id) == bool(self.sfx):
            raise ValueError(f"audio {self.id}: give either asset_id or sfx")
        return self


class ZoomLayer(_Strict):
    """Emphasis zoom: a quick punch-in on the main video for a moment (output time)."""
    id: str = Field(pattern=LAYER_ID)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    scale: float = Field(1.15, ge=1.03, le=1.6)
    focus_y: float = Field(0.4, ge=0, le=1, description="Vertical centre of the zoom (0 top, 1 bottom); faces ~0.4")

    @model_validator(mode="after")
    def _order(self) -> "ZoomLayer":
        if self.end - self.start < 0.2:
            raise ValueError(f"zoom {self.id}: must last at least 0.2s")
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start


class AudioPolish(_Strict):
    normalize: bool = Field(False, description="Loudness to -14 LUFS, the level social platforms play at")
    denoise: Literal["off", "light", "strong"] = "off"
    voice: bool = Field(False, description="Clearer voice: cut rumble and mud, add presence, gentle compression")


class BrollLayer(_Strict):
    """Cutaway footage over the main video (output time); the main audio keeps playing."""
    id: str = Field(pattern=LAYER_ID)
    asset_id: str = Field(min_length=1, max_length=64)
    start: float = Field(ge=0, description="When the cutaway starts in the output")
    duration: float = Field(gt=0.2, le=60)
    in_: float = Field(0, alias="in", ge=0, description="Start point in the B-roll source")
    motion: Literal["none", "push_in", "pull_out", "pan_left", "pan_right"] = "push_in"
    volume: float = Field(0.0, ge=0, le=2, description="B-roll's own sound (usually muted)")
    fade: float = Field(0.15, ge=0, le=1)

    @property
    def end(self) -> float:
        return self.start + self.duration


class StickerLayer(_Strict):
    """Animated sticker or callout (Lottie) at output time; x/y is its centre (0..1 of the frame)."""
    id: str = Field(pattern=LAYER_ID)
    sticker: str = Field(min_length=1, max_length=80, pattern=r"^(emoji:.+|asset:[A-Za-z0-9_-]+|[a-z_]+)$")
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.35, ge=0, le=1)
    scale: float = Field(0.25, ge=0.03, le=1.0, description="Width as a fraction of the frame width")
    rotation: float = Field(0, ge=-360, le=360)
    color: Optional[str] = Field(None, pattern=HEX_COLOR, description="Built-in callouts only")
    loop: Optional[bool] = Field(None, description="Default: emoji loop, callouts draw once and stay")

    @model_validator(mode="after")
    def _order(self) -> "StickerLayer":
        if self.end - self.start < 0.2:
            raise ValueError(f"sticker {self.id}: must last at least 0.2s")
        return self


EFFECTS = ("denoise", "grain", "blur")


class Region(_Strict):
    """A rectangle of the frame, as fractions (0..1) of the width and height."""
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _inside(self) -> "Region":
        if self.x + self.w > 1.001 or self.y + self.h > 1.001:
            raise ValueError("region must fit inside the frame (x + w <= 1, y + h <= 1)")
        return self


class EffectLayer(_Strict):
    """A picture effect over a stretch of output time (end None = to the end of the video).

    denoise: cleans up grainy, low-light footage (main video only, before the colour grade).
    grain:   film grain over the picture (under captions and text).
    blur:    blurs the whole picture, or only `region` (e.g. to hide a face, a screen or a number plate).
    """
    id: str = Field(pattern=LAYER_ID)
    type: Literal[EFFECTS]  # type: ignore[valid-type]
    start: float = Field(0.0, ge=0)
    end: Optional[float] = Field(None, gt=0)
    strength: float = Field(0.5, ge=0.05, le=1.0)
    region: Optional[Region] = Field(None, description="blur only")

    @model_validator(mode="after")
    def _check(self) -> "EffectLayer":
        if self.end is not None and self.end - self.start < 0.2:
            raise ValueError(f"effect {self.id}: must last at least 0.2s")
        if self.region and self.type != "blur":
            raise ValueError(f"effect {self.id}: region only applies to blur")
        return self


class Timeline(_Strict):
    version: Literal[1] = 1
    output: OutputSpec = Field(default_factory=OutputSpec)
    clips: list[Clip] = Field(min_length=1, max_length=500)
    grade: Optional[Grade] = None
    captions: Optional[Captions] = None
    text: list[TextLayer] = Field(default_factory=list, max_length=50)
    overlays: list[Overlay] = Field(default_factory=list, max_length=20)
    audio: list[AudioTrack] = Field(default_factory=list, max_length=20)
    broll: list[BrollLayer] = Field(default_factory=list, max_length=200)
    zooms: list[ZoomLayer] = Field(default_factory=list, max_length=300)
    stickers: list[StickerLayer] = Field(default_factory=list, max_length=100)
    effects: list[EffectLayer] = Field(default_factory=list, max_length=50)
    audio_polish: Optional[AudioPolish] = None

    @model_validator(mode="after")
    def _check(self) -> "Timeline":
        ids: set[str] = set()
        for layer in [*self.clips, *self.text, *self.overlays, *self.audio, *self.broll, *self.zooms,
                      *self.stickers, *self.effects]:
            if layer.id in ids:
                raise ValueError(f"duplicate id: {layer.id}")
            ids.add(layer.id)
        for i, c in enumerate(self.clips):
            t = c.transition_in
            if t is None:
                continue
            if i == 0:
                raise ValueError(f"clip {c.id}: the first clip cannot have a transition_in")
            shortest = min(c.duration, self.clips[i - 1].duration)
            if t.duration >= shortest:
                raise ValueError(
                    f"clip {c.id}: transition ({t.duration}s) must be shorter than both clips ({shortest:.2f}s)"
                )
        return self

    def clip_spans(self) -> list[tuple[float, float]]:
        """(start, end) of each clip in output time, accounting for transition overlaps."""
        spans, end = [], 0.0
        for c in self.clips:
            start = end - (c.transition_in.duration if c.transition_in else 0.0)
            end = start + c.duration
            spans.append((start, end))
        return spans

    @property
    def duration(self) -> float:
        return self.clip_spans()[-1][1]

    def to_json(self) -> dict:
        return self.model_dump(by_alias=True, mode="json", exclude_none=True)


def default_output(asset: Optional[dict]) -> dict:
    """Output settings matching a source's shape (portrait stays portrait), long side 1920."""
    w, h = (asset or {}).get("width"), (asset or {}).get("height")
    if not w or not h:
        return {}
    long_side = 1920
    if w >= h:
        width, height = long_side, max(64, int(round(long_side * h / w / 2)) * 2)
    else:
        width, height = max(64, int(round(long_side * w / h / 2)) * 2), long_side
    return {"width": width, "height": height, "fit": "contain"}


def check_assets(timeline: Timeline, assets: dict[str, dict], project_id: str) -> list[str]:
    """Problems that need the project's assets to detect. Empty list = renderable."""
    errors: list[str] = []

    def lookup(kind_label: str, layer_id: str, asset_id: str, allowed: tuple[str, ...]) -> Optional[dict]:
        a = assets.get(asset_id)
        if a is None or a["project_id"] != project_id:
            errors.append(f"{kind_label} {layer_id}: asset {asset_id} not found in this project")
            return None
        if a["status"] != "ready":
            errors.append(f"{kind_label} {layer_id}: asset {asset_id} is not ready (status: {a['status']})")
            return None
        if a["kind"] not in allowed:
            errors.append(f"{kind_label} {layer_id}: asset {asset_id} is {a['kind']}; needs {' or '.join(allowed)}")
            return None
        return a

    for c in timeline.clips:
        a = lookup("clip", c.id, c.asset_id, ("video", "image", "multicam"))
        if a and c.angle is not None:
            if a["kind"] != "multicam":
                errors.append(f"clip {c.id}: angle only applies to multicam clips")
            else:
                from . import store

                angles = (a.get("multicam") or store.get_analysis(a["id"]) or {}).get("angles") or []
                if c.angle >= len(angles):
                    errors.append(f"clip {c.id}: angle {c.angle} doesn't exist (this multicam has {len(angles)})")
        if a and a.get("role") == "reference":
            errors.append(f"clip {c.id}: asset {c.asset_id} is a style reference and can't be used as footage")
            continue
        if a and a["kind"] in ("video", "multicam") and a["duration"] is not None                 and c.out > a["duration"] + DURATION_TOLERANCE:
            errors.append(
                f"clip {c.id}: out={c.out:.3f}s is past the end of asset {c.asset_id} ({a['duration']:.3f}s)"
            )
        if c.background:
            if a and a["kind"] not in ("video", "multicam"):
                errors.append(f"clip {c.id}: background replacement needs a video clip")
            if c.background.asset_id:
                lookup("clip background", c.id, c.background.asset_id, ("video", "image"))
    for st in timeline.stickers:
        if st.sticker.startswith("asset:"):
            lookup("sticker", st.id, st.sticker[6:], ("lottie",))
        elif not st.sticker.startswith("emoji:"):
            from .stickers import CALLOUTS
            if st.sticker not in CALLOUTS:
                errors.append(f"sticker {st.id}: unknown sticker {st.sticker!r}")
    for b in timeline.broll:
        a = lookup("b-roll", b.id, b.asset_id, ("video", "image"))
        if a and a.get("role") == "reference":
            errors.append(f"b-roll {b.id}: asset {b.asset_id} is a style reference and can't be used as footage")
        elif a and a["kind"] == "video" and a.get("duration") and b.in_ + b.duration > a["duration"] + DURATION_TOLERANCE:
            errors.append(f"b-roll {b.id}: needs {b.in_ + b.duration:.1f}s of the source but it is "
                          f"{a['duration']:.1f}s long")
    for o in timeline.overlays:
        lookup("overlay", o.id, o.asset_id, ("image", "video"))
    for t in timeline.audio:
        if t.sfx:
            continue                     # built-in sound: nothing to look up
        a = lookup("audio", t.id, t.asset_id, ("audio", "video"))
        if a and not a.get("has_audio"):
            errors.append(f"audio {t.id}: asset {t.asset_id} has no sound")
        elif a and a.get("duration") and t.in_ >= a["duration"]:
            errors.append(f"audio {t.id}: in={t.in_}s is past the end of asset {t.asset_id}")
    if timeline.grade and timeline.grade.lut_asset_id:
        lookup("grade", "lut", timeline.grade.lut_asset_id, ("lut",))
    return errors


def layer_warnings(timeline: Timeline) -> list[str]:
    """Layers that start after the video ends (they are skipped when rendering)."""
    total = timeline.duration
    out = []
    for layer in [*timeline.text, *timeline.overlays, *timeline.audio, *timeline.broll, *timeline.zooms,
                  *timeline.stickers, *timeline.effects]:
        if layer.start >= total:
            out.append(f"{layer.id} starts at {layer.start:.1f}s, after the video ends ({total:.1f}s); it will be skipped")
    return out


def strip_markup(text: str) -> str:
    """Text for ASS: no override blocks or line-break escapes from user input."""
    return re.sub(r"[{}\\]", "", text).replace("\n", " ").strip()
