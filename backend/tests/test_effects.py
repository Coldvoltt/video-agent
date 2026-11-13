"""Picture effects: video noise removal, film grain, blur (whole frame or a region)."""

import subprocess

import numpy as np
import pytest
from pydantic import ValidationError

from editor import agent, config, diff, renderer, store
from editor.llm import AssistantTurn
from editor.timeline import Timeline
from test_agent import U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401
from test_render_styling import frame

W, H = 640, 360


@pytest.fixture(scope="session")
def noisy(tmp_path_factory):
    """3 s of flat grey with heavy noise."""
    out = tmp_path_factory.mktemp("noisy") / "noisy.mp4"
    subprocess.run([config.require_ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=0x808080:s=640x360:r=30:d=3", "-vf", "noise=alls=40:allf=t+u",
                    "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def _asset(aid, kind="video", w=W, h=H, dur=6.0):
    return {"id": aid, "kind": kind, "width": w, "height": h, "fps": 30, "duration": dur, "has_audio": False,
            "status": "ready", "role": "footage"}


def _render(tmp_path, src, effects, kind="video", out_name="out.mp4"):
    tl = Timeline.model_validate({"clips": [{"id": "c1", "asset_id": "v", "in": 0, "out": 3}], "effects": effects,
                                  "output": {"width": W, "height": H, "fps": 30, "fit": "cover"}})
    dest = tmp_path / out_name
    renderer.render(tl, {"v": _asset("v", kind)}, {"v": src}, dest)
    return dest


def sharpness(img):
    """Share of pixels on a hard edge (big jump to the neighbour)."""
    return float((np.abs(np.diff(img.mean(axis=2), axis=1)) > 40).mean())


def test_effect_schema():
    base = {"clips": [{"id": "c1", "asset_id": "a", "in": 0, "out": 2}]}
    Timeline.model_validate({**base, "effects": [{"id": "e1", "type": "blur", "region": {"x": 0.5, "y": 0, "w": 0.5, "h": 1}}]})
    for bad in ({"id": "e1", "type": "sharpen"}, {"id": "e1", "type": "grain", "region": {"x": 0, "y": 0, "w": 1, "h": 1}},
                {"id": "e1", "type": "blur", "region": {"x": 0.6, "y": 0, "w": 0.5, "h": 1}},
                {"id": "e1", "type": "blur", "start": 1, "end": 1.1}, {"id": "c1", "type": "blur"}):
        with pytest.raises(ValidationError):
            Timeline.model_validate({**base, "effects": [bad]})


def test_region_blur_only_in_its_window(media_dir, tmp_path):
    src = media_dir / "landscape.mp4"
    plain = _render(tmp_path, src, [], out_name="plain.mp4")
    out = _render(tmp_path, src, [{"id": "b1", "type": "blur", "start": 1, "end": 2, "strength": 0.8,
                                   "region": {"x": 0, "y": 0, "w": 0.5, "h": 1}}])
    a, b = frame(plain, 1.5, W, H), frame(out, 1.5, W, H)
    assert sharpness(b[:, 10:300]) < sharpness(a[:, 10:300]) / 3     # left half blurred
    assert np.abs(b[:, 340:] - a[:, 340:]).mean() < 3                  # right half untouched
    assert np.abs(frame(out, 0.5, W, H) - frame(plain, 0.5, W, H)).mean() < 3   # not before start
    assert np.abs(frame(out, 2.5, W, H) - frame(plain, 2.5, W, H)).mean() < 3   # nor after end


def test_full_blur_grain_and_denoise(media_dir, noisy, tmp_path):
    src = media_dir / "landscape.mp4"
    plain = frame(_render(tmp_path, src, [], out_name="plain.mp4"), 1.5, W, H)
    blurred = frame(_render(tmp_path, src, [{"id": "b1", "type": "blur"}]), 1.5, W, H)
    assert sharpness(blurred) < sharpness(plain) / 2

    card = media_dir / "card.png"
    flat = frame(_render(tmp_path, card, [], kind="image", out_name="flat.mp4"), 1.5, W, H)
    grainy = frame(_render(tmp_path, card, [{"id": "g1", "type": "grain", "strength": 0.6}], kind="image",
                           out_name="grain.mp4"), 1.5, W, H)
    assert flat.std(axis=(0, 1)).max() < 3 and grainy[..., 1].std() > 6

    raw = frame(_render(tmp_path, noisy, [], out_name="raw.mp4"), 1.5, W, H)
    clean = frame(_render(tmp_path, noisy, [{"id": "d1", "type": "denoise", "strength": 1.0}], out_name="dn.mp4"),
                  1.5, W, H)
    assert clean.std() < raw.std() * 0.6


def test_agent_add_effect(client, project):
    pid, aid = project
    client.put(f"/v2/projects/{pid}/timeline", params=U,
               json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}})
    p = ScriptedProvider([
        AssistantTurn(None, [call("add_effect", type="grain", strength=0.3),
                             call("add_effect", type="blur", start=1, end=3,
                                  region={"x": 0.7, "y": 0.1, "w": 0.2, "h": 0.2}),
                             call("add_effect", type="grain", region={"x": 0, "y": 0, "w": 1, "h": 1})]),
        AssistantTurn(None, [call("remove_layer", layer_id="grain1")]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "film grain and blur the logo", history=[])
    r = tool_results(p)
    assert r[0]["ok"] and r[1]["ok"] and r[3]["ok"]
    assert any("region only applies to blur" in e for e in r[2]["errors"])
    tl = store.get_timeline(pid)["data"]
    assert [e["type"] for e in tl["effects"]] == ["blur"]
    assert tl["effects"][0]["region"] == {"x": 0.7, "y": 0.1, "w": 0.2, "h": 0.2}
    before = {"clips": tl["clips"], "effects": []}
    assert "Added blur (part of the frame)" in diff.describe_changes(before, tl)


def test_agent_blur_level(client, project):
    pid, aid = project
    client.put(f"/v2/projects/{pid}/timeline", params=U,
               json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}})
    p = ScriptedProvider([
        AssistantTurn(None, [call("add_effect", type="blur", strength=0.2, start=1, end=3)]),
        AssistantTurn(None, [call("update_effect", layer_id="blur1", strength=1.0, whole_video=True),
                             call("update_effect", layer_id="nope", strength=0.5)]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "a light blur... actually blur it completely", history=[])
    r = tool_results(p)
    assert r[0]["ok"] and r[1]["ok"] and "No effect with id" in r[2]["error"]
    (fx,) = store.get_timeline(pid)["data"]["effects"]
    assert fx["strength"] == 1.0 and fx["start"] == 0 and fx.get("end") is None


def test_blur_level_changes_the_picture(media_dir, tmp_path):
    src = media_dir / "landscape.mp4"
    light = frame(_render(tmp_path, src, [{"id": "b1", "type": "blur", "strength": 0.2}], out_name="l.mp4"), 1.5, W, H)
    heavy = frame(_render(tmp_path, src, [{"id": "b1", "type": "blur", "strength": 1.0}], out_name="h.mp4"), 1.5, W, H)
    # edge energy: blurring spreads a step edge out (same total change), so square it
    energy = lambda img: float((np.diff(img.mean(axis=2), axis=1) ** 2).mean())   # noqa: E731
    assert energy(heavy) < energy(light) * 0.3
