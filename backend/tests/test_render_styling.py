"""Phase 3 rendering: transitions, grade, LUT, overlays, captions, text, music and SFX."""

import json
import subprocess

import numpy as np
import pytest

from editor import captions, media, renderer
from editor.config import require_ffmpeg, require_ffprobe
from editor.timeline import Timeline

W, H = 360, 640


def probe(path):
    out = subprocess.run([require_ffprobe(), "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def frame(path, t, w=W, h=H) -> np.ndarray:
    raw = subprocess.run([require_ffmpeg(), "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


def loudness(path, start, dur, band_hz=None) -> float:
    af = "volumedetect" if band_hz is None else f"bandpass=f={band_hz}:width_type=h:w=30,volumedetect"
    proc = subprocess.run([require_ffmpeg(), "-hide_banner", "-nostats", "-ss", str(start), "-t", str(dur), "-i",
                           str(path), "-af", af, "-f", "null", "-"], capture_output=True, text=True)
    return float(proc.stderr.split("mean_volume:")[1].split("dB")[0])


@pytest.fixture(scope="module")
def lut_file(tmp_path_factory):
    """A 2x2x2 LUT that maps everything to pure red."""
    p = tmp_path_factory.mktemp("lut") / "red.cube"
    p.write_text("TITLE \"red\"\nLUT_3D_SIZE 2\n" + "1.0 0.0 0.0\n" * 8)
    return p


@pytest.fixture(scope="module")
def assets(media_dir, lut_file):
    out, paths = {}, {}
    for aid, name in (("land", "landscape.mp4"), ("port", "portrait_silent.mp4"), ("card", "card.png"),
                      ("tone", "tone.mp3")):
        out[aid] = {"id": aid, "project_id": "P", "status": "ready", **media.probe(media_dir / name)}
        paths[aid] = media_dir / name
    out["lut"] = {"id": "lut", "project_id": "P", "status": "ready", **media.inspect_lut(lut_file)}
    paths["lut"] = lut_file
    return out, paths


WORDS = [{"word": w, "start": 0.3 + i * 0.4, "end": 0.6 + i * 0.4}
         for i, w in enumerate("This is a caption test with some words".split())]
TRANSCRIPTS = {"land": {"words": WORDS, "segments": []}}


def base(**extra):
    return {"output": {"width": W, "height": H, "fps": 30, "fit": "cover"},
            "clips": [{"id": "a", "asset_id": "land", "in": 0, "out": 3},
                      {"id": "b", "asset_id": "port", "in": 0, "out": 3,
                       "transition_in": {"type": "slideleft", "duration": 0.5}},
                      {"id": "c", "asset_id": "land", "in": 3, "out": 5.5,
                       "transition_in": {"type": "fadeblack", "duration": 0.4}}],
            **extra}


def test_everything_at_once(assets, tmp_path):
    a, p = assets
    t = Timeline.model_validate(base(
        grade={"preset": "warm", "contrast": 1.1},
        captions={"style": "karaoke"},
        text=[{"id": "title", "text": "Day one {\\b1}", "start": 0, "end": 2, "style": "title", "animation": "pop"},
              {"id": "name", "text": "Ada, host", "start": 2, "end": 4, "style": "lower_third",
               "position": "bottom-left", "animation": "slide_up"},
              {"id": "tw", "text": "typing...", "start": 4, "end": 6, "style": "label", "animation": "typewriter"},
              {"id": "late", "text": "never shown", "start": 30, "end": 31}],
        overlays=[{"id": "logo", "asset_id": "card", "start": 0.5, "end": 5, "position": "top-right", "opacity": 0.8},
                  {"id": "pip", "asset_id": "port", "start": 2, "end": 4, "position": "bottom-right", "scale": 0.3}],
        audio=[{"id": "music", "asset_id": "tone", "loop": True, "duck": True, "volume": 0.4, "fade_out": 1},
               {"id": "whoosh", "asset_id": "tone", "start": 2.5, "out": 0.5, "volume": 1.0}],
    ))
    out = tmp_path / "all.mp4"
    info = renderer.render(t, a, p, out, transcripts=TRANSCRIPTS)
    meta = probe(out)
    assert float(meta["format"]["duration"]) == pytest.approx(t.duration, abs=0.12)  # 8.5 - 0.9 = 7.6
    v = next(s for s in meta["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (W, H) and info["duration"] == pytest.approx(7.6)


def test_captions_and_grade_change_pixels(assets, tmp_path):
    a, p = assets
    plain, styled, red = tmp_path / "plain.mp4", tmp_path / "styled.mp4", tmp_path / "red.mp4"
    renderer.render(Timeline.model_validate(base()), a, p, plain)
    renderer.render(Timeline.model_validate(base(captions={"style": "boxed", "position": "bottom"})),
                    a, p, styled, transcripts=TRANSCRIPTS)
    renderer.render(Timeline.model_validate(base(grade={"lut_asset_id": "lut"})), a, p, red)

    f_plain, f_cap = frame(plain, 1.0), frame(styled, 1.0)
    lower, upper = slice(int(H * 0.6), H), slice(0, int(H * 0.4))
    assert np.abs(f_cap[lower] - f_plain[lower]).mean() > 3      # caption drawn in the lower part
    assert np.abs(f_cap[upper] - f_plain[upper]).mean() < 1      # and nowhere near the top

    f_red = frame(red, 1.0)
    assert f_red[..., 0].mean() > 200 and f_red[..., 1].mean() < 40   # LUT applied


def test_music_is_ducked_under_speech(assets, tmp_path):
    a, p = assets
    # Clip 1 has a loud tone (speech stand-in); clip 2 is silent. Music should be quieter under clip 1.
    t = Timeline.model_validate({
        "output": {"width": W, "height": H},
        "clips": [{"id": "talk", "asset_id": "land", "in": 0, "out": 3, "volume": 1.0},
                  {"id": "quiet", "asset_id": "port", "in": 0, "out": 3}],
        "audio": [{"id": "music", "asset_id": "tone", "loop": True, "duck": True, "volume": 0.5}],
    })
    not_ducked = Timeline.model_validate({**t.to_json(), "audio": [
        {"id": "music", "asset_id": "tone", "loop": True, "duck": False, "volume": 0.5}]})
    ducked, plain = tmp_path / "ducked.mp4", tmp_path / "plain.mp4"
    renderer.render(t, a, p, ducked)
    renderer.render(not_ducked, a, p, plain)
    # "Speech" is a 440 Hz tone, the music 220 Hz: isolate the music with a band-pass.
    under_speech_ducked = loudness(ducked, 0.8, 1.5, band_hz=220)
    under_speech_plain = loudness(plain, 0.8, 1.5, band_hz=220)
    assert under_speech_plain - under_speech_ducked > 6          # music dips while someone talks
    # ...and recovers where nobody speaks.
    assert loudness(ducked, 4.0, 1.5, band_hz=220) == pytest.approx(loudness(plain, 4.0, 1.5, band_hz=220), abs=1.5)


def test_output_words_follow_transitions():
    t = Timeline.model_validate({"clips": [
        {"id": "a", "asset_id": "x", "in": 10, "out": 14},
        {"id": "b", "asset_id": "x", "in": 20, "out": 24, "transition_in": {"type": "fade", "duration": 1}},
    ]})
    words = [{"word": "skip", "start": 5, "end": 5.5}, {"word": "one", "start": 11, "end": 11.5},
             {"word": "two", "start": 21, "end": 21.5}, {"word": "{evil}", "start": 22, "end": 22.4}]
    out = captions.output_words(t, {"x": {"words": words}})
    assert [(w.text, round(w.start, 2)) for w in out] == [("one", 1.0), ("two", 4.0), ("evil", 5.0)]


def test_caption_grouping_breaks_on_sentences_and_pauses():
    ws = [captions.TimedWord(*x) for x in [("Hi.", 0, 0.3), ("So", 0.4, 0.6), ("we", 0.6, 0.8), ("go", 0.8, 1.0),
                                           ("now", 1.0, 1.2), ("later", 3.0, 3.3)]]
    groups = captions.group_words(ws, 3)
    assert [[w.text for w in g] for g in groups] == [["Hi."], ["So", "we", "go"], ["now"], ["later"]]


def test_ass_has_no_injected_overrides():
    t = Timeline.model_validate({"clips": [{"id": "a", "asset_id": "x", "in": 0, "out": 5}],
                                 "text": [{"id": "t", "text": "{\\pos(0,0)}hack\\N", "start": 0, "end": 2}]})
    ass = captions.build_ass(t, {})
    line = [l for l in ass.splitlines() if l.startswith("Dialogue")][0]
    assert line.endswith("pos(0,0)hackN") and "{\\pos(0,0)}hack" not in line


def test_lut_validation(tmp_path):
    bad = tmp_path / "bad.cube"
    bad.write_text("LUT_3D_SIZE 2\n0 0 0\n")
    with pytest.raises(media.MediaError, match="expected 8"):
        media.inspect_lut(bad)
    one_d = tmp_path / "1d.cube"
    one_d.write_text("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n")
    with pytest.raises(media.MediaError, match="1D"):
        media.inspect_lut(one_d)


def test_captions_skip_fillers_and_break_on_segments():
    t = Timeline.model_validate({"clips": [{"id": "a", "asset_id": "x", "in": 0, "out": 10}]})
    tr = {"x": {"segments": [{"start": 0, "end": 1.5, "text": "Hi um there"}, {"start": 1.6, "end": 3, "text": "Next one"}],
                "words": [{"word": "Hi", "start": 0, "end": 0.4}, {"word": "um", "start": 0.5, "end": 0.8},
                          {"word": "there", "start": 0.9, "end": 1.5}, {"word": "Next", "start": 1.6, "end": 2.0},
                          {"word": "one", "start": 2.1, "end": 3.0}]}}
    words = captions.output_words(t, tr)
    assert [w.text for w in words] == ["Hi", "there", "Next", "one"]
    assert [[w.text for w in g] for g in captions.group_words(words, 5)] == [["Hi", "there"], ["Next", "one"]]
