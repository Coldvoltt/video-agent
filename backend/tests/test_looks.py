"""Built-in colour looks (LUT presets): library, strength, rendering, API and agent."""

import numpy as np
import pytest

from editor import agent, diff, luts, media, renderer, store
from editor.llm import AssistantTurn
from editor.timeline import LUT_PRESETS, Timeline
from test_agent import TRANSCRIPT, U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401
from test_render_styling import frame


def test_library_matches_schema_and_files_are_valid(tmp_path):
    assert tuple(luts.PRESETS) == LUT_PRESETS
    for look in luts.PRESETS:
        path = luts.preset_path(look)
        assert media.inspect_lut(path)["kind"] == "lut"
        lut = luts.read_cube(path)
        assert lut.shape == (33, 33, 33, 3) and 0 <= lut.min() and lut.max() <= 1
        assert np.abs(lut - luts.identity()).mean() > 0.02          # each look visibly changes the image


def test_looks_keep_tones_in_order():
    """Brighter in -> brighter out along the grey axis (no banding or inverted tones)."""
    for look in luts.PRESETS:
        lut = luts.build(look)
        greys = np.array([lut[i, i, i].mean() for i in range(33)])
        assert np.all(np.diff(greys) >= -1e-6), look


def test_noir_is_black_and_white_and_strength_blends():
    rgb = np.array([[[200, 60, 30], [20, 120, 220]]], np.uint8)
    noir = luts.apply(rgb, luts.build("noir"))
    assert np.allclose(noir[..., 0], noir[..., 1], atol=0.02) and np.allclose(noir[..., 1], noir[..., 2], atol=0.02)
    half = luts.apply(rgb, luts.blend_with_identity(luts.build("noir"), 0.5))
    original = rgb / 255
    assert np.allclose(half, (noir + original) / 2, atol=0.02)
    assert np.allclose(luts.apply(rgb, luts.identity()), original, atol=1e-6)


def test_schema_rules():
    base = {"clips": [{"id": "a", "asset_id": "x", "in": 0, "out": 1}]}
    Timeline.model_validate({**base, "grade": {"lut_preset": "noir", "lut_strength": 0.4}})
    with pytest.raises(Exception):
        Timeline.model_validate({**base, "grade": {"lut_preset": "noir", "lut_asset_id": "abc"}})
    with pytest.raises(Exception):
        Timeline.model_validate({**base, "grade": {"lut_preset": "sepia_dream"}})
    assert "lut_strength" not in Timeline.model_validate({**base, "grade": {"preset": "warm"}}).to_json()["grade"]


def test_render_with_look_at_full_and_partial_strength(media_dir, tmp_path):
    info = media.probe(media_dir / "landscape.mp4")
    assets = {"v": {"id": "v", "project_id": "P", "status": "ready", **info}}
    paths = {"v": media_dir / "landscape.mp4"}
    frames = {}
    for name, grade in (("plain", None), ("full", {"lut_preset": "noir"}),
                        ("half", {"lut_preset": "noir", "lut_strength": 0.5})):
        t = Timeline.model_validate({"output": {"width": 320, "height": 180},
                                     "clips": [{"id": "a", "asset_id": "v", "in": 1, "out": 2}],
                                     **({"grade": grade} if grade else {})})
        out = tmp_path / f"{name}.mp4"
        renderer.render(t, assets, paths, out)
        frames[name] = frame(out, 0.5, 320, 180)
    spread = lambda f: np.abs(f - f.mean(axis=2, keepdims=True)).mean()  # noqa: E731  colourfulness
    assert spread(frames["full"]) < 3 < spread(frames["half"]) < spread(frames["plain"])


def test_look_api(client, project):
    pid, aid = project
    looks = client.get("/v2/luts").json()["looks"]
    assert [x["id"] for x in looks] == list(LUT_PRESETS) and all(x["css"] for x in looks)
    cube = client.get("/v2/luts/teal_orange.cube")
    assert cube.status_code == 200 and "LUT_3D_SIZE 33" in cube.text
    assert 'filename="Teal-Orange.cube"' in cube.headers["content-disposition"]
    assert client.get("/v2/luts/nope.cube").status_code == 404

    thumbs = client.get(f"/v2/projects/{pid}/luts/thumbnails", params=U).json()
    assert thumbs["asset_id"] == aid and len(thumbs["looks"]) == len(LUT_PRESETS) + 1
    assert thumbs["looks"][0]["id"] == "none" and all(x["image"].startswith("data:image/jpeg;base64,")
                                                      for x in thumbs["looks"])
    assert len({x["image"] for x in thumbs["looks"]}) == len(thumbs["looks"])     # every look differs


def test_agent_applies_and_swaps_looks(project):
    pid, aid = project
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", snap_to_words=False, clips=[{"asset_id": aid, "in": 0, "out": 8}]),
                             call("set_grade", look="teal_orange", look_strength=0.7)]),
        AssistantTurn(None, [call("set_grade", look="bleach_bypass")]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "teal and orange at 70%, then actually bleach bypass", history=[])
    grade = store.get_timeline(pid)["data"]["grade"]
    assert grade == {"lut_preset": "bleach_bypass", "lut_strength": 0.7}


def test_diff_names_the_look():
    base = {"clips": [{"id": "a", "asset_id": "x", "in": 0, "out": 1}]}
    a = {**base, "grade": {"lut_preset": "noir"}}
    b = {**base, "grade": {"lut_preset": "golden_hour", "lut_strength": 0.6}}
    assert diff.describe_changes(a, b) == ["Look: Golden Hour at 60%"]
    assert diff.describe_changes(base, a) == ["Look: Noir"]
