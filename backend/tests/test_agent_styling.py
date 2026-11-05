"""Phase 3 agent tools, driven by a scripted model, through to a real render."""

import pytest

from editor import agent, store, worker
from editor.llm import AssistantTurn
from test_agent import TRANSCRIPT, U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401


@pytest.fixture
def styled_project(client, project, media_dir):
    pid, vid = project
    ids = {"video": vid}
    for key, name in (("logo", "card.png"), ("music", "tone.mp3")):
        with open(media_dir / name, "rb") as f:
            ids[key] = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": False},
                                   files={"file": (name, f)}).json()["asset"]["id"]
    worker.run_until_empty()
    return pid, ids


def test_styling_tools_build_a_full_timeline(client, styled_project):
    pid, ids = styled_project
    v = ids["video"]
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_captions", style="pop")]),          # no timeline yet -> error
        AssistantTurn(None, [call("set_timeline", aspect="9:16", clips=[
            {"asset_id": v, "in": 0, "out": 2.0}, {"asset_id": v, "in": 4.0, "out": 7.9}])]),
        AssistantTurn(None, [call("set_captions", style="karaoke", highlight_color="#00FF88"),
                             call("set_transitions", type="dissolve", duration=0.8),
                             call("set_grade", preset="warm", contrast=1.1),
                             call("add_text", text="Build day", start=0, end=1.5, animation="pop"),
                             call("add_overlay", asset_id=ids["logo"], start=0, end=5, scale=0.15),
                             call("add_audio", asset_id=ids["music"], loop=True, duck=True, volume=0.3, fade_out=1),
                             call("add_audio", asset_id=ids["music"], start=1.6, source_out=0.4, volume=0.9)]),
        AssistantTurn(None, [call("remove_layer", layer_id="audio2"), call("remove_layer", layer_id="nope"),
                             call("set_grade", lut_asset_id=ids["logo"])]),               # an image is not a LUT
        # Re-cut: every layer must survive the new clip list.
        AssistantTurn(None, [call("set_timeline", clips=[{"asset_id": v, "in": 4.6, "out": 7.9}])]),
        AssistantTurn("Done.", []),
    ])
    res = agent.run(p, pid, "style it", history=[])
    oks = [t["ok"] for t in res.trace]
    assert oks == [False, True, True, True, True, True, True, True, True, True, False, False, True]

    tl = store.get_timeline(pid)["data"]
    assert tl["captions"] == {"style": "karaoke", "position": "bottom", "size": 1.0, "color": "#FFFFFF",
                              "highlight_color": "#00FF88", "uppercase": False, "corrections": []}
    assert tl["grade"] == {"preset": "warm", "contrast": 1.1}
    assert [t["id"] for t in tl["text"]] == ["text1"] and tl["overlays"][0]["scale"] == 0.15
    assert [a["id"] for a in tl["audio"]] == ["audio1"] and tl["audio"][0]["duck"] is True
    assert len(tl["clips"]) == 1 and "transition_in" not in tl["clips"][0]

    # the transition was clamped to fit the 2 s first clip (0.45 x 2.0 = 0.9 > 0.8, so 0.8 kept)
    v2 = store.get_timeline(pid, 1)
    assert v2["version"] == 1

    # render it for real
    job = client.post(f"/v2/projects/{pid}/renders", params=U, json={"preview": True}).json()
    worker.run_until_empty()
    done = client.get(f"/v2/jobs/{job['id']}", params=U).json()
    assert done["status"] == "succeeded", done["error"]


def test_transitions_shrink_for_short_clips(styled_project):
    pid, ids = styled_project
    v = ids["video"]
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", snap_to_words=False, clips=[
            {"asset_id": v, "in": 0, "out": 0.15}, {"asset_id": v, "in": 1, "out": 1.6},
            {"asset_id": v, "in": 4, "out": 7}])]),
        AssistantTurn(None, [call("set_transitions", type="wipeleft", duration=1.0)]),
        AssistantTurn("ok", []),
    ])
    agent.run(p, pid, "transitions please", history=[])
    result = tool_results(p)[1]                                         # [0] is set_timeline
    assert result["ok"] and "c2" in result["note"]                      # 0.15 s clip too short next to c2
    clips = store.get_timeline(pid)["data"]["clips"]
    assert "transition_in" not in clips[1]
    assert clips[2]["transition_in"] == {"type": "wipeleft", "duration": 0.27}   # 0.45 x 0.6 s neighbour


def test_summary_reports_output_times_and_warnings(styled_project):
    pid, ids = styled_project
    v = ids["video"]
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", snap_to_words=False, clips=[
            {"asset_id": v, "in": 0, "out": 3}, {"asset_id": v, "in": 4, "out": 7}])]),
        AssistantTurn(None, [call("set_transitions", type="fade", duration=0.5),
                             call("add_text", text="late", start=20, end=21)]),
        AssistantTurn(None, [call("get_timeline")]),
        AssistantTurn("ok", []),
    ])
    agent.run(p, pid, "x", history=[])
    summary = tool_results(p)[-1]                                       # the get_timeline result
    assert [(c["output_start"], c["output_end"]) for c in summary["clips"]] == [(0, 3), (2.5, 5.5)]
    assert summary["duration_seconds"] == 5.5
    assert "skipped" in summary["warnings"][0]
