"""
Captions and text layers as an ASS subtitle script, burned in by ffmpeg's `ass` filter (libass).

Coordinates use the timeline's output size (PlayResX/Y); libass scales them to the
actual render size, so previews and finals look the same.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import typefaces
from .edits import FILLER_WORDS, _norm
from .timeline import CaptionEmphasis, Captions, TextLayer, Timeline, strip_markup

FONTS_DIR = Path(__file__).parent / "fonts"
BODY_FONT = "Montserrat ExtraBold"
DISPLAY_FONT = "Anton"

SENTENCE_END = (".", "?", "!", "…")
MAX_GAP = 0.7          # a longer pause starts a new caption
HOLD_AFTER = 0.35      # keep the last caption up briefly after the last word

STYLE_DEFAULTS = {
    #           font          size  max_words outline shadow box
    "karaoke": (BODY_FONT,    1.40, 3,        0.09,   0.03,  False),
    "pop":     (DISPLAY_FONT, 1.90, 2,        0.08,   0.04,  False),
    "boxed":   (BODY_FONT,    1.15, 4,        0.00,   0.00,  True),
    "minimal": (BODY_FONT,    0.85, 6,        0.06,   0.02,  False),
}


_DEFAULT_ID = {BODY_FONT: "montserrat", DISPLAY_FONT: "anton"}


def font_for(default: str, override: str | None) -> tuple[str, float]:
    """(libass font name, size factor) for a style's default font or a chosen one, so a different
    font comes out about the same size (fonts with tall line heights would otherwise draw small)."""
    if not override:
        return default, 1.0
    base = _DEFAULT_ID.get(default, "montserrat")
    return typefaces.ass_name(override), typefaces.em(base) / typefaces.em(override)


_NUMBER = re.compile(r"\d|[$£€¥%]")


def emphasised(words: list["TimedWord"], emp: CaptionEmphasis | None) -> set[int]:
    """Indices of the words to emphasise: the listed words/phrases wherever said, and numbers if asked."""
    if not emp:
        return set()
    norm = [_norm(w.text) for w in words]
    out: set[int] = set()
    for phrase in emp.words:
        target = [t for t in (_norm(x) for x in phrase.split()) if t]
        n = len(target)
        if not n:
            continue
        for i in range(len(norm) - n + 1):
            if norm[i:i + n] == target:
                out.update(range(i, i + n))
    if emp.numbers:
        out.update(i for i, w in enumerate(words) if _NUMBER.search(w.text))
    return out


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (alpha 0 = opaque)."""
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


@dataclass
class TimedWord:
    text: str
    start: float                 # output time
    end: float
    ends_phrase: bool = False    # last word of a transcript segment: a natural place to break
    asset_id: str = ""
    src_start: float = 0.0       # source time, for mapping edits back to the transcript
    src_end: float = 0.0
    clip_index: int = 0
    group_key: str | None = None  # words from one manual correction stay on one caption line


def _apply_corrections(words: list[dict], corrections: list, clip_in: float, clip_out: float) -> list[dict]:
    """
    Replace transcript words covered by manual corrections. Returns word dicts with extra keys:
    `group` (correction index or None) and `corrected` (True for replacement words).
    """
    out = [dict(w, group=None, corrected=False) for w in words]
    for idx, c in enumerate(corrections):
        if c.end <= clip_in or c.start >= clip_out:
            continue
        covered = [w for w in out if not w["corrected"] and c.start <= (w["start"] + w["end"]) / 2 < c.end]
        span_start = covered[0]["start"] if covered else max(c.start, clip_in)
        span_end = covered[-1]["end"] if covered else min(c.end, clip_out)
        out = [w for w in out if not any(w is x for x in covered)]
        tokens = strip_markup(c.text).split()
        if not tokens or span_end <= span_start:
            continue
        # Spread the new words over the original span, weighted by length.
        weights = [len(t) + 1 for t in tokens]
        total, t = sum(weights), span_start
        for tok, wgt in zip(tokens, weights):
            dur = (span_end - span_start) * wgt / total
            out.append({"word": tok, "start": t, "end": t + dur, "group": idx, "corrected": True})
            t += dur
    out.sort(key=lambda w: w["start"])
    return out


def output_words(timeline: Timeline, transcripts: dict[str, dict | None]) -> list[TimedWord]:
    """Caption words that survive the edit (after manual corrections), mapped to output time."""
    corrections = timeline.captions.corrections if timeline.captions else []
    words: list[TimedWord] = []
    for ci, (clip, (span_start, span_end)) in enumerate(zip(timeline.clips, timeline.clip_spans())):
        t = transcripts.get(clip.asset_id) or {}
        seg_ends = sorted(s["end"] for s in t.get("segments") or [])
        in_clip = [w for w in t.get("words") or [] if clip.in_ <= (w["start"] + w["end"]) / 2 < clip.out]
        mine = [c for c in corrections if c.asset_id == clip.asset_id]
        src_words = _apply_corrections(in_clip, mine, clip.in_, clip.out)
        for k, w in enumerate(src_words):
            if not w["corrected"] and _norm(w["word"]) in FILLER_WORDS:
                continue
            nxt = src_words[k + 1] if k + 1 < len(src_words) else None
            ends_phrase = nxt is None or any(w["end"] - 0.05 <= e <= nxt["start"] + 0.05 for e in seg_ends)
            start = span_start + max(0.0, w["start"] - clip.in_)
            end = span_start + min(clip.duration, w["end"] - clip.in_)
            text = strip_markup(w["word"])
            if text:
                words.append(TimedWord(
                    text, start, min(max(end, start + 0.05), span_end), ends_phrase,
                    asset_id=clip.asset_id, src_start=w["start"], src_end=w["end"], clip_index=ci,
                    group_key=None if w["group"] is None else f"c{w['group']}",
                ))
    words.sort(key=lambda w: w.start)
    # Overlapping clips (transitions) can produce overlapping words; keep times monotonic.
    for prev, nxt in zip(words, words[1:]):
        if nxt.start < prev.end:
            prev.end = max(prev.start + 0.05, nxt.start)
    return words


def group_words(words: list[TimedWord], max_words: int) -> list[list[TimedWord]]:
    groups: list[list[TimedWord]] = []
    for w in words:
        cur = groups[-1] if groups else None
        if cur is not None and w.group_key is not None and cur[-1].group_key == w.group_key:
            cur.append(w)        # a manually edited line stays one caption, whatever its length
            continue
        if (
            cur is None
            or w.group_key is not None
            or cur[-1].group_key is not None
            or w.clip_index != cur[-1].clip_index
            or len(cur) >= max_words
            or w.start - cur[-1].end > MAX_GAP
            or cur[-1].text.endswith(SENTENCE_END)
            or cur[-1].ends_phrase
        ):
            groups.append([w])
        else:
            cur.append(w)
    return groups


def _timed_groups(cap: Captions, words: list[TimedWord], total: float) -> list[tuple[list[TimedWord], float]]:
    """Caption lines with their on-screen end time."""
    groups = group_words(words, cap.max_words or STYLE_DEFAULTS[cap.style][2])
    out = []
    for gi, group in enumerate(groups):
        g_end = group[-1].end + HOLD_AFTER
        if gi + 1 < len(groups):
            g_end = min(g_end, groups[gi + 1][0].start)
        out.append((group, min(g_end, total)))
    return out


def caption_lines(timeline: Timeline, transcripts: dict[str, dict | None]) -> list[dict]:
    """The caption lines exactly as they will be rendered, with their source ranges (for editing)."""
    if not timeline.captions:
        return []
    words = output_words(timeline, transcripts)
    marked = emphasised(words, timeline.captions.emphasis)
    index = {id(w): i for i, w in enumerate(words)}
    lines = []
    for group, end in _timed_groups(timeline.captions, words, timeline.duration):
        lines.append({
            "start": round(group[0].start, 3),
            "end": round(end, 3),
            "text": " ".join(w.text for w in group),
            "asset_id": group[0].asset_id,
            "src_start": round(min(w.src_start for w in group), 3),
            "src_end": round(max(w.src_end for w in group), 3),
            "edited": group[0].group_key is not None,
            "emphasis": [j for j, w in enumerate(group) if index[id(w)] in marked],   # word positions in the line
        })
    return lines


class _Script:
    def __init__(self, width: int, height: int):
        self.w, self.h = width, height
        self.styles: list[str] = []
        self.events: list[tuple[float, float, str, str, int]] = []   # start, end, style, text, layer

    def style(self, name: str, font: str, size: float, primary: str, outline_color: str, back: str,
              border_style: int, outline: float, shadow: float, alignment: int, margin_v: int,
              margin_lr: int = 0) -> None:
        self.styles.append(
            f"Style: {name},{font},{size:.0f},{primary},{primary},{outline_color},{back},0,0,0,0,100,100,0,0,"
            f"{border_style},{outline:.1f},{shadow:.1f},{alignment},{margin_lr},{margin_lr},{margin_v},1"
        )

    def event(self, start: float, end: float, style: str, text: str, layer: int = 0) -> None:
        if end - start < 0.01:
            return
        self.events.append((start, end, style, text, layer))

    def clear_captions_during(self, windows: list[tuple[float, float]]) -> None:
        """Hide caption events while keyword pops are on screen (the word would show twice)."""
        if not windows:
            return
        from .edits import subtract_ranges

        kept = []
        for start, end, style, text, layer in self.events:
            if style != "Caption":
                kept.append((start, end, style, text, layer))
                continue
            for a, b in subtract_ranges((start, end), windows):
                if b - a >= 0.05:
                    kept.append((a, b, style, text, layer))
        self.events = kept

    def render(self) -> str:
        head = [
            "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {self.w}", f"PlayResY: {self.h}",
            "WrapStyle: 0", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            *self.styles, "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        lines = [f"Dialogue: {layer},{ass_time(a)},{ass_time(b)},{style},,0,0,0,,{text}"
                 for a, b, style, text, layer in sorted(self.events, key=lambda e: e[0])]
        return "\n".join(head + lines) + "\n"


def _caption_events(s: _Script, cap: Captions, words: list[TimedWord], total: float) -> None:
    default_font, rel, default_max, outline_k, shadow_k, boxed = STYLE_DEFAULTS[cap.style]
    font, factor = font_for(default_font, cap.font)
    base = min(s.w, s.h)
    size = base * 0.075 * rel * cap.size * factor
    align = {"bottom": 2, "middle": 5, "top": 8}[cap.position]
    margin_v = int(s.h * (0.17 if cap.position == "bottom" else 0.10)) if cap.position != "middle" else 0
    side = int(s.w * 0.08)
    if boxed:
        s.style("Caption", font, size, ass_color(cap.color), ass_color("#000000", 0x40),
                ass_color("#000000", 0x40), 3, size * 0.18, 0, align, margin_v, side)
    else:
        s.style("Caption", font, size, ass_color(cap.color), ass_color("#000000"),
                ass_color("#000000", 0x60), 1, size * outline_k, size * shadow_k, align, margin_v, side)

    hi = ass_color(cap.highlight_color)
    fmt = (lambda t: t.upper()) if cap.uppercase or cap.style == "pop" else (lambda t: t)
    emp = cap.emphasis
    marked = emphasised(words, emp)
    index = {id(w): i for i, w in enumerate(words)}
    if emp:
        # emphasised words: own font, colour and size (scaled so the chosen size means the same in any font)
        e_font = typefaces.ass_name(emp.font)
        e_size = size / factor * typefaces.em(_DEFAULT_ID.get(default_font, "montserrat")) \
            / typefaces.em(emp.font) * emp.size
        e_col = ass_color(emp.color)

    def word_text(w: TimedWord, current: bool) -> str:
        text = fmt(w.text)
        if index.get(id(w)) in marked:
            text = text.upper() if emp.uppercase else text
            grow = r"\fscx112\fscy112" if current and cap.style == "karaoke" else ""
            return f"{{\\fn{e_font}\\fs{e_size:.0f}\\c{e_col}{grow}}}{text}{{\\r}}"
        if current:
            grow = r"\fscx112\fscy112" if cap.style == "karaoke" else ""
            return f"{{\\c{hi}{grow}}}{text}{{\\r}}"
        return text

    for group, g_end in _timed_groups(cap, words, total):
        if cap.style == "minimal" or len(group) > 12:
            # Long hand-edited lines read better without a word-by-word highlight.
            s.event(group[0].start, g_end, "Caption", " ".join(word_text(w, False) for w in group))
            continue
        for wi, w in enumerate(group):
            start = group[0].start if wi == 0 else w.start
            end = group[wi + 1].start if wi + 1 < len(group) else g_end
            text = " ".join(word_text(other, j == wi) for j, other in enumerate(group))
            if cap.style == "pop" and wi == 0:
                text = r"{\fscx70\fscy70\t(0,90,\fscx108\fscy108)\t(90,150,\fscx100\fscy100)}" + text
            s.event(start, end, "Caption", text)


_ALIGN = {"bottom-left": 1, "bottom": 2, "bottom-right": 3, "left": 4, "center": 5, "right": 6,
          "top-left": 7, "top": 8, "top-right": 9}


def _anchor(s: _Script, align: int, margin: float) -> tuple[int, int]:
    col, row = (align - 1) % 3, (align - 1) // 3          # col 0..2 left->right, row 0 bottom .. 2 top
    x = [margin, s.w / 2, s.w - margin][col]
    y = [s.h - margin, s.h / 2, margin][row]
    return int(x), int(y)


def _text_events(s: _Script, layers: list[TextLayer], total: float) -> None:
    base = min(s.w, s.h)
    margin = s.w * 0.06
    for i, t in enumerate(layers):
        text_override = ""
        if t.start >= total:
            continue
        end = min(t.end, total)
        name = f"Text{i}"
        align = _ALIGN[t.position]
        if t.style == "title":
            font, k = font_for(DISPLAY_FONT, t.font)
            size = base * 0.16 * t.size * k
            s.style(name, font, size, ass_color(t.color), ass_color("#000000"), ass_color("#000000", 0x70),
                    1, size * 0.05, size * 0.04, align, int(margin))
        elif t.style == "label":
            font, k = font_for(BODY_FONT, t.font)
            size = base * 0.07 * t.size * k
            s.style(name, font, size, ass_color(t.color), ass_color("#000000"), ass_color("#000000", 0x70),
                    1, size * 0.07, size * 0.03, align, int(margin))
        elif t.style == "keyword":   # a big word slammed on screen for emphasis
            font, k = font_for(DISPLAY_FONT, t.font)
            size = base * 0.3 * t.size * k
            s.style(name, font, size, ass_color(t.color), ass_color("#000000"), ass_color("#000000", 0x50),
                    1, size * 0.07, size * 0.05, align, int(margin))
            text_override = strip_markup(t.text).upper()
        else:  # lower_third: text on a solid band
            font, k = font_for(BODY_FONT, t.font)
            size = base * 0.06 * t.size * k
            s.style(name, font, size, ass_color(t.color), ass_color("#000000", 0x30), ass_color("#000000", 0x30),
                    3, size * 0.35, 0, align, int(margin))
        text = text_override if t.style == "keyword" else strip_markup(t.text)
        x, y = _anchor(s, align, margin)
        if t.style == "keyword":
            # Talking-head faces sit around the middle: keep big words clear of the eyes.
            row = (align - 1) // 3          # 0 bottom row, 1 middle, 2 top
            y = int(s.h * (0.80 if row == 0 else 0.64 if row == 1 else 0.16))
        pos = f"\\an{align}\\pos({x},{y})"
        if t.animation == "typewriter":
            reveal = min(1.2, (end - t.start) * 0.4)
            n = len(text)
            steps = max(1, min(n, 40))
            for k in range(1, steps + 1):
                shown = text[: max(1, round(n * k / steps))]
                a = t.start + reveal * (k - 1) / steps
                b = t.start + reveal * k / steps if k < steps else end
                s.event(a, b, name, f"{{{pos}}}{shown}", layer=1)
            continue
        tags = {
            "none": pos,
            "fade": pos + r"\fad(250,250)",
            "pop": pos + (r"\fad(0,150)\fscx30\fscy30\t(0,110,\fscx118\fscy118)\t(110,200,\fscx100\fscy100)"
                          if t.style == "keyword" else
                          r"\fad(0,200)\fscx50\fscy50\t(0,160,\fscx108\fscy108)\t(160,240,\fscx100\fscy100)"),
            "slide_up": f"\\an{align}\\move({x},{int(y + s.h * 0.04)},{x},{y},0,300)\\fad(200,200)",
        }[t.animation]
        s.event(t.start, end, name, f"{{{tags}}}{text}", layer=1)


def build_ass(timeline: Timeline, transcripts: dict[str, dict | None]) -> str | None:
    """ASS script for the timeline's captions and text layers, or None if there is nothing to draw."""
    total = timeline.duration
    s = _Script(timeline.output.width, timeline.output.height)
    if timeline.captions:
        words = output_words(timeline, transcripts)
        if words:
            _caption_events(s, timeline.captions, words, total)
    _text_events(s, timeline.text, total)
    s.clear_captions_during([(t.start, t.end) for t in timeline.text if t.style == "keyword"])
    return s.render() if s.events else None


def merge_caption_edits(corrections: list[dict], edits: list[dict]) -> list[dict]:
    """New edits replace earlier corrections they overlap on the same asset."""
    out = list(corrections)
    for e in edits:
        out = [c for c in out
               if not (c["asset_id"] == e["asset_id"] and c["start"] < e["end"] and e["start"] < c["end"])]
        out.append(e)
    return sorted(out, key=lambda c: (c["asset_id"], c["start"]))
