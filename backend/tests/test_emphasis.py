"""Phase 6, step 2: keyword pops, emphasis zooms, sound effects, audio polish."""

import re
import subprocess

import numpy as np
import pytest

from editor import agent, captions, diff, media, renderer, sfx, store, worker
from editor.config import require_ffmpeg
from editor.llm import AssistantTurn
from editor.timeline import Timeline
from test_agent import TRANSCRIPT, U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401
from test_render_styling import frame

BASE = {"clips": [{"id": "a", "asset_id": "x", "in": 0, "out": 8}]}


def test_schema_rules():
    Timeline.model_validate({**BASE, "audio": [{"id": "s", "sfx": "pop", "start": 1}],
                             "zooms": [{"id": "z", "start": 1, "end": 2}],
                             "text": [{"id": "k", "text": "WOW", "start": 1, "end": 2, "style": "keyword"}],
                             "audio_polish": {"normalize": True, "denoise": "light"}})
    for bad in ({"audio": [{"id": "s", "start": 1}]},                                   # neither asset nor sfx
                {"audio": [{"id": "s", "sfx": "pop", "asset_id": "m", "start": 1}]},    # both
                {"audio": [{"id": "s", "sfx": "airhorn", "start": 1}]},
                {"zooms": [{"id": "z", "start": 1, "end": 1.1}]},
                {"zooms": [{"id": "z", "start": 1, "end": 2, "scale": 3}]},
                {"audio_polish": {"denoise": "max"}}):
        with pytest.raises(Exception):
            Timeline.model_validate({**BASE, **bad})


def test_sound_library_is_generated_and_level_matched():
    for sid, sound in sfx.SOUNDS.items():
        p = sfx.path(sid)
        info = media.probe(p)
        assert info["kind"] == "audio" and info["duration"] == pytest.approx(sound.duration, abs=0.03)
        peak = float(subprocess.run([require_ffmpeg(), "-hide_banner", "-nostats", "-i", str(p), "-af", "volumedetect",
                                     "-f", "null", "-"], capture_output=True, text=True).stderr
                     .split("max_volume:")[1].split("dB")[0])
        assert peak == pytest.approx(-3.0, abs=0.3), sid


def test_keyword_pops_hide_captions_meanwhile():
    t = Timeline.model_validate({**BASE, "captions": {"style": "karaoke"},
                                 "text": [{"id": "k", "text": "editor", "start": 6.0, "end": 7.0, "style": "keyword"}]})
    ass = captions.build_ass(t, {"x": TRANSCRIPT})
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]
    caption_times = [tuple(ln.split(",")[1:3]) for ln in events if ",Caption," in ln]

    def secs(ts):
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    assert caption_times and not any(secs(a) < 7.0 and secs(b) > 6.0 for a, b in caption_times)
    keyword = next(ln for ln in events if ",Text0," in ln)
    assert keyword.endswith("EDITOR")                         # keywords are upper case
    assert "\\pos(960,691)" in keyword                        # 64% down a 1080-high frame, clear of a face


def _loudness(path) -> float:
    out = subprocess.run([require_ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null",
                          "-"], capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", out)[-1])


def test_render_zoom_sound_and_polish(media_dir, tmp_path):
    assets = {"v": {"id": "v", "project_id": "P", "status": "ready", **media.probe(media_dir / "landscape.mp4")}}
    paths = {"v": media_dir / "landscape.mp4"}
    base = {"output": {"width": 320, "height": 180}, "clips": [{"id": "a", "asset_id": "v", "in": 0, "out": 6, "volume": 0.2}]}
    styled = {**base, "zooms": [{"id": "z", "start": 1.0, "end": 2.5, "scale": 1.4}],
              "audio": [{"id": "d", "sfx": "ding", "start": 4.0, "volume": 1.0}],
              "audio_polish": {"normalize": True, "voice": True, "denoise": "light"}}
    outs = {}
    for name, t in (("plain", base), ("styled", styled)):
        outs[name] = tmp_path / f"{name}.mp4"
        renderer.render(Timeline.model_validate(t), assets, paths, outs[name])
    # zoom: different at 1.8 s, identical outside the zoom window
    assert np.abs(frame(outs["plain"], 1.8, 320, 180) - frame(outs["styled"], 1.8, 320, 180)).mean() > 8
    assert np.abs(frame(outs["plain"], 3.5, 320, 180) - frame(outs["styled"], 3.5, 320, 180)).mean() < 3
    # loudness: quiet source raised to about -14 LUFS
    assert _loudness(outs["plain"]) < -20
    assert _loudness(outs["styled"]) == pytest.approx(-14, abs=1.5)
    # the ding is there (its 1318 Hz partial, separate from the 440 Hz tone)
    band = lambda f, a: float(subprocess.run(  # noqa: E731
        [require_ffmpeg(), "-hide_banner", "-nostats", "-ss", str(a), "-t", "0.5", "-i", str(f), "-af",
         "bandpass=f=1318:width_type=h:w=60,volumedetect", "-f", "null", "-"], capture_output=True,
        text=True).stderr.split("mean_volume:")[1].split("dB")[0])
    assert band(outs["styled"], 4.05) > band(outs["plain"], 4.05) + 15


def test_agent_emphasis_tools(client, project):
    pid, aid = project
    client.put(f"/v2/projects/{pid}/timeline", params=U,
               json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}})   # version 1
    p = ScriptedProvider([
        AssistantTurn(None, [call("emphasize", items=[{"phrase": "video editor", "text": "Video editor"},
                                                      {"phrase": "welcome", "zoom": False, "sound": "ding"},
                                                      {"phrase": "not said anywhere"}]),
                             call("add_broll", asset_id=aid, start=2.5, duration=1.0, source_in=0)]),
        AssistantTurn(None, [call("auto_sound_effects", on=["broll", "keywords"]),
                             call("set_audio_polish", normalize=True, voice=True),
                             call("add_sound_effect", sound="riser", start=3.5)]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "spice it up", history=[])
    results = tool_results(p)
    emph = results[0]
    assert [x["phrase"] for x in emph["placed"]] == ["video editor", "welcome"] and emph["not_found"] == ["not said anywhere"]
    tl = store.get_timeline(pid)["data"]
    kws = [t for t in tl["text"] if t["style"] == "keyword"]
    assert [k["text"] for k in kws] == ["Video editor", "welcome"]
    assert kws[0]["start"] == pytest.approx(5.67, abs=0.02)                     # on "video" (5.7 s)
    assert len(tl["zooms"]) == 1 and tl["zooms"][0]["scale"] == 1.15
    sounds = sorted((a["sfx"], a["start"]) for a in tl["audio"])
    # pop + ding from emphasize, whoosh into the cutaway, the riser; auto pops were NOT doubled on the keywords
    assert [s for s, _ in sounds] == ["ding", "pop", "riser", "whoosh"]
    assert results[2]["added"] == {"broll": 1}
    assert tl["audio_polish"] == {"normalize": True, "denoise": "off", "voice": True}
    assert len({x["id"] for x in tl["text"] + tl["zooms"] + tl["audio"]}) == len(tl["text"]) + len(tl["zooms"]) + len(tl["audio"])

    job = client.post(f"/v2/projects/{pid}/renders", params=U, json={"preview": True}).json()
    worker.run_until_empty()
    assert client.get(f"/v2/jobs/{job['id']}", params=U).json()["status"] == "succeeded"

    changes = client.get(f"/v2/projects/{pid}/timeline/changes", params=U).json()["changes"]
    assert "Emphasis zooms: 0 → 1" in changes and "Audio polish: loudness for social, clearer voice" in changes
    assert "Added keyword “Video editor”" in changes and "Added sound effect “riser”" in changes


def test_sfx_api(client):
    sounds = client.get("/v2/sfx").json()["sounds"]
    assert [s["id"] for s in sounds] == list(sfx.SOUNDS)
    wav = client.get("/v2/sfx/pop.wav")
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
    assert client.get("/v2/sfx/airhorn.wav").status_code == 404



def test_emphasis_is_a_beat_and_skips_zooms_under_broll(project):
    pid, aid = project
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", snap_to_words=False, clips=[{"asset_id": aid, "in": 0, "out": 8}]),
                             call("add_broll", asset_id=aid, start=0.0, duration=2.0, source_in=0)]),
        AssistantTurn(None, [call("emphasize", items=[{"phrase": "hello and welcome"},
                                                      {"phrase": "today we build a video editor"}]),
                             call("auto_sound_effects", on=["broll", "keywords"])]),
        AssistantTurn("ok", []),
    ])
    agent.run(p, pid, "x", history=[])
    emph = tool_results(p)[2]
    assert any("B-roll covers" in n for n in emph["notes"])
    tl = store.get_timeline(pid)["data"]
    assert len(tl["zooms"]) == 1 and tl["zooms"][0]["end"] - tl["zooms"][0]["start"] <= 1.5 + 0.35 + 0.06 + 1e-6
    assert all(x["end"] - x["start"] <= 1.6 + 0.03 + 1e-6 for x in tl["text"] if x["style"] == "keyword")
    starts = sorted(a["start"] for a in tl["audio"] if a.get("sfx"))
    assert all(b - a >= 0.0 for a, b in zip(starts, starts[1:]))


def test_tidy_reply_flattens_markdown():
    messy = """I made the edit.

What I changed:
- Set the output to **vertical** (9:16)
- Added two cutaways
1. Polished the audio

New length: 0:19."""
    assert agent.tidy_reply(messy) == ("I made the edit. Set the output to vertical (9:16). Added two cutaways. "
                                       "Polished the audio. New length: 0:19.")
    assert agent.tidy_reply("Plain sentence.") == "Plain sentence."



def test_broll_placed_after_emphasis_removes_hidden_zooms(project):
    pid, aid = project
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", snap_to_words=False, clips=[{"asset_id": aid, "in": 0, "out": 8}]),
                             call("emphasize", items=[{"phrase": "hello and welcome"}, {"phrase": "video editor"}])]),
        AssistantTurn(None, [call("add_broll", asset_id=aid, start=0.0, duration=2.0, source_in=0)]),
        AssistantTurn("ok", []),
    ])
    agent.run(p, pid, "x", history=[])
    placed = tool_results(p)[2]
    assert "Removed 1 emphasis zoom" in placed["note"]
    zooms = store.get_timeline(pid)["data"]["zooms"]
    assert len(zooms) == 1 and zooms[0]["start"] > 5                     # the one on "video editor" stays
