"""
The editing agent.

One user request = one agent run: the model inspects the project through tools,
edits an in-memory draft of the timeline, and finishes with a short reply. If
the draft changed and is valid, it is saved as a new timeline version.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from . import analysis, colormatch, edits, media, storage, store
from . import captions as captions_mod
from . import luts as luts_mod
from .llm import LLMProvider, ToolCall, ToolSpec
from .timeline import GRADE_PRESETS, TRANSITIONS, Timeline, check_assets, default_output, layer_warnings

log = logging.getLogger("editor.agent")

MAX_STEPS = 24
MAX_TOOL_RESULT_CHARS = 24_000
TRANSCRIPT_PAGE_CHARS = 20_000
HISTORY_MESSAGES = 20

SYSTEM_PROMPT = """You are the editing agent inside a video editor for content creators.
The user describes an edit; you make it by calling tools that inspect their footage and change the project's timeline.

How the timeline works:
- Clips play back to back. Each clip is a range [in, out] in seconds of one source asset ("source time").
- Captions, text, overlays, music and sound effects are placed in "output time": seconds into the finished video.
  get_timeline reports each clip's output_start/output_end; use those to place things at a moment or at each cut.
- Transitions overlap neighbouring clips, so they shorten the video slightly.
- Output format: 9:16 for Shorts/Reels/TikTok, 16:9 for YouTube, 1:1 or 4:5 for feeds. "cover" crops to fill the frame.
  A new timeline matches the footage's own shape (portrait stays portrait). Only pass aspect, or call
  set_output_format, when the user asks for a format or platform; adding captions or styling never changes it.

How to work:
0. Do what the user asked and nothing more. Never add music, sound effects, overlays, text, transitions or a colour
   grade they did not ask for, even when suitable files are in the project; offer it as your one follow-up instead.
   Keep everything already on the timeline unless they ask to change it.
1. Look before you edit: list_assets, then get_timeline, then read transcripts or search them for what the user means.
2. Choose ranges that start and end on complete sentences or thoughts. A moment should make sense on its own.
3. For "remove silences / ums / dead air / tighten the pacing", use tighten_pacing instead of hand-made cuts.
4. For "remove the part where ...", find it in the transcript, then use cut_ranges.
5. For highlights, short-form clips or "best N moments", pick ranges yourself and call set_timeline. Respect any length the user asks for (the duration is reported back to you).
6. Caption wording: when the user wants a caption fixed, reworded or hidden, call get_captions, then edit_captions.
   Manual caption edits are kept when captions are restyled or clips change.
7. Styling: set_captions (word-by-word captions from the transcript; "highlight each word" = karaoke, "one or two big words at a time" = pop), set_transitions, set_grade (colour), add_text (titles, labels, lower thirds), add_overlay (the user's images or videos, e.g. a logo), add_audio (the user's music or sound effects). remove_layer deletes text, overlays or audio.
8. Music and sound effects must come from the user's uploaded assets. Background music: add_audio with loop=true, duck=true, a low volume (0.2-0.35) and a short fade_out.
9. Changing the clips keeps captions, grade, text, overlays and audio, but text/overlay/audio times may need moving afterwards. Clip transitions are lost when clips are replaced with set_timeline; re-apply them if the user had asked for them.
10. If a tool returns errors, fix the input and try again. Never invent asset ids or timestamps; take them from tool results.

Emphasis (to "spice it up", "make it engaging", "add energy"):
- emphasize puts a big keyword, a punch-in zoom and a pop sound on a phrase. Choose the 1-2 words that carry the point
  (numbers, results, strong claims), roughly one every 5-10 seconds; never on filler or every sentence.
- auto_sound_effects adds whooshes into cutaways, swipes on transitions and pops on keywords; add_sound_effect for
  one-offs (riser before a reveal, ding on a key point, impact on a bold claim). Keep sound effects below the voice.
- set_audio_polish(normalize=true) whenever the video is for social platforms; denoise for noisy rooms; voice for
  thin or muddy voices. It changes the sound only, never the length.

B-roll (cutaways shown over the speaker while their voice continues):
- Use it when the user asks for B-roll, cutaways or "make it more visual". Always call list_broll first and pick
  the shot whose description shows what is being said; set source_in to that shot's start and keep the cutaway
  inside the shot (add_broll trims it and reports what it shows; check that it matches). Only place a shot that
  shows what is being said; if none fits, use stock or a generated image, or leave that moment without B-roll.
  Place B-roll before adding emphasis. Prefer the user's own B-roll; otherwise search_stock_broll with concrete visual queries, then
  add_stock_broll; if stock is unavailable or has nothing fitting, generate_broll_image (a still, moved with
  push_in or a pan). Only generate images when the user asked for B-roll or visuals. Place each cutaway with find_phrase on the words it illustrates, starting just before them.
- Good B-roll: 1.5-4 s per cutaway, at most about a third of the runtime, never in the first 2 seconds (the hook,
  the face sells it) and not over punchlines or reactions. Vary shots; don't reuse one clip back to back.
- Say which clips came from stock and which images are AI-generated; stock credit is shown automatically.

Reference videos (role "reference" in list_assets) show a style to copy; they are never footage.
When the user asks to edit "like", "in the style of" or "inspired by" a reference:
- Call get_reference_style, then apply_reference_style. It applies format, pacing, captions, transitions and colour
  exactly as analysed; pass parts to limit it when the user only wants some of these ("just the colour").
- Use the individual tools (set_output_format, apply_pacing, set_captions, set_transitions, match_reference_color) only
  to adjust afterwards. Music only from the user's own uploaded files.
- Tell the user what was applied and what was skipped, using the tool's applied/skipped report.

Not available yet: speed changes, AI-generated music, images or voice, stock footage, face-tracking reframes, per-clip colour, motion graphics beyond the text animations offered.
If the user asks for these, do the parts you can and say plainly what is not supported yet.

Finish with a short reply to the user: 2-4 plain sentences saying what you changed and the new length.
Only describe changes that tool results confirm; if a step failed or was skipped, say so.
Write one short paragraph: no headings, bullet lists, bold or markdown. Times are minutes:seconds into the video,
so three seconds is 0:03 (never 3:00). Write times as m:ss (for example 0:45). Do not mention tool names or internal ids.
Offer at most one follow-up suggestion, and only one you can do with your tools."""


@dataclass
class AgentResult:
    reply: str
    timeline_version: int | None
    changed: bool
    trace: list[dict] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _mmss(t: float) -> str:
    t = max(0, t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


class EditSession:
    """Project state plus the draft timeline the tools act on."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.assets = {a["id"]: a for a in store.list_assets(project_id)}
        row = store.get_timeline(project_id)
        self.base_version: int | None = row["version"] if row else None
        self.draft: dict | None = row["data"] if row else None
        self.pending_output: dict | None = None
        self.changed = False
        self._transcripts: dict[str, dict | None] = {}
        self._silences: dict[tuple, list] = {}

    # ------------------------------------------------------------ helpers

    def transcript(self, asset_id: str) -> dict | None:
        if asset_id not in self._transcripts:
            a = store.get_asset(asset_id, with_transcript=True)
            self._transcripts[asset_id] = a.get("transcript") if a else None
        return self._transcripts[asset_id]

    def words(self, asset_id: str) -> list[dict]:
        t = self.transcript(asset_id)
        return (t or {}).get("words") or []

    def silences(self, asset_id: str, min_silence: float, noise_db: float) -> list[tuple[float, float]]:
        key = (asset_id, round(min_silence, 2), round(noise_db, 1))
        if key not in self._silences:
            a = self.assets[asset_id]
            if not a.get("has_audio"):
                self._silences[key] = []
            else:
                self._silences[key] = analysis.detect_silences(
                    storage.path(a["storage_key"]), a.get("duration"), min_silence, noise_db
                )
        return self._silences[key]

    def text_between(self, asset_id: str, start: float, end: float, limit: int = 160) -> str:
        return edits.spoken_text(self.transcript(asset_id), start, end, limit)

    def _asset_or_error(self, asset_id: str) -> dict:
        a = self.assets.get(asset_id)
        if not a:
            raise ToolInputError(f"Unknown asset_id {asset_id!r}. Call list_assets.")
        return a

    def _validate(self, data: dict) -> tuple[Timeline | None, list[str]]:
        try:
            tl = Timeline.model_validate(data)
        except ValidationError as exc:
            return None, [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        problems = check_assets(tl, self.assets, self.project_id)
        return (tl if not problems else None), problems

    def _commit(self, data: dict) -> dict:
        tl, problems = self._validate(data)
        if problems:
            return {"ok": False, "errors": problems[:20]}
        self.draft = tl.to_json()
        self.changed = True
        return {"ok": True, **self.timeline_summary()}

    def timeline_summary(self) -> dict:
        if not self.draft:
            return {"timeline": None, "note": "No timeline yet."}
        tl = Timeline.model_validate(self.draft)
        clips = self.draft["clips"]
        spans = tl.clip_spans()
        summary = {
            "output": self.draft["output"],
            "duration_seconds": round(tl.duration, 2),
            "clip_count": len(clips),
            "clips": [
                {**c, "output_start": round(a, 2), "output_end": round(b, 2),
                 "text": self.text_between(c["asset_id"], c["in"], c["out"])}
                for c, (a, b) in list(zip(clips, spans))[:80]
            ],
            **({"clips_truncated": len(clips) - 80} if len(clips) > 80 else {}),
        }
        for key in ("grade", "captions", "text", "overlays", "audio", "broll", "zooms", "audio_polish"):
            if self.draft.get(key):
                summary[key] = self.draft[key]
        warnings = layer_warnings(tl)
        if warnings:
            summary["warnings"] = warnings
        return summary

    def new_id(self, prefix: str, reserved: set[str] | None = None) -> str:
        """A layer id not used on the draft (nor in `reserved`, for ids handed out before a commit)."""
        taken = {x["id"] for key in ("clips", "text", "overlays", "audio", "broll", "zooms")
                 for x in (self.draft or {}).get(key, [])} | (reserved or set())
        n = 1
        while f"{prefix}{n}" in taken:
            n += 1
        if reserved is not None:
            reserved.add(f"{prefix}{n}")
        return f"{prefix}{n}"

    def require_draft(self) -> dict:
        if not self.draft:
            raise ToolInputError("There is no timeline yet; create one with set_timeline or tighten_pacing first.")
        return self.draft

    def _output(self, first_asset_id: str | None = None) -> dict:
        """Current output settings; for a new timeline, match the first clip's source shape."""
        if self.draft:
            return self.draft["output"]
        base = default_output(self.assets.get(first_asset_id)) if first_asset_id else {}
        return {**base, **(self.pending_output or {})}


class ToolInputError(ValueError):
    pass


# ---------------------------------------------------------------- tools

def tool_list_assets(s: EditSession) -> dict:
    return {"assets": [
        {
            "asset_id": a["id"], "name": a.get("filename"), "kind": a.get("kind"), "status": a["status"],
            "duration_seconds": a.get("duration"), "width": a.get("width"), "height": a.get("height"),
            "has_audio": bool(a.get("has_audio")), "transcript": a.get("transcript_status"),
            "role": a.get("role", "footage"),
            **({"style_analysis": a.get("analysis_status")} if a.get("role") == "reference" else {}),
        }
        for a in s.assets.values()
    ]}


def tool_get_timeline(s: EditSession) -> dict:
    return s.timeline_summary()


def tool_get_transcript(s: EditSession, asset_id: str, start: float = 0, end: float | None = None,
                        include_words: bool = False) -> dict:
    s._asset_or_error(asset_id)
    t = s.transcript(asset_id)
    if not t:
        return {"error": "This asset has no transcript (not transcribed, or no speech)."}
    end = end if end is not None else float("inf")
    lines, size, next_start = [], 0, None
    for seg in t["segments"]:
        if seg["end"] <= start or seg["start"] >= end:
            continue
        line = f"[{seg['start']:.2f}-{seg['end']:.2f}] {seg['text']}"
        if size + len(line) > TRANSCRIPT_PAGE_CHARS:
            next_start = seg["start"]
            break
        lines.append(line)
        size += len(line) + 1
    out: dict[str, Any] = {"asset_id": asset_id, "language": t.get("language"), "segments": "\n".join(lines)}
    if next_start is not None:
        out["truncated"] = f"More transcript follows; call again with start={next_start:.2f}"
    if include_words:
        span_end = next_start if next_start is not None else end
        words = [w for w in t.get("words") or [] if w["end"] > start and w["start"] < span_end]
        out["words"] = " ".join(f"{w['word']}@{w['start']:.2f}" for w in words[:1500])
    return out


def tool_search_transcripts(s: EditSession, query: str, n: int = 5) -> dict:
    indexed = [a for a in s.assets.values() if a.get("collection_name") and a.get("role", "footage") == "footage"]
    if not indexed:
        return {"error": "No transcribed assets to search."}
    import rag_engine

    results = []
    for a in indexed:
        for r in rag_engine.search(query, a["collection_name"], n_results=max(1, min(n, 10))):
            results.append({"asset_id": a["id"], "start": round(r["start"], 2), "end": round(r["end"], 2),
                            "relevance": round(r["relevance"], 3), "text": r["text"]})
    results.sort(key=lambda r: r["relevance"], reverse=True)
    return {"results": results[:n]}


def tool_find_silences(s: EditSession, asset_id: str, min_silence: float = 0.5, noise_db: float = -35) -> dict:
    s._asset_or_error(asset_id)
    sil = s.silences(asset_id, min_silence, noise_db)
    return {"asset_id": asset_id, "count": len(sil), "total_seconds": round(sum(b - a for a, b in sil), 2),
            "silences": [[a, b] for a, b in sil[:300]]}


def tool_set_timeline(s: EditSession, clips: list[dict], aspect: str | None = None, fit: str | None = None,
                      snap_to_words: bool = True) -> dict:
    if not clips:
        raise ToolInputError("clips must not be empty")
    new_clips = []
    for i, c in enumerate(clips, 1):
        aid = c.get("asset_id")
        s._asset_or_error(aid)
        start, end = float(c["in"]), float(c["out"])
        if snap_to_words:
            start, end = edits.snap_range(start, end, s.words(aid))
        dur = s.assets[aid].get("duration")
        if dur:
            end = min(end, dur)
        clip = {"id": c.get("id") or f"c{i}", "asset_id": aid, "in": round(max(0.0, start), 3), "out": round(end, 3)}
        if "volume" in c:
            clip["volume"] = c["volume"]
        if c.get("zoom"):
            clip["zoom"] = c["zoom"]
        new_clips.append(clip)
    output = dict(s._output(new_clips[0]["asset_id"]))
    if aspect:
        output.update(_aspect_output(aspect, fit or "cover"))
    elif fit:
        output["fit"] = fit
    return s._commit({**(s.draft or {}), "version": 1, "output": output, "clips": new_clips})


def _aspect_output(aspect: str, fit: str) -> dict:
    if aspect not in edits.ASPECT_PRESETS:
        raise ToolInputError(f"aspect must be one of {sorted(edits.ASPECT_PRESETS)}")
    w, h = edits.ASPECT_PRESETS[aspect]
    return {"width": w, "height": h, "fit": fit}


def tool_set_output_format(s: EditSession, aspect: str, fit: str = "cover") -> dict:
    out = _aspect_output(aspect, fit)
    if not s.draft:
        s.pending_output = {**(s.pending_output or {}), **out}
        return {"ok": True, "note": "Saved; applies when clips are set.", "output": s.pending_output}
    return s._commit({**s.draft, "output": {**s.draft["output"], **out}})


def _base_clips(s: EditSession) -> list[dict]:
    if s.draft:
        return [dict(c) for c in s.draft["clips"]]
    videos = [a for a in s.assets.values()
              if a.get("kind") == "video" and a["status"] == "ready" and a.get("role", "footage") == "footage"]
    if not videos:
        raise ToolInputError("No timeline and no ready video assets to start from.")
    return [{"id": f"c{i}", "asset_id": a["id"], "in": 0.0, "out": a["duration"]} for i, a in enumerate(videos, 1)]


def tool_tighten_pacing(s: EditSession, remove_silences: bool = True, remove_fillers: bool = True,
                        min_silence: float = 0.6, padding: float = 0.15, noise_db: float = -35) -> dict:
    clips = _base_clips(s)
    before = sum(c["out"] - c["in"] for c in clips)
    asset_ids = {c["asset_id"] for c in clips}
    sil: dict = {}
    if remove_silences:
        for aid in asset_ids:
            # Loudness-based detection misses pauses under room tone or music; word gaps catch those.
            gaps = edits.speech_gaps(s.words(aid), min_silence, s.assets[aid].get("duration"))
            sil[aid] = edits.merge_ranges(s.silences(aid, min_silence, noise_db) + gaps)
    fill: dict = {}
    notes = []
    if remove_fillers:
        for aid in asset_ids:
            words = s.words(aid)
            if not words:
                notes.append(f"asset {aid} has no word-level transcript; filler words were not removed there")
            fill[aid] = edits.filler_ranges(words)
    new, removed = edits.tighten_clips(clips, sil, fill, padding=padding)
    if not new:
        raise ToolInputError("Everything would be removed; try a longer min_silence or lower noise_db.")
    result = s._commit({**(s.draft or {}), "version": 1, "output": dict(s._output(clips[0]["asset_id"])), "clips": new})
    if result.get("ok"):
        result = {"removed_seconds": round(removed, 2), "before_seconds": round(before, 2),
                  "fillers_removed": sum(len(v) for v in fill.values()), **result}
        result.pop("clips", None)  # can be hundreds of pieces; get_timeline shows them
        if notes:
            result["notes"] = notes
    return result


def tool_cut_ranges(s: EditSession, ranges: list[dict]) -> dict:
    if not s.draft:
        raise ToolInputError("There is no timeline yet; use set_timeline first.")
    by_asset: dict[str, list] = {}
    for r in ranges:
        s._asset_or_error(r["asset_id"])
        a, b = edits.snap_range(float(r["start"]), float(r["end"]), s.words(r["asset_id"]))
        by_asset.setdefault(r["asset_id"], []).append((a, b))
    new: list[dict] = []
    for c in s.draft["clips"]:
        pieces = edits.subtract_ranges((c["in"], c["out"]), by_asset.get(c["asset_id"], []))
        pieces = [p for p in pieces if p[1] - p[0] >= edits.MIN_PIECE_SECONDS]
        if len(pieces) == 1 and pieces[0] == (c["in"], c["out"]):
            new.append(c)
        else:
            for i, (a, b) in enumerate(pieces, 1):
                piece = {**c, "id": f"{c['id']}-{i}"[:64], "in": round(a, 3), "out": round(b, 3)}
                if i > 1:
                    piece.pop("transition_in", None)
                new.append(piece)
    if not new:
        raise ToolInputError("That would remove the whole timeline.")
    before = sum(c["out"] - c["in"] for c in s.draft["clips"])
    result = s._commit({**s.draft, "clips": new})
    if result.get("ok"):
        result["removed_seconds"] = round(before - result["duration_seconds"], 2)
    return result


_NUM = {"type": "number"}
TOOLS: dict[str, tuple[ToolSpec, Callable[..., dict]]] = {}


def _register(fn: Callable[..., dict], description: str, properties: dict | None = None,
              required: list[str] | None = None) -> None:
    name = fn.__name__.removeprefix("tool_")
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = required
    TOOLS[name] = (ToolSpec(name, description, schema), fn)


_register(tool_list_assets, "List the project's media: ids, kind, duration, size, and whether a transcript exists.")
_register(tool_get_timeline, "Show the current timeline: output format, total duration, and each clip with the words spoken in it.")
_register(tool_get_transcript,
          "Read an asset's transcript as timestamped segments (seconds). Optionally include per-word start times for precise cuts.",
          {"asset_id": {"type": "string"}, "start": _NUM, "end": _NUM,
           "include_words": {"type": "boolean", "description": "Add word@start timings (use for precise trims)."}},
          ["asset_id"])
_register(tool_search_transcripts, "Semantic search across all transcripts. Returns matching passages with timestamps.",
          {"query": {"type": "string"}, "n": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"])
_register(tool_find_silences, "Detect silent stretches in an asset's audio (seconds). Misses pauses under background "
          "noise or music; tighten_pacing also uses word timings.",
          {"asset_id": {"type": "string"}, "min_silence": _NUM,
           "noise_db": {"type": "number", "description": "Silence threshold, default -35 dB"}},
          ["asset_id"])
_register(tool_set_timeline,
          "Replace the timeline with these clips, in play order. Cuts are snapped to word boundaries by default. "
          "Optionally set the aspect ratio in the same call.",
          {"clips": {"type": "array", "minItems": 1, "items": {
              "type": "object",
              "properties": {"asset_id": {"type": "string"}, "in": _NUM, "out": _NUM,
                             "id": {"type": "string"}, "volume": _NUM,
                             "zoom": {"type": "number", "minimum": 1, "maximum": 2,
                                      "description": "Punch-in, e.g. 1.15"}},
              "required": ["asset_id", "in", "out"], "additionalProperties": False}},
           "aspect": {"type": "string", "enum": sorted(edits.ASPECT_PRESETS)},
           "fit": {"type": "string", "enum": ["cover", "contain"]},
           "snap_to_words": {"type": "boolean"}},
          ["clips"])
_register(tool_set_output_format, "Change the output aspect ratio (9:16 vertical, 16:9, 1:1, 4:5) and fit.",
          {"aspect": {"type": "string", "enum": sorted(edits.ASPECT_PRESETS)},
           "fit": {"type": "string", "enum": ["cover", "contain"]}},
          ["aspect"])
_register(tool_tighten_pacing,
          "Remove silences and filler words (um, uh, ...) from every clip on the timeline. With no timeline yet, "
          "starts from all video assets in full. Use for 'cut dead air', 'remove ums', 'make it snappier'.",
          {"remove_silences": {"type": "boolean"}, "remove_fillers": {"type": "boolean"},
           "min_silence": {"type": "number", "description": "Shortest pause to cut, seconds (default 0.6)"},
           "padding": {"type": "number", "description": "Pause kept on each side of a cut, seconds (default 0.15)"},
           "noise_db": _NUM})
_register(tool_cut_ranges, "Remove source ranges from the timeline wherever they appear (e.g. a tangent or a mistake).",
          {"ranges": {"type": "array", "minItems": 1, "items": {
              "type": "object", "properties": {"asset_id": {"type": "string"}, "start": _NUM, "end": _NUM},
              "required": ["asset_id", "start", "end"], "additionalProperties": False}}},
          ["ranges"])


# ---------------------------------------------------------------- styling tools (Phase 3)

def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def tool_set_captions(s: EditSession, enabled: bool = True, style: str | None = None, position: str | None = None,
                      size: float | None = None, color: str | None = None, highlight_color: str | None = None,
                      uppercase: bool | None = None, max_words: int | None = None) -> dict:
    draft = s.require_draft()
    if not enabled:
        return s._commit({k: v for k, v in draft.items() if k != "captions"})
    caps = {**(draft.get("captions") or {}), **_drop_none(dict(
        style=style, position=position, size=size, color=color, highlight_color=highlight_color,
        uppercase=uppercase, max_words=max_words))}
    result = s._commit({**draft, "captions": caps})
    missing = sorted({s.assets[c["asset_id"]].get("filename") or c["asset_id"]
                      for c in draft["clips"] if not s.words(c["asset_id"])})
    if result.get("ok") and missing:
        result["note"] = f"No word-level transcript for: {', '.join(missing)}; those clips will have no captions."
    return result


def tool_set_transitions(s: EditSession, type: str = "fade", duration: float = 0.5,
                         clip_ids: list[str] | None = None) -> dict:
    draft = s.require_draft()
    clips = [dict(c) for c in draft["clips"]]
    targets = set(clip_ids) if clip_ids else {c["id"] for c in clips[1:]}
    unknown = targets - {c["id"] for c in clips}
    if unknown:
        raise ToolInputError(f"Unknown clip ids: {sorted(unknown)}")
    skipped = []
    for i, c in enumerate(clips):
        if c["id"] not in targets or i == 0:
            continue
        if type == "none":
            c.pop("transition_in", None)
            continue
        shortest = min(c["out"] - c["in"], clips[i - 1]["out"] - clips[i - 1]["in"])
        d = round(min(duration, shortest * 0.45), 2)
        if d < 0.1:
            skipped.append(c["id"])
            c.pop("transition_in", None)
            continue
        c["transition_in"] = {"type": type, "duration": d}
    result = s._commit({**draft, "clips": clips})
    if result.get("ok") and skipped:
        result["note"] = f"Clips too short for a transition were left as hard cuts: {skipped}"
    return result


def tool_set_grade(s: EditSession, preset: str | None = None, brightness: float | None = None,
                   contrast: float | None = None, saturation: float | None = None,
                   temperature: float | None = None, lut_asset_id: str | None = None,
                   look: str | None = None, look_strength: float | None = None, clear: bool = False) -> dict:
    draft = s.require_draft()
    if clear:
        return s._commit({k: v for k, v in draft.items() if k != "grade"})
    grade = dict(draft.get("grade") or {})
    if look == "none":
        grade.pop("lut_preset", None)
        look = None
    if look:
        grade.pop("lut_asset_id", None)          # a built-in look replaces an uploaded or matched LUT
    if lut_asset_id:
        grade.pop("lut_preset", None)
    grade.update(_drop_none(dict(preset=preset, brightness=brightness, contrast=contrast, saturation=saturation,
                                 temperature=temperature, lut_asset_id=lut_asset_id, lut_preset=look,
                                 lut_strength=look_strength)))
    return s._commit({**draft, "grade": grade})


def tool_add_text(s: EditSession, text: str, start: float, end: float, style: str = "title",
                  position: str = "center", animation: str = "fade", size: float = 1.0,
                  color: str = "#FFFFFF") -> dict:
    draft = s.require_draft()
    layer = {"id": s.new_id("text"), "text": text, "start": start, "end": end, "style": style,
             "position": position, "animation": animation, "size": size, "color": color}
    result = s._commit({**draft, "text": [*draft.get("text", []), layer]})
    return {"layer_id": layer["id"], **result} if result.get("ok") else result


def tool_add_overlay(s: EditSession, asset_id: str, start: float, end: float, position: str = "top-right",
                     scale: float = 0.2, opacity: float = 1.0, fade: float = 0.3) -> dict:
    draft = s.require_draft()
    s._asset_or_error(asset_id)
    layer = {"id": s.new_id("overlay"), "asset_id": asset_id, "start": start, "end": end, "position": position,
             "scale": scale, "opacity": opacity, "fade": fade}
    result = s._commit({**draft, "overlays": [*draft.get("overlays", []), layer]})
    return {"layer_id": layer["id"], **result} if result.get("ok") else result


def tool_add_audio(s: EditSession, asset_id: str, start: float = 0, source_in: float = 0,
                   source_out: float | None = None, volume: float = 0.8, loop: bool = False, duck: bool = False,
                   fade_in: float = 0, fade_out: float = 0) -> dict:
    draft = s.require_draft()
    s._asset_or_error(asset_id)
    layer = _drop_none({"id": s.new_id("audio"), "asset_id": asset_id, "start": start, "in": source_in,
                        "out": source_out, "volume": volume, "loop": loop, "duck": duck,
                        "fade_in": fade_in, "fade_out": fade_out})
    result = s._commit({**draft, "audio": [*draft.get("audio", []), layer]})
    return {"layer_id": layer["id"], **result} if result.get("ok") else result


def tool_remove_layer(s: EditSession, layer_id: str) -> dict:
    draft = s.require_draft()
    for key in ("text", "overlays", "audio", "broll", "zooms"):
        layers = draft.get(key, [])
        if any(x["id"] == layer_id for x in layers):
            return s._commit({**draft, key: [x for x in layers if x["id"] != layer_id]})
    raise ToolInputError(f"No text, overlay, audio, B-roll or zoom layer with id {layer_id!r}")


def tool_get_captions(s: EditSession, start: float = 0, end: float | None = None) -> dict:
    tl = Timeline.model_validate(s.require_draft())
    if not tl.captions:
        return {"error": "Captions are off; turn them on with set_captions first."}
    transcripts = {c.asset_id: s.transcript(c.asset_id) for c in tl.clips}
    stop = end if end is not None else float("inf")
    lines = [ln for ln in captions_mod.caption_lines(tl, transcripts) if ln["end"] > start and ln["start"] < stop]
    return {"count": len(lines), "lines": lines[:200], **({"truncated": True} if len(lines) > 200 else {})}


def tool_edit_captions(s: EditSession, edits: list[dict]) -> dict:
    draft = s.require_draft()
    if not draft.get("captions"):
        raise ToolInputError("Captions are off; turn them on with set_captions first.")
    clean = []
    for e in edits:
        s._asset_or_error(e["asset_id"])
        clean.append({"asset_id": e["asset_id"], "start": float(e["start"]), "end": float(e["end"]),
                      "text": str(e.get("text", ""))})
    caps = {**draft["captions"]}
    caps["corrections"] = captions_mod.merge_caption_edits(caps.get("corrections", []), clean)
    return s._commit({**draft, "captions": caps})


_register(tool_get_captions,
          "List caption lines as rendered (output start/end, text) with their source range (asset_id, src_start, "
          "src_end). Use before edit_captions.",
          {"start": _NUM, "end": _NUM})
_register(tool_edit_captions,
          "Replace the text of caption lines (fix a misheard word, reword, or hide with empty text). Each edit "
          "covers a source range: copy asset_id, src_start and src_end from get_captions.",
          {"edits": {"type": "array", "minItems": 1, "items": {
              "type": "object",
              "properties": {"asset_id": {"type": "string"}, "start": _NUM, "end": _NUM,
                             "text": {"type": "string", "maxLength": 300}},
              "required": ["asset_id", "start", "end", "text"], "additionalProperties": False}}},
          ["edits"])


_POSITIONS = ["top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right"]
_COLOR = {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$", "description": "Hex colour like #FFD23F"}

_register(tool_set_captions,
          "Turn on word-by-word captions from the transcript, change their look, or turn them off (enabled=false). "
          "Styles: karaoke (current word highlighted), pop (1-2 big uppercase words), boxed (text on a dark box), "
          "minimal (plain subtitles). Only the fields you pass are changed.",
          {"enabled": {"type": "boolean"},
           "style": {"type": "string", "enum": ["karaoke", "pop", "boxed", "minimal"]},
           "position": {"type": "string", "enum": ["top", "middle", "bottom"]},
           "size": {"type": "number", "minimum": 0.5, "maximum": 2},
           "color": _COLOR, "highlight_color": _COLOR, "uppercase": {"type": "boolean"},
           "max_words": {"type": "integer", "minimum": 1, "maximum": 10}})
_register(tool_set_transitions,
          "Set the transition into the given clips (default: every cut). type 'none' makes them hard cuts. "
          "Durations are shortened automatically for short clips.",
          {"type": {"type": "string", "enum": ["none", *TRANSITIONS]},
           "duration": {"type": "number", "minimum": 0.1, "maximum": 2},
           "clip_ids": {"type": "array", "items": {"type": "string"}}},
          ["type"])
_register(tool_set_grade,
          "Colour-grade the whole video. Built-in looks (look, with look_strength 0-1): "
          + "; ".join(f"{p.id} = {p.description}" for p in luts_mod.PRESETS.values())
          + ". Also simple presets and fine adjustments, or a .cube LUT the user uploaded (lut_asset_id). "
          "clear=true removes the grade. Only the fields you pass are changed.",
          {"preset": {"type": "string", "enum": list(GRADE_PRESETS)},
           "brightness": {"type": "number", "minimum": -0.3, "maximum": 0.3},
           "contrast": {"type": "number", "minimum": 0.5, "maximum": 1.6},
           "saturation": {"type": "number", "minimum": 0, "maximum": 2},
           "temperature": {"type": "number", "minimum": -1, "maximum": 1, "description": "-1 cool, +1 warm"},
           "lut_asset_id": {"type": "string"},
           "look": {"type": "string", "enum": ["none", *luts_mod.PRESETS]},
           "look_strength": {"type": "number", "minimum": 0, "maximum": 1},
           "clear": {"type": "boolean"}})
_register(tool_add_text,
          "Add on-screen text at output time [start, end]: title (big display font), label, or lower_third "
          "(name and role on a band). Returns its layer_id.",
          {"text": {"type": "string", "maxLength": 200}, "start": _NUM, "end": _NUM,
           "style": {"type": "string", "enum": ["title", "label", "lower_third"]},
           "position": {"type": "string", "enum": _POSITIONS},
           "animation": {"type": "string", "enum": ["none", "fade", "pop", "slide_up", "typewriter"]},
           "size": {"type": "number", "minimum": 0.3, "maximum": 3}, "color": _COLOR},
          ["text", "start", "end"])
_register(tool_add_overlay,
          "Place one of the user's images or videos over the video at output time [start, end], e.g. a logo or "
          "picture-in-picture. scale = width as a fraction of the frame.",
          {"asset_id": {"type": "string"}, "start": _NUM, "end": _NUM,
           "position": {"type": "string", "enum": _POSITIONS},
           "scale": {"type": "number", "minimum": 0.03, "maximum": 1},
           "opacity": {"type": "number", "minimum": 0.05, "maximum": 1},
           "fade": {"type": "number", "minimum": 0, "maximum": 2}},
          ["asset_id", "start", "end"])
_register(tool_add_audio,
          "Add the user's music or a sound effect. start = output time it begins; source_in/source_out pick part of "
          "the file. Music: loop=true, duck=true (quieter under speech), volume 0.2-0.35, fade_out 1-2 s.",
          {"asset_id": {"type": "string"}, "start": _NUM, "source_in": _NUM, "source_out": _NUM,
           "volume": {"type": "number", "minimum": 0, "maximum": 2}, "loop": {"type": "boolean"},
           "duck": {"type": "boolean"}, "fade_in": _NUM, "fade_out": _NUM},
          ["asset_id"])
_register(tool_remove_layer, "Remove a text, overlay, audio or B-roll layer by its id (see get_timeline).",
          {"layer_id": {"type": "string"}}, ["layer_id"])


# ---------------------------------------------------------------- reference style tools (Phase 4)

def _reference(s: EditSession, asset_id: str) -> tuple[dict, dict]:
    a = s._asset_or_error(asset_id)
    if a.get("role") != "reference":
        raise ToolInputError(f"Asset {asset_id} is footage, not a style reference.")
    if a.get("analysis_status") != "done":
        raise ToolInputError(f"The reference is not analysed yet (status: {a.get('analysis_status')}).")
    profile = store.get_analysis(asset_id) or {}
    return a, profile


def tool_get_reference_style(s: EditSession, asset_id: str) -> dict:
    _, profile = _reference(s, asset_id)
    pacing = {k: v for k, v in profile.get("pacing", {}).items() if k != "cut_times"}
    color = {k: profile.get("color", {}).get(k) for k in ("brightness", "contrast", "saturation", "warmth")}
    return {"asset_id": asset_id, "summary": profile.get("summary"), "format": profile.get("format"),
            "pacing": pacing, "color": color, "audio": profile.get("audio"), "visual": profile.get("visual"),
            "suggestions": profile.get("suggestions")}


def _footage_frames(s: EditSession, clips: list[dict]):
    import numpy as np

    by_asset: dict[str, list[float]] = {}
    for c in clips[:16]:
        a = s.assets.get(c["asset_id"])
        if not a or a.get("kind") not in ("video", "image"):
            continue
        length = c["out"] - c["in"]
        by_asset.setdefault(c["asset_id"], []).extend([c["in"] + length * 0.3, c["in"] + length * 0.7])
    stacks = [analysis.frames_at(storage.path(s.assets[aid]["storage_key"]), times) for aid, times in by_asset.items()]
    if not stacks:
        raise ToolInputError("The timeline has no video frames to analyse.")
    return np.concatenate(stacks)


def tool_match_reference_color(s: EditSession, reference_asset_id: str, strength: float = 0.75) -> dict:
    draft = s.require_draft()
    ref, profile = _reference(s, reference_asset_id)
    ref_color = profile.get("color") or {}
    if "lab_mean" not in ref_color:
        raise ToolInputError("The reference has no colour statistics.")
    src = analysis.color_stats(_footage_frames(s, draft["clips"]))
    strength = max(0.1, min(1.0, float(strength)))
    lut = colormatch.transfer_lut(src["lab_mean"], src["lab_std"], ref_color["lab_mean"], ref_color["lab_std"],
                                  strength=strength)
    owner = next(iter(s.assets.values()))["user_id"]
    asset_id = store.new_id()
    key = storage.asset_key(owner, s.project_id, asset_id, ".cube")
    name = f"Colour match ({ref.get('filename') or 'reference'})"
    colormatch.write_cube(storage.path(key), lut, title=name)
    store.create_asset(s.project_id, owner, "generated", asset_id=asset_id, filename=f"{name}.cube",
                       storage_key=key, transcribe=False, role="generated", status="ready")
    store.update_asset(asset_id, kind="lut", has_video=False, has_audio=False)
    s.assets[asset_id] = store.get_asset(asset_id)
    result = s._commit({**draft, "grade": {"lut_asset_id": asset_id}})
    if result.get("ok"):
        result["footage_look"] = {k: src[k] for k in ("brightness", "contrast", "saturation", "warmth")}
        result["reference_look"] = {k: ref_color.get(k) for k in ("brightness", "contrast", "saturation", "warmth")}
    return result


def tool_apply_pacing(s: EditSession, target_shot_seconds: float, punch_in: float = 1.12) -> dict:
    draft = s.require_draft()
    target = max(0.8, min(30.0, float(target_shot_seconds)))
    zoom = None if not punch_in or punch_in <= 1.0 else min(1.5, float(punch_in))
    ids = {c["asset_id"] for c in draft["clips"]}
    words = {aid: s.words(aid) for aid in ids}
    segments = {aid: (s.transcript(aid) or {}).get("segments") or [] for aid in ids}
    clips, added = edits.split_for_pacing(draft["clips"], words, segments, target, zoom)
    if not added:
        return {"ok": True, "note": "Nothing to split: clips are already short, or have no transcript to find "
                                    "phrase boundaries.", **s.timeline_summary()}
    result = s._commit({**draft, "clips": clips})
    if result.get("ok"):
        result["cuts_added"] = added
        result.pop("clips", None)
    return result


_register(tool_get_reference_style,
          "Read a reference video's style profile: format, pacing (cuts per minute, typical shot length), colour, "
          "audio (tempo, speaking pace), what a vision model saw (captions, text, framing, transitions, energy), "
          "and suggested settings.",
          {"asset_id": {"type": "string"}}, ["asset_id"])
_register(tool_match_reference_color,
          "Grade the whole video to look like a reference: builds a colour-matching LUT from the reference's "
          "colour statistics and the current footage. strength 0.1-1 (default 0.75). Replaces any existing grade.",
          {"reference_asset_id": {"type": "string"}, "strength": {"type": "number", "minimum": 0.1, "maximum": 1}},
          ["reference_asset_id"])
_register(tool_apply_pacing,
          "Match a faster editing rhythm: split long clips at phrase boundaries into shots of about "
          "target_shot_seconds, alternating a punch-in zoom so each split reads as a cut (jump-cut style). "
          "punch_in=1 splits without zooming.",
          {"target_shot_seconds": {"type": "number", "minimum": 0.8, "maximum": 30},
           "punch_in": {"type": "number", "minimum": 1, "maximum": 1.5}},
          ["target_shot_seconds"])


STYLE_PARTS = ("format", "pacing", "captions", "transitions", "color")


def tool_apply_reference_style(s: EditSession, reference_asset_id: str, parts: list[str] | None = None,
                               color_strength: float = 0.75) -> dict:
    """Apply a reference's suggested format, pacing, captions, transitions and colour in one go."""
    _, profile = _reference(s, reference_asset_id)
    sug = profile.get("suggestions") or {}
    wanted = set(parts or STYLE_PARTS)
    unknown = wanted - set(STYLE_PARTS)
    if unknown:
        raise ToolInputError(f"Unknown parts {sorted(unknown)}; choose from {list(STYLE_PARTS)}")
    applied: dict[str, object] = {}
    skipped: dict[str, str] = {}

    if not s.draft:
        clips = _base_clips(s)
        started = s._commit({"version": 1, "output": dict(s._output(clips[0]["asset_id"])), "clips": clips})
        if not started.get("ok"):
            return started

    def run(part: str, fn, *args, **kwargs) -> None:
        result = fn(s, *args, **kwargs)
        if result.get("ok") is False or "error" in result:
            skipped[part] = "; ".join(result.get("errors") or [result.get("error", "failed")])[:300]
        else:
            applied[part] = kwargs or (args[0] if args else True)

    if "format" in wanted:
        if sug.get("aspect"):
            run("format", tool_set_output_format, aspect=sug["aspect"], fit="cover")
        else:
            skipped["format"] = "the reference's shape is unknown"
    if "pacing" in wanted:
        target = sug.get("target_shot_seconds")
        clips = s.draft["clips"]
        current = sum(c["out"] - c["in"] for c in clips) / max(1, len(clips))
        if not target:
            skipped["pacing"] = "the reference has too few cuts to set a rhythm"
        elif current <= target * 1.5:
            skipped["pacing"] = f"already paced at about {current:.1f}s per shot (reference {target:.1f}s)"
        else:
            # Without punch-ins a split inside one take is invisible, so use at least a subtle zoom.
            run("pacing", tool_apply_pacing, target_shot_seconds=target,
                punch_in=1.12 if sug.get("punch_ins") else 1.06)
    if "captions" in wanted:
        if sug.get("captions"):
            run("captions", tool_set_captions, **sug["captions"])
        else:
            skipped["captions"] = "the reference has no captions (existing captions were left as they are)"
    if "transitions" in wanted:
        t = sug.get("transition")
        if len(s.draft["clips"]) < 2:
            skipped["transitions"] = "only one clip"
        elif t:
            run("transitions", tool_set_transitions, type=t["type"], duration=t["duration"])
        else:
            run("transitions", tool_set_transitions, type="none")
    if "color" in wanted:
        run("color", tool_match_reference_color, reference_asset_id=reference_asset_id, strength=color_strength)

    summary = s.timeline_summary()
    summary.pop("clips", None)
    return {"ok": bool(applied), "applied": applied, "skipped": skipped, **summary}


_register(tool_apply_reference_style,
          "Make the edit match a reference's style in one step, exactly as analysed: format (aspect, crop to fill), "
          "pacing (jump cuts at the reference's shot length), captions (style, position, colours), transitions "
          "(or hard cuts) and colour (a matching LUT). parts limits it, e.g. ['captions','color']. Reports what was "
          "applied and what was skipped and why.",
          {"reference_asset_id": {"type": "string"},
           "parts": {"type": "array", "items": {"type": "string", "enum": list(STYLE_PARTS)}},
           "color_strength": {"type": "number", "minimum": 0.1, "maximum": 1}},
          ["reference_asset_id"])


# ---------------------------------------------------------------- B-roll (Phase 6)

def _norm_word(w: str) -> str:
    return edits._norm(w)


def tool_find_phrase(s: EditSession, text: str, limit: int = 5) -> dict:
    """Where a phrase is spoken in the edited video (output time)."""
    tl = Timeline.model_validate(s.require_draft())
    words = captions_mod.output_words(
        Timeline.model_validate({**tl.to_json(), "captions": {"style": "minimal"}}),
        {c.asset_id: s.transcript(c.asset_id) for c in tl.clips},
    )
    target = [t for t in (_norm_word(x) for x in text.split()) if t]
    if not target:
        raise ToolInputError("Give the words to look for.")
    norm = [_norm_word(w.text) for w in words]
    hits = []
    for i in range(len(norm) - len(target) + 1):
        if norm[i:i + len(target)] == target:
            hits.append({"start": round(words[i].start, 2), "end": round(words[i + len(target) - 1].end, 2),
                         "context": " ".join(w.text for w in words[max(0, i - 4):i + len(target) + 4])})
    if not hits and len(target) == 1:
        hits = [{"start": round(w.start, 2), "end": round(w.end, 2), "context": w.text}
                for w, n in zip(words, norm) if target[0] in n][:limit]
    return {"phrase": text, "matches": hits[:limit], "note": None if hits else "Not found in the current edit."}


def tool_list_broll(s: EditSession) -> dict:
    items = []
    for a in s.assets.values():
        if a.get("role") != "broll" or a["status"] != "ready":
            continue
        analysis = store.get_analysis(a["id"]) or {}
        items.append({
            "asset_id": a["id"], "name": a.get("filename"), "kind": a.get("kind"),
            "duration_seconds": a.get("duration"), "width": a.get("width"), "height": a.get("height"),
            "source": {"pexels": "stock (Pexels)", "pixabay": "stock (Pixabay)",
                       "ai": "AI-generated still"}.get(a.get("source"), "uploaded"),
            "shots": [{k: v for k, v in sh.items() if k in ("start", "end", "description", "tags")}
                      for sh in analysis.get("shots", [])][:40],
        })
    return {"broll": items, "note": None if items else "No B-roll in the project yet; search stock footage."}


def tool_search_stock_broll(s: EditSession, query: str, orientation: str | None = None, n: int = 6) -> dict:
    from . import broll

    if not broll.stock_available():
        return {"error": "No stock footage source is set up (needs PIXABAY_API_KEY). Use generate_broll_image "
                         "or the user's own B-roll instead."}
    if orientation is None and s.draft:
        o = s.draft["output"]
        orientation = "portrait" if o["height"] > o["width"] else "square" if o["height"] == o["width"] else "landscape"
    try:
        results = broll.search_stock(query, orientation, per_page=max(1, min(n, 10)))
    except broll.StockError as exc:
        return {"error": str(exc)}
    return {"query": query, "orientation": orientation, "results": [
        {"stock_id": f"{r['provider']}-{r['id']}", "duration_seconds": r["duration"], "width": r["width"],
         "height": r["height"], "by": r["credit"], "source": broll.provider_name(r["provider"]),
         **({"tags": r["tags"]} if r.get("tags") else {})} for r in results]}


def tool_add_stock_broll(s: EditSession, stock_id: str, description: str = "") -> dict:
    """Download a stock clip into the project (takes a few seconds) and return its asset_id."""
    from . import broll

    owner = next(iter(s.assets.values()))["user_id"] if s.assets else None
    if owner is None:
        raise ToolInputError("Add footage first.")
    provider, _, vid = str(stock_id).rpartition("-")
    provider = provider or "pixabay"
    try:
        clip = broll.get_stock(vid, provider)
        asset = store.create_asset(s.project_id, owner, provider,
                                   filename=f"{clip['credit'] or 'Stock'} on {broll.provider_name(provider)} "
                                            f"({clip['id']}).mp4",
                                   source_url=clip["page_url"], transcribe=False, role="broll")
        key = storage.asset_key(owner, s.project_id, asset["id"], ".mp4")
        broll.download(clip["download_url"], storage.path(key))
        info = media.probe(storage.path(key))
    except (broll.StockError, media.MediaError) as exc:
        return {"error": str(exc)}
    store.update_asset(asset["id"], storage_key=key, status="ready", analysis_status="done",
                       analysis={"version": 1, "described": bool(description),
                                 "shots": [{"start": 0.0, "end": info["duration"] or 0.0,
                                            "description": description[:200]}]}, **info)
    s.assets[asset["id"]] = store.get_asset(asset["id"])
    return {"asset_id": asset["id"], "duration_seconds": info["duration"], "width": info["width"],
            "height": info["height"], "credit": clip["credit"]}


def tool_generate_broll_image(s: EditSession, prompt: str, orientation: str | None = None) -> dict:
    """Generate an AI still for B-roll (about 10-30 s); returns its asset_id for add_broll."""
    from . import genimage

    owner = next(iter(s.assets.values()))["user_id"] if s.assets else None
    if owner is None:
        raise ToolInputError("Add footage first.")
    if orientation is None and s.draft:
        o = s.draft["output"]
        orientation = "portrait" if o["height"] > o["width"] else "square" if o["height"] == o["width"] else "landscape"
    asset = store.create_asset(s.project_id, owner, "ai", filename=f"AI: {prompt[:60]}.png", transcribe=False,
                               role="broll")
    key = storage.asset_key(owner, s.project_id, asset["id"], ".png")
    try:
        genimage.generate(prompt, storage.path(key), orientation or "landscape")
        info = media.probe(storage.path(key))
    except (genimage.ImageError, media.MediaError) as exc:
        store.update_asset(asset["id"], status="failed", error=str(exc)[:500])
        return {"error": str(exc)}
    store.update_asset(asset["id"], storage_key=key, status="ready", analysis_status="done",
                       analysis={"version": 1, "described": True,
                                 "shots": [{"start": 0.0, "end": 0.0, "description": prompt[:200]}]}, **info)
    s.assets[asset["id"]] = store.get_asset(asset["id"])
    return {"asset_id": asset["id"], "kind": "image", "width": info["width"], "height": info["height"],
            "note": "A still: place it with add_broll and a motion (push_in or a pan) so it moves."}


def tool_add_broll(s: EditSession, asset_id: str, start: float, duration: float, source_in: float = 0,
                   motion: str = "push_in", volume: float = 0.0) -> dict:
    draft = s.require_draft()
    a = s._asset_or_error(asset_id)
    note = None
    shot = None
    if a.get("kind") == "video":
        shots = (store.get_analysis(asset_id) or {}).get("shots", [])
        shot = next((sh for sh in shots if sh["start"] - 0.05 <= source_in < sh["end"]), None)
        if shot and source_in + duration > shot["end"] + 0.05:
            # A cutaway that runs into the next shot shows an unrelated image at its end.
            duration = round(shot["end"] - source_in, 3)
            note = f"Shortened to {duration:.1f}s so it stays inside one shot."
        if a.get("duration"):
            duration = min(duration, max(0.3, a["duration"] - source_in))
    if duration < 0.5:
        raise ToolInputError("That leaves less than 0.5 s of the shot; start earlier in the shot (use its start).")
    layer = {"id": s.new_id("broll"), "asset_id": asset_id, "start": round(start, 3),
             "duration": round(duration, 3), "in": round(source_in, 3), "motion": motion, "volume": volume}
    # Emphasis zooms act on the speaker; under a cutaway they can't be seen, so they go.
    end = start + duration
    zooms = draft.get("zooms", [])
    kept = [z for z in zooms if not (z["start"] < end and start < z["end"])]
    if len(kept) < len(zooms):
        note = ((note + " ") if note else "") + f"Removed {len(zooms) - len(kept)} emphasis zoom(s) hidden under it."
    result = s._commit({**draft, "zooms": kept, "broll": [*draft.get("broll", []), layer]})
    if result.get("ok"):
        result = {"layer_id": layer["id"], "ok": True, "duration_seconds": result.get("duration_seconds"),
                  "placed": {**layer, "shows": (shot or {}).get("description") or "(no description)"},
                  **({"note": note} if note else {}), "warnings": result.get("warnings")}
    return result


_register(tool_find_phrase,
          "Find when words are spoken in the current edit (output time): use it to put B-roll, text or sound "
          "effects exactly on a phrase.",
          {"text": {"type": "string", "maxLength": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
          ["text"])
_register(tool_list_broll,
          "List the project's B-roll (the user's cutaway clips and imported stock) with each shot's time range "
          "and description.")
_register(tool_search_stock_broll,
          "Search free stock video (Pexels) for B-roll. Orientation defaults to the edit's shape. Use concrete "
          "visual queries ('pouring coffee close up', not 'productivity').",
          {"query": {"type": "string", "maxLength": 100},
           "orientation": {"type": "string", "enum": ["landscape", "portrait", "square"]},
           "n": {"type": "integer", "minimum": 1, "maximum": 10}},
          ["query"])
_register(tool_add_stock_broll,
          "Import a stock clip from search_stock_broll into the project; returns its asset_id for add_broll.",
          {"stock_id": {"type": "string"}, "description": {"type": "string", "maxLength": 200}},
          ["stock_id"])
_register(tool_generate_broll_image,
          "Generate a photorealistic still with AI to use as B-roll when the user has no matching clip and stock "
          "isn't available or has nothing good. Describe one concrete scene (subject, setting, light); no text. "
          "Use sparingly (each costs money and ~20 s).",
          {"prompt": {"type": "string", "maxLength": 500},
           "orientation": {"type": "string", "enum": ["landscape", "portrait", "square"]}},
          ["prompt"])
_register(tool_add_broll,
          "Show B-roll over the main video from output time start for duration seconds (the speaker's audio "
          "continues). source_in picks the part of the B-roll clip (use a shot's start). motion adds gentle "
          "camera movement.",
          {"asset_id": {"type": "string"}, "start": _NUM, "duration": {"type": "number", "minimum": 0.3, "maximum": 60},
           "source_in": _NUM, "motion": {"type": "string", "enum": ["none", "push_in", "pull_out", "pan_left", "pan_right"]},
           "volume": {"type": "number", "minimum": 0, "maximum": 2}},
          ["asset_id", "start", "duration"])


# ---------------------------------------------------------------- emphasis pack (Phase 6, step 2)

MAX_EMPHASIS = 1.5      # seconds: emphasis is a beat, not a scene
SFX_GAP = 0.35          # seconds between automatic sound effects


def tool_emphasize(s: EditSession, items: list[dict]) -> dict:
    """Keyword pop + punch-in zoom + sound on spoken phrases, each timed to the words."""
    draft = s.require_draft()
    text, zooms, audio = list(draft.get("text", [])), list(draft.get("zooms", [])), list(draft.get("audio", []))
    placed, missing, notes = [], [], []
    reserved: set[str] = set()
    covered = [(b["start"], b["start"] + b["duration"]) for b in draft.get("broll", [])]
    for item in items[:20]:
        phrase = str(item.get("phrase", "")).strip()
        hits = tool_find_phrase(s, phrase, limit=10)["matches"] if phrase else []
        occurrence = int(item.get("occurrence", 1))
        if len(hits) < occurrence or occurrence < 1:
            missing.append(phrase)
            continue
        hit = hits[occurrence - 1]
        start = hit["start"]
        end = min(hit["end"], start + MAX_EMPHASIS)       # long word timings (a stretched "um") stay a beat
        label = str(item.get("text") or phrase)[:40]
        if item.get("pop", True):
            text.append({"id": s.new_id("kw", reserved), "text": label, "start": round(max(0, start - 0.03), 3),
                         "end": round(min(max(end + 0.45, start + 0.8), start + 1.6), 3), "style": "keyword",
                         "position": item.get("position", "center"), "animation": "pop",
                         "color": item.get("color", "#FFD23F"), "size": 1.0})
        under_broll = any(a < end and start < b for a, b in covered)
        if item.get("zoom", True) and under_broll:
            notes.append(f"No zoom on “{phrase}”: B-roll covers the speaker there.")
        elif item.get("zoom", True):
            zooms.append({"id": s.new_id("zoom", reserved), "start": round(max(0, start - 0.06), 3),
                          "end": round(end + 0.35, 3), "scale": float(item.get("zoom_scale", 1.15)), "focus_y": 0.4})
        sound = item.get("sound", "pop")
        if sound:
            audio.append({"id": s.new_id("sfx", reserved), "sfx": sound, "start": round(max(0, start - 0.03), 3),
                          "volume": 0.55})
        placed.append({"phrase": phrase, "at": start, "text": label})
    if not placed:
        return {"error": f"None of the phrases were found in the current edit: {missing}"}
    result = s._commit({**draft, "text": text, "zooms": zooms, "audio": audio})
    if result.get("ok"):
        result = {"ok": True, "placed": placed, **({"not_found": missing} if missing else {}),
                  **({"notes": notes} if notes else {}), "duration_seconds": result.get("duration_seconds")}
    return result


def tool_add_zoom(s: EditSession, start: float, end: float, scale: float = 1.15, focus_y: float = 0.4) -> dict:
    draft = s.require_draft()
    layer = {"id": s.new_id("zoom"), "start": round(start, 3), "end": round(end, 3), "scale": scale, "focus_y": focus_y}
    result = s._commit({**draft, "zooms": [*draft.get("zooms", []), layer]})
    return {"layer_id": layer["id"], **result} if result.get("ok") else result


def tool_add_sound_effect(s: EditSession, sound: str, start: float, volume: float = 0.6) -> dict:
    draft = s.require_draft()
    layer = {"id": s.new_id("sfx"), "sfx": sound, "start": round(max(0, start), 3), "volume": volume}
    result = s._commit({**draft, "audio": [*draft.get("audio", []), layer]})
    return {"layer_id": layer["id"], "ok": True} if result.get("ok") else result


def tool_auto_sound_effects(s: EditSession, on: list[str] | None = None, volume: float = 0.5) -> dict:
    """Place sounds by rule: whoosh into cutaways, swipe on transitions, pop on keyword pops, click on cuts."""
    draft = s.require_draft()
    tl = Timeline.model_validate(draft)
    wanted = set(on or ["broll", "transitions", "keywords"])
    audio = [a for a in draft.get("audio", [])]
    reserved: set[str] = set()
    taken = [a["start"] for a in audio if a.get("sfx")]
    added: dict[str, int] = {}

    def place(sound: str, t: float, why: str) -> None:
        t = round(max(0.0, t), 3)
        if t >= tl.duration or any(abs(t - x) < SFX_GAP for x in taken):
            return                                   # never stack two effects on one moment
        audio.append({"id": s.new_id("sfx", reserved), "sfx": sound, "start": t, "volume": volume})
        taken.append(t)
        added[why] = added.get(why, 0) + 1

    if "broll" in wanted:
        for b in tl.broll:
            place("whoosh", b.start - 0.2, "broll")
    if "transitions" in wanted:
        for c, (a, _) in zip(tl.clips, tl.clip_spans()):
            if c.transition_in:
                place("swipe", a, "transitions")
    if "keywords" in wanted:
        for t in tl.text:
            if t.style in ("keyword", "title"):
                place("pop" if t.style == "keyword" else "impact", t.start, "keywords")
    if "cuts" in wanted:
        for c, (a, _) in list(zip(tl.clips, tl.clip_spans()))[1:]:
            if not c.transition_in:
                place("click", a, "cuts")
    if not added:
        return {"ok": True, "note": "Nothing new to add (no matching moments, or effects already there)."}
    result = s._commit({**draft, "audio": audio})
    return {"ok": True, "added": added} if result.get("ok") else result


def tool_set_audio_polish(s: EditSession, normalize: bool | None = None, denoise: str | None = None,
                          voice: bool | None = None) -> dict:
    draft = s.require_draft()
    polish = {**(draft.get("audio_polish") or {}), **_drop_none(dict(normalize=normalize, denoise=denoise, voice=voice))}
    return s._commit({**draft, "audio_polish": polish})


_SFX = ["whoosh", "swipe", "riser", "pop", "ding", "click", "impact"]
_register(tool_emphasize,
          "Emphasise key moments: for each spoken phrase, slam it on screen as a big keyword (pop), punch in on the "
          "speaker (zoom) and play a sound, all timed to the words. Pick 1-2 words that carry the point (numbers, "
          "results, strong claims); text can shorten the phrase. Use sparingly: about one every 5-10 seconds.",
          {"items": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
              "type": "object",
              "properties": {"phrase": {"type": "string", "description": "Words as spoken, found with find_phrase"},
                             "text": {"type": "string", "description": "What to show (default: the phrase)"},
                             "occurrence": {"type": "integer", "minimum": 1},
                             "pop": {"type": "boolean"}, "zoom": {"type": "boolean"},
                             "zoom_scale": {"type": "number", "minimum": 1.05, "maximum": 1.4},
                             "sound": {"type": ["string", "null"], "enum": [*_SFX, None]},
                             "position": {"type": "string", "enum": ["center", "bottom", "top"]},
                             "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}},
              "required": ["phrase"], "additionalProperties": False}}},
          ["items"])
_register(tool_add_zoom, "Quick punch-in on the speaker between output times start and end (scale 1.05-1.6).",
          {"start": _NUM, "end": _NUM, "scale": {"type": "number", "minimum": 1.03, "maximum": 1.6},
           "focus_y": {"type": "number", "minimum": 0, "maximum": 1}}, ["start", "end"])
_register(tool_add_sound_effect,
          "Play a built-in sound at output time start: whoosh (cuts, cutaways), swipe (fast cuts, text sliding), "
          "riser (build-up before a reveal, ~1.5 s, start it before the moment), pop (text), ding (key point), "
          "click, impact (big statement).",
          {"sound": {"type": "string", "enum": _SFX}, "start": _NUM,
           "volume": {"type": "number", "minimum": 0, "maximum": 1.5}}, ["sound", "start"])
_register(tool_auto_sound_effects,
          "Add sound effects by rule without stacking: whoosh into each B-roll cutaway, swipe on transitions, pop "
          "on keyword pops (impact on titles), optionally click on hard cuts.",
          {"on": {"type": "array", "items": {"type": "string", "enum": ["broll", "transitions", "keywords", "cuts"]}},
           "volume": {"type": "number", "minimum": 0.1, "maximum": 1}})
_register(tool_set_audio_polish,
          "Clean up the sound: normalize (loudness for social platforms, -14 LUFS), denoise (light/strong background "
          "noise reduction), voice (clearer, more present voice). Only the fields you pass change.",
          {"normalize": {"type": "boolean"}, "denoise": {"type": "string", "enum": ["off", "light", "strong"]},
           "voice": {"type": "boolean"}})


def execute_tool(s: EditSession, call: ToolCall) -> tuple[str, bool]:
    entry = TOOLS.get(call.name)
    if entry is None:
        return json.dumps({"error": f"Unknown tool {call.name}"}), False
    if call.arguments is None:
        return json.dumps({"error": "Arguments were not valid JSON; send a JSON object."}), False
    try:
        result = entry[1](s, **call.arguments)
        ok = "error" not in result and result.get("ok", True) is not False
    except (ToolInputError, TypeError, KeyError, ValueError) as exc:
        result, ok = {"error": str(exc)}, False
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + '... [truncated]'
    return text, ok


# ---------------------------------------------------------------- loop

def _context_block(s: EditSession) -> str:
    assets = ", ".join(
        f"{a.get('filename') or a['id']} ({a.get('kind')}, {_mmss(a['duration']) if a.get('duration') else 'still'})"
        for a in s.assets.values()
    ) or "none"
    if s.draft:
        dur = sum(c["out"] - c["in"] for c in s.draft["clips"])
        o = s.draft["output"]
        tl = f"{len(s.draft['clips'])} clips, {_mmss(dur)}, {o['width']}x{o['height']}"
    else:
        tl = "empty"
    return f"[Project state] Assets: {assets}. Timeline: {tl}."


def tidy_reply(text: str) -> str:
    """Turn a markdown-ish answer (headings, bullets, bold) into plain sentences for the chat."""
    import re

    parts = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"(#+\s*)?[A-Z][\w '’]{0,40}:", line):      # "What I changed:" style headings
            continue
        line = re.sub(r"^(#+|[-*•]|\d+[.)])\s+", "", line)           # heading marks, bullets, numbering
        line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line)
        if line and line[-1] not in ".!?:\"”'":
            line += "."
        parts.append(line)
    return " ".join(parts).strip()


def run(provider: LLMProvider, project_id: str, user_message: str, history: list[dict],
        on_step: Callable[[int, str], None] | None = None) -> AgentResult:
    s = EditSession(project_id)
    tools = [spec for spec, _ in TOOLS.values()]
    messages: list[dict] = [
        {"role": m["role"], "content": m["content"]} for m in history[-HISTORY_MESSAGES:]
        if m["role"] in ("user", "assistant") and m.get("content")
    ]
    messages.append({"role": "user", "content": f"{_context_block(s)}\n\n{user_message}"})

    trace: list[dict] = []
    usage: dict[str, int] = {}
    reply = None
    for step in range(MAX_STEPS):
        turn = provider.complete(SYSTEM_PROMPT, messages, tools)
        for k, v in (turn.usage or {}).items():
            if isinstance(v, int):
                usage[k] = usage.get(k, 0) + v
        messages.append({"role": "assistant", "content": turn.text, "tool_calls": turn.tool_calls})
        if not turn.tool_calls:
            reply = (turn.text or "").strip()
            break
        for call in turn.tool_calls:
            if on_step:
                on_step(step, call.name)
            content, ok = execute_tool(s, call)
            trace.append({"tool": call.name, "ok": ok})
            log.info("tool %s ok=%s", call.name, ok)
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content})

    errors: list[str] = []
    version = None
    if s.changed and s.draft:
        _, errors = s._validate(s.draft)
        if not errors:
            version = store.save_timeline(project_id, s.draft, note=f"Agent: {user_message[:200]}")
    reply = tidy_reply(reply) if reply else reply
    if reply is None:
        reply = "I ran out of steps before finishing." + (" I saved what I had so far." if version else "")
    if not reply:
        reply = "Done." if version else "I didn't change anything."
    return AgentResult(reply=reply, timeline_version=version, changed=version is not None,
                       trace=trace, usage=usage, errors=errors)
