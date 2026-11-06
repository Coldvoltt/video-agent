"""Phase 6, step 3b: background removal and replacement (MODNet mattes and chroma key)."""

import subprocess

import numpy as np
import pytest
from pydantic import ValidationError

from editor import agent, config, diff, matte, renderer, store, worker
from editor.llm import AssistantTurn
from editor.timeline import Timeline, check_assets
from test_agent import U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401
from test_render_styling import frame


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    """Stand-in for MODNet: the left half of every frame is 'the person'."""
    calls = {"frames": 0}

    def predict(rgb):
        calls["frames"] += 1
        m = np.zeros(rgb.shape[:2], np.float32)
        m[:, : rgb.shape[1] // 2] = 1.0
        return m

    monkeypatch.setattr(matte, "predict", predict)
    return calls


@pytest.fixture(scope="session")
def green_screen(tmp_path_factory):
    """3 s of a red square in front of a green screen."""
    out = tmp_path_factory.mktemp("green") / "green.mp4"
    subprocess.run([config.require_ffmpeg(), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=0x3CD95D:s=640x360:r=30:d=3",
                    "-f", "lavfi", "-i", "color=c=0xDD2222:s=160x160:r=30:d=3",
                    "-filter_complex", "[0][1]overlay=240:100", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(out)], check=True)
    return out


def _asset(aid, path, w, h, dur, fps=30, kind="video"):
    return {"id": aid, "user_id": "u1", "project_id": "p1", "kind": kind, "width": w, "height": h, "fps": fps,
            "duration": dur, "has_audio": False, "status": "ready", "role": "footage"}


def _render(tmp_path, clip_bg, assets, paths, mattes=None, clip_asset="v", out=(640, 360)):
    tl = Timeline.model_validate({"clips": [{"id": "c1", "asset_id": clip_asset, "in": 0.5, "out": 2.5,
                                             "background": clip_bg}],
                                  "output": {"width": out[0], "height": out[1], "fps": 30, "fit": "cover"}})
    dest = tmp_path / "out.mp4"
    renderer.render(tl, assets, paths, dest, mattes=mattes or {})
    return dest


def near(px, rgb, tol=40):
    return all(abs(int(a) - b) <= tol for a, b in zip(px, rgb))


def test_background_schema():
    ok = {"id": "c1", "asset_id": "a", "in": 0, "out": 2}
    Timeline.model_validate({"clips": [{**ok, "background": {"replace_with": "blur"}}]})
    for bad in ({"replace_with": "color"}, {"replace_with": "media"},
                {"replace_with": "blur", "asset_id": "x"}, {"replace_with": "sky"},
                {"replace_with": "blur", "method": "magic"}):
        with pytest.raises(ValidationError):
            Timeline.model_validate({"clips": [{**ok, "background": bad}]})


def test_background_assets_checked(media_dir):
    assets = {"v": _asset("v", None, 640, 360, 6), "img": {**_asset("img", None, 800, 600, None, kind="image")},
              "aud": {**_asset("aud", None, 0, 0, 3, kind="audio")}}
    tl = Timeline.model_validate({"clips": [
        {"id": "c1", "asset_id": "v", "in": 0, "out": 2, "background": {"replace_with": "media", "asset_id": "aud"}},
        {"id": "c2", "asset_id": "img", "in": 0, "out": 2, "background": {"replace_with": "blur"}}]})
    problems = check_assets(tl, assets, "p1")
    assert any("aud is audio" in p for p in problems)
    assert any("c2: background replacement needs a video clip" in p for p in problems)


def test_matte_cache_reuses_segments(media_dir, fake_model):
    a = _asset("v", None, 1280, 720, 6)
    src = media_dir / "landscape.mp4"
    found = matte.ensure(a, src, [(1.0, 2.0), (1.5, 3.0), (4.5, 5.5)])
    first = fake_model["frames"]
    # (1,2) and (1.5,3) merge into one segment; padded by PAD either side
    segs = sorted((s, e) for s, e, _ in matte._segments(a))
    assert segs == [(0.75, 3.25), (4.25, 5.75)]
    assert 50 <= first <= 66                                         # 15 fps over 4 s of footage
    assert found[(1.0, 2.0)][1] == 0.75
    matte.ensure(a, src, [(1.1, 1.9), (4.5, 5.5)])                  # trims inside cached ranges
    assert fake_model["frames"] == first
    assert matte.work_size(1280, 720) == (672, 384)
    assert matte.work_size(720, 1280) == (384, 672)


def test_render_ai_background_color_blur_media(media_dir, tmp_path):
    a = _asset("v", None, 1280, 720, 6)
    assets = {"v": a, "img": _asset("img", None, 800, 600, None, kind="image")}
    paths = {"v": media_dir / "landscape.mp4", "img": media_dir / "card.png"}
    found = matte.ensure(a, paths["v"], [(0.5, 2.5)])
    mattes = {"c1": found[(0.5, 2.5)]}
    plain = frame(_render(tmp_path, None, assets, paths), 1.0, 640, 360)

    out = frame(_render(tmp_path, {"replace_with": "color", "color": "#0000FF"}, assets, paths, mattes), 1.0, 640, 360)
    assert near(out[180, 600], (0, 0, 255))                            # right half: new background
    assert np.abs(out[100:260, 20:280] - plain[100:260, 20:280]).mean() < 8   # left half: the person, unchanged

    out = frame(_render(tmp_path, {"replace_with": "media", "asset_id": "img"}, assets, paths, mattes), 1.0, 640, 360)
    assert near(out[180, 600], (255, 165, 0), 45)                    # orange card behind

    out = frame(_render(tmp_path, {"replace_with": "blur", "blur": 1.0}, assets, paths, mattes), 1.0, 640, 360)
    right, left = out[40:320, 400:620], plain[40:320, 400:620]
    sharp = lambda img: np.abs(np.diff(img, axis=1)).mean()             # noqa: E731
    assert sharp(right) < sharp(left) / 3                             # background blurred
    assert np.abs(out[100:260, 20:280] - plain[100:260, 20:280]).mean() < 8

    with pytest.raises(renderer.RenderError, match="matte is missing"):
        _render(tmp_path, {"replace_with": "blur"}, assets, paths, {})


def test_chroma_key(green_screen, media_dir, tmp_path):
    assert matte.screen_color(green_screen, 1.0) is not None
    assert matte.screen_color(media_dir / "landscape.mp4", 1.0) is None
    key = matte.screen_color(green_screen, 1.0)
    assets = {"g": _asset("g", None, 640, 360, 3)}
    out = frame(_render(tmp_path, {"replace_with": "color", "color": "#0000FF", "method": "chroma", "key_color": key},
                        assets, {"g": green_screen}, clip_asset="g"), 1.0, 640, 360)
    assert near(out[20, 20], (0, 0, 255)) and near(out[340, 620], (0, 0, 255))  # screen replaced
    assert near(out[180, 320], (221, 34, 34), 45)                                # subject kept, not green-tinted


def test_agent_set_background(client, project, green_screen):
    pid, aid = project
    with open(green_screen, "rb") as f:
        gid = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": False},
                          files={"file": ("green.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    client.put(f"/v2/projects/{pid}/timeline", params=U, json={"timeline": {"clips": [
        {"id": "c1", "asset_id": aid, "in": 0, "out": 3}, {"id": "c2", "asset_id": gid, "in": 0, "out": 2}]}})
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_background", replace_with="color", color="#112233"),
                             call("set_background", replace_with="media", asset_id=aid, clip_ids=["nope"]),
                             call("set_background", replace_with="color", color="#112233", method="chroma",
                                  clip_ids=["c1"])]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "put me on a dark blue background", history=[])
    r = tool_results(p)
    assert r[0]["ok"] and [c["method"] for c in r[0]["clips"]] == ["AI cut-out", "green/blue screen key"]
    assert "min longer" in r[0]["note"]
    assert "Unknown clip ids" in r[1]["error"]
    assert "doesn't look like a green" in r[2]["error"]
    tl = store.get_timeline(pid)["data"]
    bg = tl["clips"][0]["background"]
    assert (bg["replace_with"], bg["color"], bg["method"]) == ("color", "#112233", "ai")
    assert tl["clips"][1]["background"]["method"] == "chroma"
    changes = client.get(f"/v2/projects/{pid}/timeline/changes", params=U).json()["changes"]
    assert "Background replaced (a solid colour) on 2 clips" in changes

    job = client.post(f"/v2/projects/{pid}/renders", params=U, json={"preview": True}).json()
    worker.run_until_empty()
    done = client.get(f"/v2/jobs/{job['id']}", params=U).json()
    assert done["status"] == "succeeded", done["error"]
    asset = store.get_asset(aid)
    assert matte.find(asset, 0, 3) is not None                               # matte cached for the AI clip
    assert matte.find(store.get_asset(gid), 0, 2) is None                    # chroma clip needs none

    p2 = ScriptedProvider([AssistantTurn(None, [call("set_background", replace_with="none")]),
                           AssistantTurn("Done.", [])])
    agent.run(p2, pid, "undo the background", history=[])
    row = store.get_timeline(pid)
    assert all("background" not in c for c in row["data"]["clips"])
    before = store.get_timeline(pid, row["version"] - 1)["data"]
    assert "Original background restored on 2 clips" in diff.describe_changes(before, row["data"])
