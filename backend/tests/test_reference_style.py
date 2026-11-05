"""Phase 4: reference analysis, colour matching, pacing, punch-ins and the agent tools that use them."""

import subprocess

import numpy as np
import pytest

from editor import agent, analysis, colormatch, edits, media, renderer, store, style, worker
from editor.config import require_ffmpeg
from editor.llm import AssistantTurn
from editor.timeline import Timeline
from test_agent import TRANSCRIPT, U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401
from test_render_styling import frame


def ff(*args):
    subprocess.run([require_ffmpeg(), "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="module")
def reference_video(tmp_path_factory):
    """A 9:16 'reference': three 2 s shots in clearly different warm colours, plus a 120 BPM click track."""
    d = tmp_path_factory.mktemp("ref")
    out = d / "reference.mp4"
    ff("-f", "lavfi", "-i", "color=c=0xFF8C00:s=270x480:d=2:r=30",
       "-f", "lavfi", "-i", "color=c=0x5A1000:s=270x480:d=2:r=30",
       "-f", "lavfi", "-i", "color=c=0xFFD27A:s=270x480:d=2:r=30",
       "-f", "lavfi", "-i", "aevalsrc='if(lt(mod(t,0.5),0.03),0.8*sin(2*PI*1500*t),0)':s=44100:d=6",
       "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
       "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out))
    return out


def test_cut_detection_and_pacing(reference_video):
    cuts = analysis.detect_cuts(reference_video, 6)
    assert len(cuts) == 2 and cuts[0] == pytest.approx(2.0, abs=0.1) and cuts[1] == pytest.approx(4.0, abs=0.1)
    stats = analysis.pacing_stats(cuts, 6.0)
    assert stats["cuts"] == 2 and stats["cuts_per_minute"] == 20.0
    assert stats["median_shot_seconds"] == pytest.approx(2.0, abs=0.1)


def test_tempo_of_click_track(reference_video):
    tempo = analysis.estimate_tempo(reference_video, 6)
    assert tempo["bpm"] == pytest.approx(120, abs=4)


def test_speech_has_no_confident_tempo(talk_video):
    # a steady tone with one pause: no pulse
    assert analysis.estimate_tempo(talk_video, 8)["bpm"] is None


def test_colour_stats_tell_warm_from_cool():
    warm = np.full((2, 8, 8, 3), (220, 140, 60), np.uint8)
    cool = np.full((2, 8, 8, 3), (60, 120, 220), np.uint8)
    assert analysis.color_stats(warm)["warmth"] > 0.5 > -0.5 > analysis.color_stats(cool)["warmth"]
    assert analysis.color_stats(np.full((1, 4, 4, 3), 128, np.uint8))["saturation"] == 0


def test_lab_round_trip():
    rgb = np.random.default_rng(0).random((500, 3))
    assert np.abs(colormatch.lab_to_srgb(colormatch.srgb_to_lab(rgb)) - rgb).max() < 1e-6


def test_colour_transfer_moves_footage_toward_reference(tmp_path):
    rng = np.random.default_rng(1)
    footage = np.clip(rng.normal((0.3, 0.4, 0.7), 0.08, (4000, 3)), 0, 1)       # bluish
    reference = np.clip(rng.normal((0.8, 0.5, 0.25), 0.12, (4000, 3)), 0, 1)    # orange
    src, ref = colormatch.srgb_to_lab(footage), colormatch.srgb_to_lab(reference)
    lut = colormatch.transfer_lut(src.mean(0), src.std(0), ref.mean(0), ref.std(0), strength=1.0)
    graded = colormatch.srgb_to_lab(colormatch.apply_lut(footage, lut))
    before = np.abs(src.mean(0) - ref.mean(0)).sum()
    after = np.abs(graded.mean(0) - ref.mean(0)).sum()
    assert after < before * 0.25

    half = colormatch.transfer_lut(src.mean(0), src.std(0), ref.mean(0), ref.std(0), strength=0.5)
    mid = np.abs(colormatch.srgb_to_lab(colormatch.apply_lut(footage, half)).mean(0) - ref.mean(0)).sum()
    assert after < mid < before                                                  # strength blends

    cube = colormatch.write_cube(tmp_path / "m.cube", lut)
    assert media.inspect_lut(cube)["kind"] == "lut"                               # valid .cube


def test_profile_with_vision(reference_video):
    seen = {}

    def vision(images):
        seen["n"] = len(images)
        assert all(img[:2] == b"\xff\xd8" for img in images)                     # JPEGs
        return {"captions": {"present": True, "position": "middle", "uppercase": True, "words_per_line": 2,
                             "highlight_color": None, "boxed": False, "bold": True},
                "transitions": "hard_cuts", "punch_in_zooms": True, "look": "warm orange tones", "energy": "high"}

    info = media.probe(reference_video)
    profile = style.build_profile(reference_video, info, None, vision)
    assert seen["n"] == style.VISION_FRAMES
    assert profile["format"]["aspect"] == "9:16" and profile["pacing"]["cuts"] == 2
    assert profile["color"]["warmth"] > 0.3
    s = profile["suggestions"]
    assert s["captions"] == {"style": "pop", "position": "middle", "uppercase": True, "max_words": 2}
    assert s["transition"] is None and s["punch_ins"] is True
    assert s["target_shot_seconds"] is None            # only 2 cuts: too few to judge a rhythm
    assert "9:16" in style.summarize(profile) and "warm orange" in style.summarize(profile)


def test_profile_survives_vision_failure(reference_video):
    def broken(_):
        raise RuntimeError("model down")
    profile = style.build_profile(reference_video, media.probe(reference_video), None, broken)
    assert profile["visual"] is None and "model down" in profile["visual_error"]


@pytest.mark.parametrize("cap,expected", [
    ({"present": False}, None),
    ({"present": True, "boxed": True}, "boxed"),
    ({"present": True, "highlight_color": "#ffd23f", "words_per_line": 3}, "karaoke"),
    ({"present": True, "words_per_line": 1}, "pop"),
    ({"present": True, "words_per_line": 6, "bold": False}, "minimal"),
])
def test_caption_mapping(cap, expected):
    got = style.caption_style_for({"captions": cap})
    assert (got or {}).get("style") == expected


def test_split_for_pacing():
    words = [{"word": "w", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(40)]
    segs = [{"start": 0, "end": 4.9}, {"start": 5, "end": 19.9}]
    clips = [{"id": "c1", "asset_id": "a", "in": 0, "out": 19.9, "transition_in": {"type": "fade", "duration": 0.5}},
             {"id": "short", "asset_id": "a", "in": 0, "out": 2}, {"id": "notext", "asset_id": "b", "in": 0, "out": 30}]
    new, added = edits.split_for_pacing(clips, {"a": words}, {"a": segs}, target=3.0, punch_in=1.15)
    pieces = [c for c in new if c["id"].startswith("c1")]
    assert added == len(pieces) - 1 >= 5
    assert all(2.0 <= c["out"] - c["in"] <= 4.5 for c in pieces)
    assert all(a["out"] == b["in"] for a, b in zip(pieces, pieces[1:]))                 # contiguous, nothing lost
    assert [c.get("zoom") for c in pieces[:4]] == [None, 1.15, None, 1.15]
    assert "transition_in" in pieces[0] and all("transition_in" not in c for c in pieces[1:])
    assert [c["id"] for c in new if not c["id"].startswith("c1")] == ["short", "notext"]  # untouched


def test_punch_in_renders_closer(media_dir, tmp_path):
    info = media.probe(media_dir / "landscape.mp4")
    assets = {"v": {"id": "v", "project_id": "P", "status": "ready", **info}}
    paths = {"v": media_dir / "landscape.mp4"}
    outs = {}
    for z in (None, 1.5):
        clip = {"id": "a", "asset_id": "v", "in": 1, "out": 2, **({"zoom": z} if z else {})}
        t = Timeline.model_validate({"output": {"width": 320, "height": 180}, "clips": [clip]})
        outs[z] = tmp_path / f"z{z}.mp4"
        renderer.render(t, assets, paths, outs[z])
    plain, zoomed = frame(outs[None], 0.5, 320, 180), frame(outs[1.5], 0.5, 320, 180)
    assert np.abs(plain - zoomed).mean() > 10                       # different framing
    # ...and it is the centre of the plain frame, enlarged 1.5x (nearest-neighbour approximation)
    ys = ((180 - 180 / 1.5) / 2 + np.arange(180) / 1.5).astype(int)   # crop window starts at y=30
    xs = ((320 - 320 / 1.5) / 2 + np.arange(320) / 1.5).astype(int)   # ...and x=53
    assert np.abs(plain[ys][:, xs] - zoomed).mean() < np.abs(plain - zoomed).mean() / 2


# ---------------------------------------------------------------- end to end with the agent

@pytest.fixture
def reference_project(client, project, reference_video):
    pid, vid = project
    with open(reference_video, "rb") as f:
        r = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "role": "reference", "transcribe": False},
                        files={"file": ("ref.mp4", f)}).json()
    worker.run_until_empty()
    return pid, vid, r["asset"]["id"]


def test_reference_upload_and_profile_in_api(client, reference_project, media_dir):
    pid, _, rid = reference_project
    ref = next(a for a in client.get(f"/v2/projects/{pid}", params=U).json()["assets"] if a["id"] == rid)
    assert ref["role"] == "reference" and ref["analysis_status"] == "done"
    assert ref["style"]["pacing"]["cuts"] == 2 and "cut_times" not in ref["style"]["pacing"]
    assert ref["style"]["visual"] is None                     # no API key in tests: measurements only
    with open(media_dir / "card.png", "rb") as f:
        bad = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "role": "reference"},
                          files={"file": ("x.png", f)})
    assert bad.status_code == 400


def test_agent_matches_reference_style(client, reference_project):
    pid, vid, rid = reference_project
    p = ScriptedProvider([
        AssistantTurn(None, [call("list_assets"), call("get_reference_style", asset_id=rid)]),
        AssistantTurn(None, [call("set_timeline", clips=[{"asset_id": rid, "in": 0, "out": 2}]),      # not footage
                             call("set_timeline", snap_to_words=False, clips=[{"asset_id": vid, "in": 0, "out": 8}]),
                             call("set_output_format", aspect="9:16"),
                             call("apply_pacing", target_shot_seconds=2.0, punch_in=1.12),
                             call("match_reference_color", reference_asset_id=rid, strength=0.8),
                             call("match_reference_color", reference_asset_id=vid)]),                   # footage
        AssistantTurn("Matched the reference.", []),
    ])
    res = agent.run(p, pid, "make it like the reference", history=[])
    results = tool_results(p)
    assets = {a["asset_id"]: a for a in results[0]["assets"]}
    assert assets[rid]["role"] == "reference" and assets[rid]["style_analysis"] == "done"
    assert results[1]["pacing"]["cuts"] == 2
    assert "style reference" in results[2]["errors"][0]
    assert results[5]["cuts_added"] >= 1
    assert results[6]["ok"] and results[6]["reference_look"]["warmth"] > 0.3
    assert "not a style reference" in results[7]["error"]
    assert [t["ok"] for t in res.trace] == [True, True, False, True, True, True, True, False]

    tl = store.get_timeline(pid)["data"]
    lut_id = tl["grade"]["lut_asset_id"]
    lut = store.get_asset(lut_id)
    assert lut["role"] == "generated" and lut["kind"] == "lut" and lut["filename"].startswith("Colour match")
    assert any(c.get("zoom") == 1.12 for c in tl["clips"])
    assert (tl["output"]["width"], tl["output"]["height"]) == (1080, 1920)

    job = client.post(f"/v2/projects/{pid}/renders", params=U, json={"preview": True}).json()
    worker.run_until_empty()
    done = client.get(f"/v2/jobs/{job['id']}", params=U).json()
    assert done["status"] == "succeeded", done["error"]


def test_apply_reference_style_in_one_step(client, reference_project):
    pid, vid, rid = reference_project
    # Give the reference profile the suggestions a vision model would add, and a rhythm to match.
    profile = store.get_analysis(rid)
    profile["suggestions"].update(target_shot_seconds=2.0, punch_ins=True,
                                  captions={"style": "pop", "position": "middle", "uppercase": True},
                                  transition=None)
    store.update_asset(rid, analysis=profile)
    p = ScriptedProvider([
        AssistantTurn(None, [call("apply_reference_style", reference_asset_id=rid)]),     # no timeline yet
        AssistantTurn(None, [call("apply_reference_style", reference_asset_id=rid, parts=["pacing", "sparkles"])]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "make it like the reference", history=[])
    first, second = tool_results(p)
    assert first["ok"] and set(first["applied"]) == {"format", "pacing", "captions", "transitions", "color"}
    assert first["skipped"] == {}
    assert "Unknown parts" in second["error"]

    tl = store.get_timeline(pid)["data"]
    assert (tl["output"]["width"], tl["output"]["height"], tl["output"]["fit"]) == (1080, 1920, "cover")
    assert tl["captions"]["style"] == "pop" and tl["captions"]["position"] == "middle"
    assert any(c.get("zoom") == 1.12 for c in tl["clips"]) and len(tl["clips"]) >= 3
    assert all("transition_in" not in c for c in tl["clips"])
    assert store.get_asset(tl["grade"]["lut_asset_id"])["kind"] == "lut"

    # Running it again finds the pacing already matched instead of splitting further.
    p2 = ScriptedProvider([AssistantTurn(None, [call("apply_reference_style", reference_asset_id=rid,
                                                      parts=["pacing"])]), AssistantTurn("ok", [])])
    agent.run(p2, pid, "again", history=[])
    assert "already paced" in tool_results(p2)[0]["skipped"]["pacing"]
