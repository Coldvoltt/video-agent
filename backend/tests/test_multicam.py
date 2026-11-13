"""Multicam: sync up to three angles of one take, trim to the same length, pick the best audio, cut between them."""

import subprocess

import numpy as np
import pytest

from editor import agent, config, multicam, renderer, store, worker
from editor.llm import AssistantTurn
from editor.timeline import Timeline
from test_agent import U, ScriptedProvider, call, client, tool_results  # noqa: F401
from test_render_styling import frame


def _ff(*args):
    subprocess.run([config.require_ffmpeg(), "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="session")
def cams(tmp_path_factory):
    """One 12 s take heard by three cameras that started at different times.

    red:   starts at take 0.0, 10 s, clean sound
    blue:  starts at take 1.5,  9 s, noisy sound
    green: starts at take 0.8,  9 s, clipped (distorted) sound
    plus a separate clean recorder that started at take 0.3.
    """
    d = tmp_path_factory.mktemp("cams")
    take = d / "take.wav"
    _ff("-f", "lavfi", "-i", "anoisesrc=d=12:a=0.5:seed=7:r=48000",
        "-af", "volume='0.03+0.97*gt(sin(2*PI*t*1.7)*sin(2*PI*t*0.37+1)+0.3*sin(2*PI*t*5.1),0.25)':eval=frame",
        str(take))
    specs = {"red": (0.0, 10, ""), "blue": (1.5, 9, "noise"), "green": (0.8, 9, "clip")}
    out = {}
    for name, (start, dur, fx) in specs.items():
        path = d / f"{name}.mp4"
        audio = f"[1:a]atrim={start}:{start + dur},asetpts=PTS-STARTPTS"
        if fx == "noise":
            graph = f"{audio}[a];anoisesrc=d={dur}:a=0.25:seed=3:r=48000[n];[a][n]amix=inputs=2:normalize=0[o]"
        elif fx == "clip":
            graph = f"{audio},volume=8,aformat=sample_fmts=s16[o]"
        else:
            graph = f"{audio}[o]"
        _ff("-f", "lavfi", "-i", f"color=c={name}:s=320x240:r=25:d={dur}", "-i", str(take),
            "-filter_complex", graph, "-map", "0:v", "-map", "[o]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path))
        out[name] = path
    rec = d / "recorder.wav"
    _ff("-i", str(take), "-af", "atrim=0.3:12,asetpts=PTS-STARTPTS", str(rec))
    out["recorder"] = rec
    return out


def test_offsets_from_audio_alone(cams):
    ref = multicam.load_audio(cams["red"])
    for name, expected in (("blue", -1.5), ("green", -0.8), ("recorder", -0.3)):
        coarse, conf = multicam.envelope_offset(ref, multicam.load_audio(cams[name]))
        fine = multicam.refine_offset(ref, multicam.load_audio(cams[name]), coarse)
        assert abs(coarse - expected) <= 0.02 and conf > 1.15, (name, coarse, conf)
        assert abs(fine - expected) <= 0.003, (name, fine)


def test_transcript_offset_and_shift():
    words = "so today I want to show you how this little camera works in the dark".split()
    ref = {"words": [{"word": w, "start": 1.0 + i * 0.4, "end": 1.3 + i * 0.4} for i, w in enumerate(words)]}
    src = {"words": [{"word": w.upper() + ",", "start": 3.5 + i * 0.4, "end": 3.8 + i * 0.4} for i, w in enumerate(words)]}
    assert multicam.transcript_offset(ref, src) == pytest.approx(2.5)
    assert multicam.transcript_offset(ref, {"words": src["words"][:3]}) is None      # too few matches
    t = {"words": ref["words"], "segments": [{"start": 1.0, "end": 3.0, "text": "so today I want"},
                                             {"start": 3.2, "end": 7.0, "text": "to show you"}]}
    moved = multicam.shift_transcript(t, 2.0, 3.0)
    assert moved["duration"] == 3.0 and moved["words"][0]["start"] == 0.0
    assert all(0 <= w["start"] <= w["end"] <= 3.0 for w in moved["words"])
    assert [s["text"] for s in moved["segments"]] == ["so today I want", "to show you"]


def test_audio_quality_prefers_clean_sound(cams):
    clean, noisy, clipped = (multicam.audio_quality(multicam.load_audio(cams[n])) for n in ("red", "blue", "green"))
    assert clean["score"] > noisy["score"] and clean["score"] > clipped["score"]
    assert clipped["clipped"] > 0.001 and noisy["noise_db"] > clean["noise_db"] + 10


@pytest.fixture
def mc_project(client, cams):
    pid = client.post("/v2/projects", params=U, json={"name": "multicam"}).json()["id"]
    ids = {}
    for name in ("red", "blue", "green", "recorder"):
        with open(cams[name], "rb") as f:
            ids[name] = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": False},
                                    files={"file": (cams[name].name, f)}).json()["asset"]["id"]
    worker.run_until_empty()
    return pid, ids


def test_create_multicam_job(client, mc_project):
    pid, ids = mc_project
    r = client.post(f"/v2/projects/{pid}/multicam", params=U,
                    json={"asset_ids": [ids["red"], ids["blue"], ids["green"]]})
    assert r.status_code == 200
    worker.run_until_empty()
    job = client.get(f"/v2/jobs/{r.json()['job']['id']}", params=U).json()
    assert job["status"] == "succeeded", job["error"]
    mc = client.get(f"/v2/projects/{pid}/assets/{job['result']['asset_id']}", params=U).json()
    assert mc["kind"] == "multicam" and mc["duration"] == pytest.approx(8.3, abs=0.03)   # the shared stretch
    offs = {a["asset_id"]: a["offset"] for a in mc["multicam"]["angles"]}
    assert offs[ids["red"]] == pytest.approx(1.5, abs=0.005)
    assert offs[ids["blue"]] == pytest.approx(0.0, abs=0.005)
    assert offs[ids["green"]] == pytest.approx(0.7, abs=0.005)
    assert mc["multicam"]["audio"]["asset_id"] == ids["red"] and "clearest" in mc["multicam"]["audio"]["reason"]
    assert [a["label"] for a in mc["multicam"]["angles"]] == ["Camera 1", "Camera 2", "Camera 3"]

    # with a separate recorder, its sound is used
    r = client.post(f"/v2/projects/{pid}/multicam", params=U,
                    json={"asset_ids": [ids["red"], ids["blue"]], "audio_asset_id": ids["recorder"]})
    worker.run_until_empty()
    job = client.get(f"/v2/jobs/{r.json()['job']['id']}", params=U).json()
    mc2 = client.get(f"/v2/projects/{pid}/assets/{job['result']['asset_id']}", params=U).json()
    assert mc2["multicam"]["audio"]["asset_id"] == ids["recorder"]
    assert mc2["multicam"]["audio"]["offset"] == pytest.approx(1.2, abs=0.005)


def test_create_multicam_rejects_bad_input(client, mc_project, tmp_path):
    pid, ids = mc_project
    with pytest.raises(multicam.MulticamError, match="2 to 3"):
        multicam.create(pid, [ids["red"]])
    unrelated = tmp_path / "other.mp4"
    _ff("-f", "lavfi", "-i", "color=c=white:s=320x240:r=25:d=6", "-f", "lavfi",
        "-i", "anoisesrc=d=6:a=0.5:seed=99,volume='gt(sin(2*PI*t*0.9),0.5)':eval=frame",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(unrelated))
    with open(unrelated, "rb") as f:
        other = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": False},
                            files={"file": ("other.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    with pytest.raises(multicam.MulticamError, match="same take|barely overlap"):
        multicam.create(pid, [ids["red"], other])


def colour(px):
    r, g, b = px
    return "red" if r > 150 and g < 80 else "blue" if b > 150 and r < 80 else "green" if g > 80 and r < 80 else "?"


def test_agent_multicam_edit_and_render(client, mc_project):
    pid, ids = mc_project
    p = ScriptedProvider([
        AssistantTurn(None, [call("create_multicam", asset_ids=[ids["red"], ids["blue"], ids["green"]])]),
        AssistantTurn("Synced.", []),
    ])
    agent.run(p, pid, "these are three angles of one take", history=[])
    r = tool_results(p)[0]
    mc_id = r["asset_id"]
    assert len(r["angles"]) == 3 and "clearest" in r["sound_from"]

    # a transcript on the group, so angle switches land between sentences
    words = []
    t = 0.2
    for i, w in enumerate("one two three. four five six seven. eight nine ten. eleven twelve thirteen fourteen.".split()):
        words.append({"word": w, "start": round(t, 2), "end": round(t + 0.35, 2)})
        t += 0.55 if not w.endswith(".") else 0.9
    store.update_asset(mc_id, transcript={"words": words, "segments": [], "duration": 8.3}, transcript_status="done")
    client.put(f"/v2/projects/{pid}/timeline", params=U, json={"timeline": {
        "clips": [{"id": "c1", "asset_id": mc_id, "in": 0, "out": 8}],
        "output": {"width": 320, "height": 240, "fps": 25, "fit": "cover"}}})
    p = ScriptedProvider([
        AssistantTurn(None, [call("switch_angles", min_seconds=1.5, max_seconds=3.0)]),
        AssistantTurn(None, [call("set_angle", angle=2, start=6.5, end=8.0)]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "cut between the cameras, green at the end", history=[])
    r = tool_results(p)
    assert r[0]["ok"] and r[0]["shots"] >= 3
    tl = store.get_timeline(pid)["data"]
    clips = tl["clips"]
    assert sum(c["out"] - c["in"] for c in clips) == pytest.approx(8.0, abs=0.01)    # same length, just cut up
    angles = [c["angle"] for c in clips]
    assert all(a != b for a, b in zip(angles, angles[1:]))                          # never the same angle twice
    for c in clips[:-1]:                                                            # cuts fall in pauses
        assert any(w0["end"] <= c["out"] <= w1["start"] for w0, w1 in zip(words, words[1:])) or c["out"] >= 6.4
    assert clips[-1]["angle"] == 2 and clips[-1]["in"] <= 6.51        # green from 6.5 s (merged if it already was)

    job = client.post(f"/v2/projects/{pid}/renders", params=U, json={"preview": False}).json()
    worker.run_until_empty()
    done = client.get(f"/v2/jobs/{job['id']}", params=U).json()
    assert done["status"] == "succeeded", done["error"]
    out = config.settings.storage_root / "out.mp4"
    out.write_bytes(client.get(f"/v2/jobs/{job['id']}/output", params=U).content)
    spans = Timeline.model_validate(tl).clip_spans()
    names = {0: "red", 1: "blue", 2: "green"}
    for c, (a, b) in zip(clips, spans):
        px = frame(out, (a + b) / 2, 320, 240)[120, 160]
        assert colour(px) == names[c["angle"]], (c, px)

    # the sound is the group audio (the clean red camera) all the way through: no gaps at the cuts
    raw = subprocess.run([config.require_ffmpeg(), "-v", "error", "-i", str(out), "-ac", "1", "-ar", "8000",
                          "-f", "f32le", "-"], capture_output=True).stdout
    rendered = np.frombuffer(raw, np.float32)
    source = multicam.load_audio(config.settings.storage_root / store.get_asset(mc_id)["storage_key"])
    env = lambda x: np.sqrt((x[: len(x) // 400 * 400].reshape(-1, 400) ** 2).mean(axis=1))   # noqa: E731  50 ms
    er, es = env(rendered), env(source)
    n = min(len(er), len(es))
    assert np.corrcoef(er[:n], es[:n])[0, 1] > 0.95                     # same sound, in step
    for c, (a, b) in list(zip(clips, spans))[1:]:                        # no dropout at any cut
        k = int(a / 0.05)
        if es[k - 1:k + 2].min() > 0.05:
            assert er[k - 1:k + 2].min() > es[k - 1:k + 2].min() * 0.5, a
