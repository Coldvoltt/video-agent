import pytest
from pydantic import ValidationError

from editor.timeline import Timeline, check_assets


def tl(**overrides):
    data = {"clips": [{"id": "c1", "asset_id": "a1", "in": 1, "out": 3}]}
    data.update(overrides)
    return data


def test_minimal_timeline_defaults():
    t = Timeline.model_validate(tl())
    assert t.output.width == 1920 and t.output.fit == "contain"
    assert t.duration == pytest.approx(2)
    assert t.to_json()["clips"][0]["in"] == 1  # serialised with the public field name


@pytest.mark.parametrize("bad", [
    tl(clips=[]),
    tl(clips=[{"id": "c1", "asset_id": "a1", "in": 3, "out": 3}]),
    tl(clips=[{"id": "c1", "asset_id": "a1", "in": -1, "out": 3}]),
    tl(clips=[{"id": "c1", "asset_id": "a1", "in": 0, "out": 1}, {"id": "c1", "asset_id": "a1", "in": 1, "out": 2}]),
    tl(output={"width": 1081, "height": 1920}),
    tl(output={"fit": "stretch"}),
    tl(output={"background": "red; drawtext=x"}),          # no filter injection via colour
    tl(clips=[{"id": "c 1", "asset_id": "a1", "in": 0, "out": 1}]),
    tl(captions={"style": "comic"}),
    tl(effects=[{"type": "glitch"}]),                       # unknown feature -> rejected, not ignored
    tl(clips=[{"id": "c1", "asset_id": "a1", "in": 0, "out": 1, "transition_in": {"type": "fade"}}]),  # first clip
    tl(clips=[{"id": "c1", "asset_id": "a1", "in": 0, "out": 1},
              {"id": "c2", "asset_id": "a1", "in": 2, "out": 3, "transition_in": {"type": "fade", "duration": 1.0}}]),
    tl(clips=[{"id": "c1", "asset_id": "a1", "in": 0, "out": 2},
              {"id": "c2", "asset_id": "a1", "in": 2, "out": 4, "transition_in": {"type": "zoomblur"}}]),
    tl(text=[{"id": "c1", "text": "dup id", "start": 0, "end": 1}]),
    tl(text=[{"id": "t1", "text": "x", "start": 1, "end": 1.1}]),
    tl(grade={"preset": "teal-orange"}),
    tl(captions={"color": "white"}),
])
def test_invalid_timelines_rejected(bad):
    with pytest.raises(ValidationError):
        Timeline.model_validate(bad)


def test_check_assets():
    t = Timeline.model_validate({"clips": [
        {"id": "ok", "asset_id": "v", "in": 0, "out": 5},
        {"id": "past_end", "asset_id": "v", "in": 5, "out": 12},
        {"id": "missing", "asset_id": "nope", "in": 0, "out": 1},
        {"id": "audio", "asset_id": "au", "in": 0, "out": 1},
        {"id": "busy", "asset_id": "p", "in": 0, "out": 1},
        {"id": "other_project", "asset_id": "x", "in": 0, "out": 1},
        {"id": "image", "asset_id": "img", "in": 0, "out": 30},
    ]})
    assets = {
        "v": {"project_id": "P", "status": "ready", "kind": "video", "duration": 10.0},
        "au": {"project_id": "P", "status": "ready", "kind": "audio", "duration": 10.0},
        "p": {"project_id": "P", "status": "processing", "kind": None, "duration": None},
        "x": {"project_id": "Q", "status": "ready", "kind": "video", "duration": 10.0},
        "img": {"project_id": "P", "status": "ready", "kind": "image", "duration": None},
    }
    errors = check_assets(t, assets, "P")
    flagged = {e.split(":")[0] for e in errors}
    assert flagged == {"clip past_end", "clip missing", "clip audio", "clip busy", "clip other_project"}


def test_clip_spans_with_transitions():
    t = Timeline.model_validate({"clips": [
        {"id": "a", "asset_id": "x", "in": 0, "out": 4},
        {"id": "b", "asset_id": "x", "in": 10, "out": 13, "transition_in": {"type": "fade", "duration": 1}},
        {"id": "c", "asset_id": "x", "in": 20, "out": 22},
    ]})
    assert t.clip_spans() == [(0, 4), (3, 6), (6, 8)]
    assert t.duration == 8
    assert "transition_in" not in t.to_json()["clips"][0]   # unset optionals stay out of the JSON


def test_layer_asset_checks():
    from editor.timeline import layer_warnings

    t = Timeline.model_validate({
        "clips": [{"id": "a", "asset_id": "v", "in": 0, "out": 5}],
        "overlays": [{"id": "logo", "asset_id": "au", "start": 0, "end": 2}],
        "audio": [{"id": "music", "asset_id": "silent", "start": 0}],
        "grade": {"lut_asset_id": "img"},
        "text": [{"id": "late", "text": "bye", "start": 9, "end": 10}],
    })
    assets = {
        "v": {"project_id": "P", "status": "ready", "kind": "video", "duration": 10.0, "has_audio": True},
        "au": {"project_id": "P", "status": "ready", "kind": "audio", "duration": 10.0, "has_audio": True},
        "silent": {"project_id": "P", "status": "ready", "kind": "video", "duration": 10.0, "has_audio": False},
        "img": {"project_id": "P", "status": "ready", "kind": "image", "duration": None, "has_audio": False},
    }
    errors = check_assets(t, assets, "P")
    assert any(e.startswith("overlay logo") for e in errors)
    assert any(e.startswith("audio music") and "no sound" in e for e in errors)
    assert any(e.startswith("grade lut") for e in errors)
    assert layer_warnings(t) == ["late starts at 9.0s, after the video ends (5.0s); it will be skipped"]
