"""Portrait footage stays portrait; captions can be edited by hand."""

import pytest

from editor import agent, captions, media, store, worker
from editor.llm import AssistantTurn
from editor.timeline import Timeline, default_output
from test_agent import TRANSCRIPT, U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401


def test_probe_applies_rotation(media_dir):
    info = media.probe(media_dir / "phone_portrait.mp4")
    assert (info["width"], info["height"]) == (270, 480)


def test_default_output_matches_source_shape():
    assert default_output({"width": 1080, "height": 1920}) == {"width": 1080, "height": 1920, "fit": "contain"}
    assert default_output({"width": 1280, "height": 720}) == {"width": 1920, "height": 1080, "fit": "contain"}
    assert default_output({"width": 270, "height": 480}) == {"width": 1080, "height": 1920, "fit": "contain"}
    assert default_output({"width": 500, "height": 500}) == {"width": 1920, "height": 1920, "fit": "contain"}
    assert default_output(None) == {}


@pytest.fixture
def portrait_project(client, media_dir):
    pid = client.post("/v2/projects", params=U, json={"name": "phone"}).json()["id"]
    with open(media_dir / "phone_portrait.mp4", "rb") as f:
        aid = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": False},
                          files={"file": ("phone.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    words = [{"word": w, "start": 0.2 + i * 0.5, "end": 0.6 + i * 0.5}
             for i, w in enumerate("hello from my phone".split())]
    store.update_asset(aid, transcript={"segments": [{"start": 0.2, "end": 2.1, "text": "hello from my phone"}],
                                        "words": words}, transcript_status="done")
    return pid, aid


def test_captions_request_keeps_portrait(client, portrait_project):
    """The reported bug: asking for captions on a portrait video produced a landscape edit."""
    pid, _ = portrait_project
    p = ScriptedProvider([
        AssistantTurn(None, [call("tighten_pacing")]),            # no aspect given, as when only captions are asked
        AssistantTurn(None, [call("set_captions", style="karaoke")]),
        AssistantTurn("Captions added.", []),
    ])
    agent.run(p, pid, "add captions", history=[])
    out = store.get_timeline(pid)["data"]["output"]
    assert (out["width"], out["height"]) == (1080, 1920)

    job = client.post(f"/v2/projects/{pid}/renders", params=U, json={"preview": True}).json()
    worker.run_until_empty()
    done = client.get(f"/v2/jobs/{job['id']}", params=U).json()
    assert done["status"] == "succeeded", done["error"]
    assert done["result"]["height"] > done["result"]["width"]


def test_api_timeline_without_output_matches_source(client, portrait_project):
    pid, aid = portrait_project
    client.put(f"/v2/projects/{pid}/timeline", params=U,
               json={"timeline": {"clips": [{"id": "a", "asset_id": aid, "in": 0, "out": 2}]}})
    out = client.get(f"/v2/projects/{pid}/timeline", params=U).json()["data"]["output"]
    assert (out["width"], out["height"]) == (1080, 1920)


def test_refresh_fixes_dimensions_stored_before_the_fix(portrait_project):
    from editor.tasks import refresh_video_dimensions

    _, aid = portrait_project
    store.update_asset(aid, width=480, height=270)        # what the old probe recorded
    assert refresh_video_dimensions() == 1
    a = store.get_asset(aid)
    assert (a["width"], a["height"]) == (270, 480)
    assert refresh_video_dimensions() == 0


# ---------------------------------------------------------------- caption editing

def _tl(corrections=(), max_words=3):
    return Timeline.model_validate({
        "clips": [{"id": "a", "asset_id": "x", "in": 0, "out": 10}],
        "captions": {"style": "karaoke", "max_words": max_words, "corrections": list(corrections)},
    })


TR = {"x": {"segments": [{"start": 0, "end": 3, "text": "the zoo is grate"}, {"start": 3.5, "end": 5, "text": "um bye"}],
            "words": [{"word": "the", "start": 0, "end": 0.4}, {"word": "zoo", "start": 0.5, "end": 1.0},
                      {"word": "is", "start": 1.1, "end": 1.4}, {"word": "grate", "start": 1.5, "end": 3.0},
                      {"word": "um", "start": 3.5, "end": 3.8}, {"word": "bye", "start": 4.0, "end": 5.0}]}}


def test_caption_lines_map_back_to_source():
    lines = captions.caption_lines(_tl(), TR)
    assert [ln["text"] for ln in lines] == ["the zoo is", "grate", "bye"]
    assert lines[1] == {"start": 1.5, "end": 3.35, "text": "grate", "asset_id": "x", "src_start": 1.5,
                        "src_end": 3.0, "edited": False}


def test_corrections_replace_hide_and_stay_together():
    fixed = _tl([{"asset_id": "x", "start": 0, "end": 3, "text": "The Zoo is GREAT, honestly amazing"},
                 {"asset_id": "x", "start": 3.9, "end": 5.1, "text": ""}])
    lines = captions.caption_lines(fixed, TR)
    # One edited line, kept whole despite max_words=3; "um" stays hidden; "bye" hidden by the empty correction.
    assert [(ln["text"], ln["edited"]) for ln in lines] == [("The Zoo is GREAT, honestly amazing", True)]
    words = captions.output_words(fixed, TR)
    assert words[0].start == 0 and words[-1].end == pytest.approx(3.0)      # spread over the original span
    assert all(a.end <= b.start + 1e-9 for a, b in zip(words, words[1:]))


def test_corrections_survive_a_recut():
    fixed = {"asset_id": "x", "start": 1.5, "end": 3.0, "text": "great"}
    recut = Timeline.model_validate({
        "clips": [{"id": "b", "asset_id": "x", "in": 1.0, "out": 3.2}],
        "captions": {"style": "minimal", "corrections": [fixed]},
    })
    assert [ln["text"] for ln in captions.caption_lines(recut, TR)] == ["is", "great"]


def test_merge_caption_edits_replaces_overlaps():
    old = [{"asset_id": "x", "start": 0, "end": 2, "text": "a"}, {"asset_id": "x", "start": 5, "end": 6, "text": "b"},
           {"asset_id": "y", "start": 0, "end": 2, "text": "other asset"}]
    merged = captions.merge_caption_edits(old, [{"asset_id": "x", "start": 1, "end": 3, "text": "new"}])
    assert [(c["asset_id"], c["text"]) for c in merged] == [("x", "new"), ("x", "b"), ("y", "other asset")]


def test_caption_editing_api(client, project):
    pid, aid = project      # 8 s talk video with TRANSCRIPT
    client.put(f"/v2/projects/{pid}/timeline", params=U, json={"timeline": {
        "clips": [{"id": "a", "asset_id": aid, "in": 0, "out": 8}], "captions": {"style": "boxed"}}})
    got = client.get(f"/v2/projects/{pid}/captions", params=U).json()
    assert got["settings"]["style"] == "boxed" and "corrections" not in got["settings"]
    first = got["lines"][0]
    assert first["text"] == "Hello and welcome."

    r = client.post(f"/v2/projects/{pid}/captions", params=U, json={
        "settings": {"style": "pop", "highlight_color": "#FF3366"},
        "edits": [{"asset_id": first["asset_id"], "start": first["src_start"], "end": first["src_end"],
                   "text": "Hi and welcome!"}]}).json()
    assert r["version"] == 2 and r["preview_job_id"]
    got = client.get(f"/v2/projects/{pid}/captions", params=U).json()
    assert got["settings"]["style"] == "pop" and got["lines"][0]["text"] == "Hi and welcome!"
    assert got["lines"][0]["edited"] is True
    versions = client.get(f"/v2/projects/{pid}", params=U).json()["timeline_versions"]
    assert versions[0]["note"] == "Edited captions (style, 1 line)"

    worker.run_until_empty()
    assert client.get(f"/v2/jobs/{r['preview_job_id']}", params=U).json()["status"] == "succeeded"

    bad = client.post(f"/v2/projects/{pid}/captions", params=U, json={"settings": {"font": "Comic"}})
    assert bad.status_code == 422
    off = client.post(f"/v2/projects/{pid}/captions", params=U, json={"enabled": False, "render_preview": False}).json()
    assert client.get(f"/v2/projects/{pid}/captions", params=U).json() == {
        "version": off["version"], "settings": None, "lines": []}


def test_agent_edits_caption_text(project):
    pid, aid = project
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", snap_to_words=False, clips=[{"asset_id": aid, "in": 0, "out": 8}]),
                             call("set_captions", style="minimal")]),
        AssistantTurn(None, [call("get_captions")]),
        AssistantTurn(None, [call("edit_captions", edits=[{"asset_id": aid, "start": 4.0, "end": 7.9,
                                                            "text": "Today we build a video editor!"}])]),
        AssistantTurn("Fixed the caption.", []),
    ])
    agent.run(p, pid, "fix the second caption", history=[])
    lines = tool_results(p)[2]["lines"]
    assert lines[0]["text"] == "Hello and welcome."
    caps = store.get_timeline(pid)["data"]["captions"]
    assert caps["corrections"] == [{"asset_id": aid, "start": 4.0, "end": 7.9, "text": "Today we build a video editor!"}]
