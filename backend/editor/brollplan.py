"""
B-roll planning from the transcript: understand the video first, then search stock with concrete keywords.

1. The language model reads the whole edited transcript (output time) and picks the moments where a cutaway
   would show something concrete that is being said, with 2-3 short visual search queries for each.
2. Each moment is located in the transcript (exact words, so times are real, never guessed) and checked
   against the B-roll rules: not in the opening hook, no overlap, at most about a third of the runtime.
3. Stock is searched per moment (queries in order until there are candidates), and a vision model looks at
   the candidates' thumbnails and scores how well each shows the moment; weak matches are dropped rather
   than placed. Without a vision model, candidates are ranked by how many query words their tags share.

The agent tool `plan_broll` turns this plan into cutaways (download + place).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from . import broll, edits

log = logging.getLogger(__name__)

HOOK_SECONDS = 2.0        # keep the speaker's face on screen at the start
MAX_SHARE = 1 / 3         # of the runtime covered by B-roll
MIN_LEN, MAX_LEN = 1.5, 4.0
MIN_SCORE = 6             # vision score (0-10) a clip needs to be used
CANDIDATES = 5            # thumbnails shown to the vision model per moment

PLAN_SYSTEM = """You are a video editor planning B-roll (cutaway footage shown while the speaker keeps talking)
for a short social video. You get the transcript with timestamps. Understand what the video is about first,
then choose the moments where showing something would help the viewer: concrete objects, places, actions,
examples, numbers made visible. Skip greetings, the opening hook, jokes and reactions that need the face, and
abstract filler. Answer only with JSON."""

PLAN_PROMPT = """Transcript (m:ss, then what is said):
{transcript}

The video is {duration:.0f} seconds long. Pick up to {count} moments for B-roll, spread through the video.
For each moment give:
- "quote": 2-6 consecutive words copied exactly from the transcript where the cutaway should start
- "visual": one sentence describing the shot the viewer should see
- "queries": 2-3 stock-footage search queries, most specific first: 1-3 plain English words naming visible
  things (for example "elephant herd", "pouring coffee", "city traffic night"), never abstract ideas or
  words like "concept", "b-roll" or "footage"
{extra}
Answer as {{"topic": "what the video is about, one sentence", "moments": [{{"quote": "...", "visual": "...",
"queries": ["...", "..."]}}]}}"""

SCORE_SYSTEM = "You judge stock footage thumbnails for B-roll. Answer only with JSON."
SCORE_PROMPT = """The cutaway should show: {visual}
The speaker is saying: "{context}"
Score each of the {n} images from 0 to 10 for how clearly it shows that (10 = exactly it, 6 = clearly related and
usable, 3 = loosely related, 0 = unrelated or misleading). Answer as {{"scores": [n1, n2, ...]}} in image order."""


@dataclass
class Moment:
    quote: str
    visual: str
    queries: list[str]
    start: float = 0.0
    end: float = 0.0
    context: str = ""
    skipped: Optional[str] = None
    query_used: Optional[str] = None
    candidates: list[dict] = field(default_factory=list)
    choice: Optional[dict] = None
    score: Optional[float] = None

    @property
    def duration(self) -> float:
        return self.end - self.start


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def transcript_lines(words: list, max_chars: int = 24000) -> str:
    """Transcript as timestamped lines, one per phrase."""
    lines, cur, start = [], [], None
    for w in words:
        if start is None:
            start = w.start
        cur.append(w.text)
        if w.ends_phrase or len(cur) >= 18:
            lines.append(f"[{mmss(start)}] {' '.join(cur)}")
            cur, start = [], None
    if cur:
        lines.append(f"[{mmss(start)}] {' '.join(cur)}")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n[...]"


def locate(words: list, quote: str) -> Optional[tuple[int, int]]:
    """Index range of `quote` in the words: exact sequence first, then the best partial overlap."""
    target = [t for t in (edits._norm(x) for x in quote.split()) if t]
    norm = [edits._norm(w.text) for w in words]
    if not target or not norm:
        return None
    n = len(target)
    for i in range(len(norm) - n + 1):
        if norm[i:i + n] == target:
            return i, i + n - 1
    best, best_i = 0.0, None
    for i in range(len(norm)):
        window = norm[i:i + n]
        score = sum(1 for a, b in zip(window, target) if a == b) / n
        if score > best:
            best, best_i = score, i
    if best_i is not None and best >= 0.6:
        return best_i, min(len(norm), best_i + n) - 1
    return None


def _overlaps(a: tuple[float, float], spans: list[tuple[float, float]], gap: float = 0.5) -> bool:
    return any(a[0] < b[1] + gap and b[0] < a[1] + gap for b in spans)


def place(moments: list[Moment], words: list, duration: float, taken: list[tuple[float, float]]) -> None:
    """Give each moment real times from the transcript and apply the B-roll rules (sets `skipped`)."""
    budget = duration * MAX_SHARE - sum(b - a for a, b in taken)
    spans = list(taken)
    for m in moments:
        hit = locate(words, m.quote)
        if hit is None:
            m.skipped = "its words weren't found in the edit"
            continue
        i, j = hit
        start = max(0.0, words[i].start - 0.15)
        if start < HOOK_SECONDS:
            m.skipped = "it's in the opening seconds (the hook)"
            continue
        # run on through the phrase it illustrates, within 1.5-4 s
        k = j
        while k + 1 < len(words) and words[k + 1].end - start <= MAX_LEN and not words[k].ends_phrase:
            k += 1
        end = min(duration, max(start + MIN_LEN, min(words[k].end + 0.2, start + MAX_LEN)))
        if end - start < MIN_LEN - 0.01:
            m.skipped = "too close to the end"
            continue
        if _overlaps((start, end), spans):
            m.skipped = "it overlaps other B-roll"
            continue
        if end - start > budget:
            if budget < MIN_LEN:
                m.skipped = "B-roll already covers a third of the video"
                continue
            end = start + budget            # shorten it to what's left of the budget
        m.start, m.end = round(start, 2), round(end, 2)
        m.context = " ".join(w.text for w in words[max(0, i - 6):min(len(words), k + 6)])
        spans.append((start, end))
        budget -= end - start


def _tag_score(result: dict, queries: list[str]) -> float:
    tags = {t.strip().lower() for t in (result.get("tags") or "").split(",")}
    words = {w for q in queries for w in q.lower().split()}
    return float(len(words & {w for t in tags for w in t.split()}))


def _thumb(url: Optional[str], client: httpx.Client) -> Optional[bytes]:
    if not url:
        return None
    try:
        r = client.get(url)
        r.raise_for_status()
        return r.content if r.headers.get("content-type", "").startswith("image/") or r.content[:3] == b"\xff\xd8\xff" else None
    except httpx.HTTPError:
        return None


def choose(m: Moment, orientation: Optional[str], vision: Optional[Callable[[str, str, list[bytes]], dict]],
           search: Optional[Callable[..., list[dict]]] = None, client: Optional[httpx.Client] = None) -> None:
    """Search stock for a placed moment and pick the clip that best shows it (sets choice, or skipped)."""
    search = search or broll.search_stock
    for q in m.queries[:3]:
        try:
            found = search(q, orientation, per_page=8)
        except broll.StockError as exc:
            m.skipped = str(exc)
            return
        found = [r for r in found if (r.get("duration") or 0) >= m.duration + 0.3]
        if found:
            m.query_used, m.candidates = q, found
            break
    if not m.candidates:
        m.skipped = f"no stock clip found for {', '.join(m.queries[:3])}"
        return
    ranked = sorted(m.candidates, key=lambda r: -_tag_score(r, m.queries))[:CANDIDATES]
    if vision is None:
        m.choice, m.score = ranked[0], None
        return
    own = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=True)
    try:
        pairs = [(r, _thumb(r.get("thumbnail"), client)) for r in ranked]
    finally:
        if own:
            client.close()
    pairs = [(r, img) for r, img in pairs if img]
    if not pairs:
        m.choice, m.score = ranked[0], None
        return
    try:
        answer = vision(SCORE_SYSTEM, SCORE_PROMPT.format(visual=m.visual, context=m.context, n=len(pairs)),
                        [img for _, img in pairs])
        scores = [float(x) for x in (answer.get("scores") or [])][:len(pairs)]
    except Exception as exc:  # noqa: BLE001 - a failed check falls back to tags rather than failing the edit
        log.warning("B-roll thumbnail scoring failed: %s", exc)
        m.choice, m.score = ranked[0], None
        return
    if len(scores) != len(pairs):
        m.choice, m.score = ranked[0], None
        return
    best = max(range(len(pairs)), key=lambda k: scores[k])
    if scores[best] < MIN_SCORE:
        m.skipped = f"no stock clip clearly showed it (best match scored {scores[best]:.0f}/10)"
        return
    m.choice, m.score = pairs[best][0], scores[best]


def plan(words: list, duration: float, ask_json: Callable[[str, str, list[bytes]], dict], count: Optional[int] = None,
         taken: Optional[list[tuple[float, float]]] = None, wishes: str = "") -> tuple[str, list[Moment]]:
    """Ask the model for B-roll moments and queries, then place them on real transcript times."""
    if not words:
        return "", []
    count = count or max(1, min(8, round(duration / 12)))
    extra = f"The user asked: {wishes.strip()[:400]}\n" if wishes.strip() else ""
    answer = ask_json(PLAN_SYSTEM, PLAN_PROMPT.format(transcript=transcript_lines(words), duration=duration,
                                                      count=count, extra=extra), [])
    moments = []
    for raw in (answer.get("moments") or [])[:count + 2]:
        quote = str(raw.get("quote") or "").strip()
        queries = [re.sub(r"\s+", " ", str(q)).strip()[:60] for q in (raw.get("queries") or []) if str(q).strip()]
        if quote and queries:
            moments.append(Moment(quote=quote[:120], visual=str(raw.get("visual") or quote)[:200], queries=queries[:3]))
    place(moments, words, duration, taken or [])
    kept = [m for m in moments if not m.skipped][:count]
    return str(answer.get("topic") or "")[:200], kept + [m for m in moments if m.skipped]
