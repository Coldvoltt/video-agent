"""
Deterministic timeline edits used by the agent's tools.

The model decides *what* to do ("tighten the pacing", "keep these three moments");
these functions do the exact arithmetic, so cuts land on word boundaries and
hundreds of silence cuts never depend on the model copying numbers correctly.
"""

import bisect
import re
from typing import Iterable

Range = tuple[float, float]

FILLER_WORDS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "hm", "mm", "mhm"}
MIN_PIECE_SECONDS = 0.25


def merge_ranges(ranges: Iterable[Range], gap: float = 0.0) -> list[Range]:
    """Sort and merge overlapping (or within `gap`) ranges."""
    out: list[list[float]] = []
    for a, b in sorted((a, b) for a, b in ranges if b > a):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def subtract_ranges(keep: Range, remove: Iterable[Range]) -> list[Range]:
    """Parts of `keep` not covered by any range in `remove`."""
    start, end = keep
    pieces: list[Range] = []
    cursor = start
    for a, b in merge_ranges(remove):
        if b <= cursor or a >= end:
            continue
        if a > cursor:
            pieces.append((cursor, min(a, end)))
        cursor = max(cursor, b)
        if cursor >= end:
            break
    if cursor < end:
        pieces.append((cursor, end))
    return pieces


def _norm(word: str) -> str:
    return re.sub(r"[^a-z]", "", word.lower())


# Whisper's word timings get sloppy around disfluencies: a filler can be stretched
# over the next real word (seen live: "um" 1.7 s long, the following "trunks" 0.02 s).
# Cutting such a range would clip speech, so implausibly long fillers are left in.
FILLER_MAX_SECONDS = 0.8


def filler_ranges(words: list[dict]) -> list[Range]:
    return [(w["start"], w["end"]) for w in words
            if _norm(w["word"]) in FILLER_WORDS and w["end"] - w["start"] <= FILLER_MAX_SECONDS]


def spoken_text(transcript: dict | None, start: float, end: float, limit: int = 240) -> str:
    """Words spoken inside [start, end]: by word midpoints when word timings exist, else whole segments."""
    if not transcript:
        return ""
    words = transcript.get("words") or []
    if words:
        text = " ".join(w["word"].strip() for w in words if start <= (w["start"] + w["end"]) / 2 < end)
    else:
        segs = transcript.get("segments") or []
        text = " ".join(x["text"] for x in segs if x["end"] > start and x["start"] < end)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def speech_gaps(words: list[dict], min_gap: float, duration: float | None) -> list[Range]:
    """
    Pauses found from word timings: gaps between consecutive words, plus lead-in and
    tail. Works when background noise (room tone, music) hides pauses from a loudness check.
    """
    if not words:
        return []
    gaps: list[Range] = []
    if words[0]["start"] >= min_gap:
        gaps.append((0.0, words[0]["start"]))
    for prev, nxt in zip(words, words[1:]):
        if nxt["start"] - prev["end"] >= min_gap:
            gaps.append((prev["end"], nxt["start"]))
    if duration and duration - words[-1]["end"] >= min_gap:
        gaps.append((words[-1]["end"], duration))
    return gaps


def removable_silences(silences: list[Range], padding: float) -> list[Range]:
    """Shrink each silence by `padding` on both sides so speech keeps a natural breath."""
    out = []
    for a, b in silences:
        a2, b2 = a + padding, b - padding
        if b2 - a2 > 0.05:
            out.append((a2, b2))
    return out


def tighten_clips(clips: list[dict], silences_by_asset: dict[str, list[Range]],
                  fillers_by_asset: dict[str, list[Range]], padding: float = 0.15,
                  min_piece: float = MIN_PIECE_SECONDS) -> tuple[list[dict], float]:
    """
    Remove silences and filler words from every clip. Returns (new clips, seconds removed).
    Clip dicts use the timeline JSON shape ({"id", "asset_id", "in", "out", ...}).
    """
    new: list[dict] = []
    removed = 0.0
    for clip in clips:
        aid = clip["asset_id"]
        remove = removable_silences(silences_by_asset.get(aid, []), padding) + fillers_by_asset.get(aid, [])
        pieces = [p for p in subtract_ranges((clip["in"], clip["out"]), remove) if p[1] - p[0] >= min_piece]
        kept = sum(b - a for a, b in pieces)
        removed += (clip["out"] - clip["in"]) - kept
        if len(pieces) == 1 and pieces[0] == (clip["in"], clip["out"]):
            new.append(dict(clip))
            continue
        for i, (a, b) in enumerate(pieces, 1):
            piece = {**clip, "in": round(a, 3), "out": round(b, 3)}
            piece["id"] = f"{clip['id']}-{i}"[:64]
            if i > 1:
                piece.pop("transition_in", None)  # cuts inside a clip are jump cuts
            new.append(piece)
    return new, removed


def snap_range(start: float, end: float, words: list[dict], max_shift: float = 1.5) -> Range:
    """
    Move a cut so it doesn't slice through a word: the start moves back to the
    beginning of the word it lands in, the end moves forward to that word's end.
    Shifts larger than `max_shift` are ignored (the transcript is probably sparse there).
    """
    if not words:
        return start, end
    starts = [w["start"] for w in words]

    def containing(t: float) -> dict | None:
        i = bisect.bisect_right(starts, t) - 1
        if 0 <= i < len(words) and words[i]["start"] <= t < words[i]["end"]:
            return words[i]
        return None

    w = containing(start)
    if w and start - w["start"] <= max_shift:
        start = w["start"]
    w = containing(end)
    if w and w["end"] - end <= max_shift:
        end = w["end"]
    return round(start, 3), round(end, 3)


ASPECT_PRESETS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


def phrase_boundaries(words: list[dict], segments: list[dict], start: float, end: float,
                      min_pause: float = 0.08) -> list[float]:
    """Natural cut points inside [start, end]: segment ends and short pauses between words."""
    points = {round(s["end"], 3) for s in segments if start < s["end"] < end}
    for a, b in zip(words, words[1:]):
        if b["start"] - a["end"] >= min_pause and start < a["end"] < end:
            points.add(round((a["end"] + b["start"]) / 2, 3))   # cut in the middle of the pause
    return sorted(points)


def split_for_pacing(clips: list[dict], words_by_asset: dict[str, list[dict]],
                     segments_by_asset: dict[str, list[dict]], target: float,
                     punch_in: float | None = 1.12, min_shot: float = 0.8) -> tuple[list[dict], int]:
    """
    Break long clips into shots of roughly `target` seconds at phrase boundaries.
    Alternate shots get a punch-in zoom so the cut is visible on a single continuous take.
    Returns (new clips, number of cuts added). Clips without a transcript are left whole.
    """
    out: list[dict] = []
    added = 0
    for clip in clips:
        length = clip["out"] - clip["in"]
        bounds = phrase_boundaries(words_by_asset.get(clip["asset_id"], []),
                                   segments_by_asset.get(clip["asset_id"], []), clip["in"], clip["out"])
        if length < target * 1.5 or not bounds:
            out.append(dict(clip))
            continue
        cuts, cur = [], clip["in"]
        while clip["out"] - cur > target * 1.5:
            options = [b for b in bounds if cur + min_shot <= b <= clip["out"] - min_shot]
            if not options:
                break
            remaining = clip["out"] - cur
            # Near the end, split what's left evenly rather than leaving a short last shot.
            want = cur + (target if remaining >= 2 * target else remaining / 2)
            best = min(options, key=lambda b: abs(b - want))
            if best <= cur:
                break
            cuts.append(best)
            cur = best
        edges = [clip["in"], *cuts, clip["out"]]
        for k, (a, b) in enumerate(zip(edges, edges[1:])):
            piece = {**clip, "id": f"{clip['id']}-p{k + 1}"[:64] if cuts else clip["id"],
                     "in": round(a, 3), "out": round(b, 3)}
            if k > 0:
                piece.pop("transition_in", None)
            if punch_in and k % 2 == 1:
                piece["zoom"] = punch_in        # even pieces keep the clip's own framing
            out.append(piece)
        added += len(cuts)
    return out, added
