"""B-roll planned from the transcript: understand it, write stock keywords, check thumbnails, place clear matches."""

from dataclasses import dataclass

import pytest

from editor import agent, broll, brollplan, store
from editor.llm import AssistantTurn
from test_agent import U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401


@dataclass
class W:
    text: str
    start: float
    end: float
    ends_phrase: bool = False


def words_of(text: str, start: float = 0.0, step: float = 0.4) -> list[W]:
    out = []
    for i, t in enumerate(text.split()):
        out.append(W(t, start + i * step, start + i * step + step * 0.9, t.endswith(".")))
    return out


WORDS = words_of("Hi there. Today I visit the elephants at the zoo. They have really long trunks. "
                 "Then we eat ice cream by the lake and watch the sunset.", step=0.8)


def m(quote, queries=("x",), visual="v"):
    return brollplan.Moment(quote=quote, visual=visual, queries=list(queries))


def test_locate_exact_and_fuzzy():
    assert brollplan.locate(WORDS, "the elephants at") == (5, 7)
    assert brollplan.locate(WORDS, "the Elephants, at") == (5, 7)
    assert brollplan.locate(WORDS, "the elephant at the zoo") == (5, 9)        # one word off: still found
    assert brollplan.locate(WORDS, "flying purple dragons") is None


def test_place_applies_rules():
    duration = WORDS[-1].end
    ms = [m("Hi there."), m("visit the elephants"), m("the elephants at the zoo"), m("really long trunks"),
          m("dragons fly"), m("watch the sunset")]
    brollplan.place(ms, WORDS, duration, taken=[(9.0, 10.5)])
    reasons = [x.skipped for x in ms]
    assert "opening seconds" in reasons[0]
    assert reasons[1] is None and 1.5 <= ms[1].duration <= 4.0 and ms[1].start == pytest.approx(WORDS[4].start - 0.15)
    assert "overlaps" in reasons[2]                       # same words as the one before
    assert "overlaps" in reasons[3]                       # 'really long trunks' sits on existing B-roll at 9-10.5 s
    assert "weren't found" in reasons[4]
    assert reasons[5] is None and ms[5].end <= duration
    assert "elephants" in ms[1].context


def test_place_respects_a_third_of_the_runtime():
    duration = WORDS[-1].end   # ~15.2 s -> about 5 s of B-roll allowed
    ms = [m("visit the elephants"), m("really long trunks"), m("eat ice cream"), m("watch the sunset")]
    brollplan.place(ms, WORDS, duration, taken=[])
    used = sum(x.duration for x in ms if not x.skipped)
    assert used <= duration / 3 + 0.01
    assert any(x.skipped and "a third" in x.skipped for x in ms)


def _result(i, tags, duration=10):
    return {"provider": "pixabay", "id": str(i), "duration": duration, "width": 1920, "height": 1080,
            "thumbnail": f"https://cdn.example/{i}.jpg", "tags": tags, "credit": "someone",
            "download_url": f"https://cdn.example/{i}.mp4", "page_url": "https://pixabay.com/x"}


def test_choose_uses_queries_in_order_and_vision_scores(monkeypatch):
    seen = []

    def search(q, orientation=None, per_page=8):
        seen.append(q)
        return [] if q == "african elephant trunk" else [_result(1, "zoo, fence"), _result(2, "elephant, trunk, herd"),
                                                         _result(3, "elephant", duration=1)]

    monkeypatch.setattr(brollplan, "_thumb", lambda url, client: b"\xff\xd8\xff" + url.encode())
    shown = {}

    def vision(system, prompt, images):
        shown["n"] = len(images)
        return {"scores": [9, 2]}          # image order follows tag ranking: elephant clip first

    x = m("visit the elephants", ["african elephant trunk", "elephant"], "an elephant using its trunk")
    x.start, x.end = 2.0, 4.0
    brollplan.choose(x, "landscape", vision, search=search)
    assert seen == ["african elephant trunk", "elephant"] and x.query_used == "elephant"
    assert shown["n"] == 2                  # the 1-second clip is too short for a 2 s cutaway
    assert x.choice["id"] == "2" and x.score == 9

    y = m("visit the elephants", ["elephant"]); y.start, y.end = 2.0, 4.0
    brollplan.choose(y, "landscape", lambda *a: {"scores": [4, 3]}, search=search)
    assert y.choice is None and "scored 4/10" in y.skipped

    z = m("visit the elephants", ["elephant"]); z.start, z.end = 2.0, 4.0
    brollplan.choose(z, "landscape", None, search=search)             # no vision: best tag match
    assert z.choice["id"] == "2"


class PlanningProvider(ScriptedProvider):
    def __init__(self, turns, plan):
        super().__init__(turns)
        self.plan, self.prompts = plan, []

    def vision_json(self, system, prompt, images):
        self.prompts.append(prompt)
        if system == brollplan.PLAN_SYSTEM:
            return self.plan
        return {"scores": [8] * len(images)}


def test_agent_plan_broll(client, project, monkeypatch, media_dir):
    pid, aid = project
    client.put(f"/v2/projects/{pid}/timeline", params=U,
               json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}})
    monkeypatch.setenv("PIXABAY_API_KEY", "px")
    searched = []
    found = _result(77, "video, editing, computer, software")
    monkeypatch.setattr(broll, "search_stock", lambda q, o=None, **k: searched.append((q, o)) or [found])
    monkeypatch.setattr(broll, "get_stock", lambda vid, provider="pixabay", **k: found)
    monkeypatch.setattr(broll, "download",
                        lambda url, dest, **k: dest.write_bytes((media_dir / "landscape.mp4").read_bytes()) or dest)
    monkeypatch.setattr(brollplan, "_thumb", lambda url, client: b"\xff\xd8\xff")
    plan = {"topic": "Building a video editor", "moments": [
        {"quote": "Hello and welcome", "visual": "a wave", "queries": ["waving hand"]},
        {"quote": "build a video editor", "visual": "someone editing video on a laptop",
         "queries": ["video editing laptop", "editing timeline"]},
    ]}
    p = PlanningProvider([AssistantTurn(None, [call("plan_broll", wishes="tech stuff")]), AssistantTurn("Done.", [])], plan)
    agent.run(p, pid, "add some b-roll, tech stuff", history=[])
    r = tool_results(p)[0]
    assert r["topic"] == "Building a video editor"
    assert [x["words"] for x in r["placed"]] == ["build a video editor"]
    assert "opening seconds" in r["skipped"][0]["skipped"]
    assert searched == [("video editing laptop", "landscape")]
    assert "today we build a video editor" in p.prompts[0].lower() and "tech stuff" in p.prompts[0]
    assert "someone editing video on a laptop" in p.prompts[1]
    tl = store.get_timeline(pid)["data"]
    (b,) = tl["broll"]
    assert b["start"] == r["placed"][0]["start"] and 1.5 <= b["duration"] <= 4.0
    asset = store.get_asset(b["asset_id"])
    assert asset["source"] == "pixabay" and asset["role"] == "broll"


def test_plan_broll_needs_stock(client, project, monkeypatch):
    pid, aid = project
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    client.put(f"/v2/projects/{pid}/timeline", params=U,
               json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}})
    p = PlanningProvider([AssistantTurn(None, [call("plan_broll")]), AssistantTurn("Done.", [])], {})
    agent.run(p, pid, "b-roll please", history=[])
    assert "PIXABAY_API_KEY" in tool_results(p)[0]["error"]
