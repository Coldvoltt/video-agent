import json
import subprocess

import pytest

from editor import media, renderer
from editor.config import require_ffprobe
from editor.timeline import Timeline


def ffprobe(path):
    out = subprocess.run(
        [require_ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    return float(info["format"]["duration"]), v, a


def test_probe_kinds(media_dir):
    v = media.probe(media_dir / "landscape.mp4")
    assert v["kind"] == "video" and v["has_audio"] and v["width"] == 1280 and v["fps"] == pytest.approx(30)
    assert v["duration"] == pytest.approx(6, abs=0.1)
    s = media.probe(media_dir / "portrait_silent.mp4")
    assert s["kind"] == "video" and not s["has_audio"]
    assert media.probe(media_dir / "card.png")["kind"] == "image"
    a = media.probe(media_dir / "tone.mp3")
    assert a["kind"] == "audio" and not a["has_video"]


def _assets(media_dir):
    assets, paths = {}, {}
    for aid, name in (("land", "landscape.mp4"), ("port", "portrait_silent.mp4"), ("card", "card.png")):
        info = media.probe(media_dir / name)
        assets[aid] = {"id": aid, "project_id": "P", "status": "ready", **info}
        paths[aid] = media_dir / name
    return assets, paths


@pytest.mark.parametrize("fit", ["cover", "contain"])
def test_render_vertical_short_mixed_sources(media_dir, tmp_path, fit):
    assets, paths = _assets(media_dir)
    t = Timeline.model_validate({
        "output": {"width": 720, "height": 1280, "fps": 30, "fit": fit},
        "clips": [
            {"id": "a", "asset_id": "land", "in": 1.0, "out": 2.5},
            {"id": "b", "asset_id": "port", "in": 0.5, "out": 2.0},   # no audio -> silence
            {"id": "c", "asset_id": "card", "in": 0, "out": 1.0},     # still image
            {"id": "d", "asset_id": "land", "in": 4.0, "out": 5.5, "volume": 0.5},
        ],
    })
    out = tmp_path / "out.mp4"
    seen = []
    info = renderer.render(t, assets, paths, out, preview=False, on_progress=seen.append)

    duration, v, a = ffprobe(out)
    assert duration == pytest.approx(5.5, abs=0.15)
    assert (v["width"], v["height"]) == (720, 1280)
    assert len(a) == 1 and a[0]["channels"] == 2
    assert info["size_bytes"] > 0 and seen[-1] == 1.0


def test_preview_is_downscaled(media_dir, tmp_path):
    assets, paths = _assets(media_dir)
    t = Timeline.model_validate({
        "output": {"width": 1920, "height": 1080, "fps": 60},
        "clips": [{"id": "a", "asset_id": "land", "in": 0, "out": 1}],
    })
    out = tmp_path / "preview.mp4"
    info = renderer.render(t, assets, paths, out, preview=True)
    _, v, _ = ffprobe(out)
    assert (v["width"], v["height"]) == (854, 480) == (info["width"], info["height"])
    assert info["fps"] == 30


def test_render_failure_raises(media_dir, tmp_path):
    assets, paths = _assets(media_dir)
    paths["land"] = tmp_path / "missing.mp4"
    t = Timeline.model_validate({"clips": [{"id": "a", "asset_id": "land", "in": 0, "out": 1}]})
    with pytest.raises(renderer.RenderError):
        renderer.render(t, assets, paths, tmp_path / "x.mp4")
