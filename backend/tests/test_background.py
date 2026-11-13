"""Background removal and replacement: cached cut-outs (MODNet or a soft chroma key) composited by ffmpeg."""

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
    segs = sorted((s, e) for s, e, _, _ in matte._segments(a, "ai"))
    assert segs == [(0.75, 3.25), (4.25, 5.75)]
    assert 50 <= first <= 66                                         # model on 15 keyframes a second, 4 s
    fg, alpha, seg_start = found[(1.0, 2.0)]
    assert seg_start == 0.75 and fg.suffix == ".mp4" and alpha.suffix == ".mkv"
    n = int(subprocess.run([config.settings.ffprobe or "ffprobe", "-v", "error", "-count_frames", "-select_streams",
                            "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(alpha)],
                           capture_output=True, text=True).stdout.strip())
    assert 73 <= n <= 76                                             # every frame at 30 fps (2.5 s), interpolated
    matte.ensure(a, src, [(1.1, 1.9), (4.5, 5.5)])                  # trims inside cached ranges
    assert fake_model["frames"] == first
    assert matte.find(a, 1.1, 1.9, tag="chroma00ff00t120") is None   # other methods are cached separately
    assert matte.work_size(1280, 720) == (800, 448)
    assert matte.work_size(720, 1280) == (448, 800)


def test_chroma_key_keeps_soft_edges_and_brightness():
    px = np.array([[[60, 220, 90],      # screen
                    [200, 150, 120],    # skin
                    [130, 175, 110],    # hair strand over the screen: partly see-through
                    [150, 170, 150]]],  # grey jumper with green spill
                  np.uint8)
    out, alpha = matte.chroma_key(px, "#3CDC5A", 0.12)
    assert alpha[0, 0] == 0 and alpha[0, 1] == 1
    assert 0.1 < alpha[0, 2] < 0.95
    r, g, b = out[0, 3].astype(int)
    assert g <= max(r, b) + 12                                       # spill gone
    lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]   # noqa: E731
    assert lum(out[0, 3].astype(int)) > lum(px[0, 3].astype(int)) * 0.9   # without going dark


def test_decontaminate_removes_background_fringe():
    rgb = np.zeros((200, 200, 3), np.uint8)
    rgb[:, :100] = (230, 180, 140)                                   # subject on the left, black behind
    alpha = np.zeros((200, 200), np.float32)
    alpha[:, :96] = 1
    alpha[:, 96:104] = np.linspace(1, 0, 8)                          # soft edge
    fringe = rgb[:, 100:104].copy()                                  # edge pixels: the black background
    clean = matte.decontaminate(rgb, alpha)[:, 100:104]
    assert clean.mean() > fringe.mean() + 60                         # edge takes the subject's colour
    assert (matte.decontaminate(rgb, alpha)[:, :90] == rgb[:, :90]).all()   # solid pixels untouched


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
    found = matte.ensure(assets["g"], green_screen, [(0.5, 2.5)], "chroma", key, 0.12)
    out = frame(_render(tmp_path, {"replace_with": "color", "color": "#0000FF", "method": "chroma", "key_color": key},
                        assets, {"g": green_screen}, {"c1": found[(0.5, 2.5)]}, clip_asset="g"), 1.0, 640, 360)
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
    assert matte.find(asset, 0, 3) is not None                               # cut-out cached for the AI clip
    green = store.get_asset(gid)
    key = tl["clips"][1]["background"]["key_color"]
    assert matte.find(green, 0, 2, tag=matte.method_tag("chroma", key, 0.12)) is not None
    assert matte.find(green, 0, 2) is None                                   # no AI cut-out for the keyed clip

    p2 = ScriptedProvider([AssistantTurn(None, [call("set_background", replace_with="none")]),
                           AssistantTurn("Done.", [])])
    agent.run(p2, pid, "undo the background", history=[])
    row = store.get_timeline(pid)
    assert all("background" not in c for c in row["data"]["clips"])
    before = store.get_timeline(pid, row["version"] - 1)["data"]
    assert "Original background restored on 2 clips" in diff.describe_changes(before, row["data"])
