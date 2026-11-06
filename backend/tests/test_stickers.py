"""Phase 6, step 3: animated stickers and callouts (Lottie)."""

import io
import json

import numpy as np
import pytest
from PIL import Image

from editor import agent, diff, media, renderer, stickers, store, worker
from editor.llm import AssistantTurn
from editor.timeline import Timeline, check_assets
from test_agent import TRANSCRIPT, U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401
from test_render_styling import frame


@pytest.fixture(autouse=True)
def offline_emoji(monkeypatch):
    """Serve a stand-in animation for emoji instead of fetching from Google's CDN."""
    fake = json.dumps(stickers._callout("sparkle", "#FF8800")).encode()

    def fetch(code, client=None):
        if code.startswith("0"):
            raise stickers.StickerError(f"No animated version of that emoji ({code})")
        return fake

    monkeypatch.setattr(stickers, "_emoji_json", fetch)


def opaque(png: bytes) -> int:
    return int((np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))[..., 3] > 0).sum())


def test_emoji_codes():
    assert stickers.emoji_code("🔥") == "1f525"
    assert stickers.emoji_code("U+1F525") == "1f525"
    assert stickers.emoji_code("1F44D 1F3FB") == "1f44d_1f3fb"
    assert stickers.emoji_code("❤️") == "2764_fe0f"
    with pytest.raises(stickers.StickerError):
        stickers.emoji_code("")
    with pytest.raises(stickers.StickerError):
        stickers.emoji_code("lasers")


@pytest.mark.parametrize("name", list(stickers.CALLOUTS))
def test_callouts_draw_on_and_hold(name):
    data = stickers.resolve(name, "#FF0000")
    frames = stickers.render_frames(data, duration=2.0, fps=30, width_px=200, loop=False)
    assert len(frames) == 60
    assert opaque(frames[1]) < opaque(frames[-1]) and opaque(frames[-1]) > 200        # it draws on
    assert frames[-1] == frames[-2]                                                  # then holds its last frame
    looped = stickers.render_frames(data, duration=2.0, fps=30, width_px=200, loop=True)
    assert any(opaque(f) < opaque(looped[-1]) / 2 for f in looped[30:])              # looping restarts the draw


def test_rotation_and_colour():
    arrow = stickers.resolve("arrow", "#00FF00")
    right = Image.open(io.BytesIO(stickers.render_frames(arrow, 1.5, 30, 200, False)[-1])).convert("RGBA")
    down = Image.open(io.BytesIO(stickers.render_frames(arrow, 1.5, 30, 200, False, rotation=90)[-1])).convert("RGBA")
    a, b = np.asarray(right), np.asarray(down)
    ys, xs = np.nonzero(a[..., 3]); ys2, xs2 = np.nonzero(b[..., 3])
    assert (xs.max() - xs.min()) > (ys.max() - ys.min())              # horizontal...
    assert (ys2.max() - ys2.min()) > (xs2.max() - xs2.min())          # ...turned to vertical
    solid = a[a[..., 3] == 255][:, :3]
    assert solid[:, 1].mean() > 200 and solid[:, 0].mean() < 60       # it's green


def test_lottie_upload_validation(tmp_path):
    good = tmp_path / "anim.json"
    good.write_text(json.dumps(stickers._callout("check", "#FFFFFF")))
    assert stickers.inspect_lottie(good)["kind"] == "lottie"
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": 1}')
    with pytest.raises(stickers.StickerError, match="Not a Lottie"):
        stickers.inspect_lottie(bad)


def test_schema_and_checks():
    base = {"clips": [{"id": "a", "asset_id": "v", "in": 0, "out": 5}]}
    t = Timeline.model_validate({**base, "stickers": [
        {"id": "s1", "sticker": "circle", "start": 1, "end": 2},
        {"id": "s2", "sticker": "emoji:🔥", "start": 1, "end": 2},
        {"id": "s3", "sticker": "asset:anim", "start": 1, "end": 2},
        {"id": "s4", "sticker": "lasers", "start": 1, "end": 2}]})
    assets = {"v": {"project_id": "P", "status": "ready", "kind": "video", "duration": 10.0},
              "anim": {"project_id": "P", "status": "ready", "kind": "image", "duration": None}}
    errors = check_assets(t, assets, "P")
    assert any("s3" in e and "lottie" in e for e in errors) and any("s4" in e and "unknown" in e for e in errors)
    for bad in ({"sticker": "circle; rm -rf", "start": 1, "end": 2}, {"sticker": "circle", "start": 1, "end": 1.1},
                {"sticker": "circle", "start": 1, "end": 2, "scale": 2}):
        with pytest.raises(Exception):
            Timeline.model_validate({**base, "stickers": [{"id": "x", **bad}]})


def test_sticker_renders_in_place(media_dir, tmp_path):
    assets = {"v": {"id": "v", "project_id": "P", "status": "ready", **media.probe(media_dir / "portrait_silent.mp4")}}
    t = Timeline.model_validate({
        "output": {"width": 320, "height": 320},
        "clips": [{"id": "a", "asset_id": "v", "in": 0, "out": 3}],
        "stickers": [{"id": "s", "sticker": "box", "start": 1.0, "end": 2.5, "x": 0.75, "y": 0.25, "scale": 0.4,
                      "color": "#FF0000"}],
    })
    out = tmp_path / "s.mp4"
    renderer.render(t, assets, {"v": media_dir / "portrait_silent.mp4"}, out)

    def red(f, region):
        y0, y1, x0, x1 = region
        px = f[y0:y1, x0:x1]
        return ((px[..., 0] > 180) & (px[..., 1] < 80) & (px[..., 2] < 80)).sum()

    top_right, bottom_left = (0, 160, 160, 320), (160, 320, 0, 160)
    during, before = frame(out, 2.2, 320, 320), frame(out, 0.5, 320, 320)
    # The test pattern has red bars of its own, so compare against the same frame area before the sticker.
    added_tr = red(during, top_right) - red(before, top_right)
    added_bl = red(during, bottom_left) - red(before, bottom_left)
    assert added_tr > 800 and abs(added_bl) < added_tr / 10        # a 128 px box outline is ~1500 px


def test_agent_sticker_tools(client, project, tmp_path):
    pid, aid = project
    anim = tmp_path / "wave.json"
    anim.write_text(json.dumps(stickers._callout("underline", "#00FFAA")))
    with open(anim, "rb") as f:
        up = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": False},
                         files={"file": ("wave.json", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    assert client.get(f"/v2/projects/{pid}/assets/{up}", params=U).json()["kind"] == "lottie"
    client.put(f"/v2/projects/{pid}/timeline", params=U,
               json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}})
    p = ScriptedProvider([
        AssistantTurn(None, [call("list_stickers"),
                             call("add_sticker", sticker="circle", start=1, color="#FF3B30", x=0.7, y=0.7),
                             call("add_sticker", sticker="🔥", start=2.5, duration=1),
                             call("add_sticker", sticker=up, start=4),
                             call("add_sticker", sticker="lasers", start=1),
                             call("add_sticker", sticker="emoji:0000", start=1)]),
        AssistantTurn(None, [call("emphasize", items=[{"phrase": "video editor", "emoji": "🚀", "zoom": False}])]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "add stickers", history=[])
    r = tool_results(p)
    assert [c["sticker"] for c in r[0]["callouts"]] == list(stickers.CALLOUTS)
    assert r[0]["uploaded_animations"][0]["sticker"] == f"asset:{up}"
    assert r[1]["ok"] and r[2]["ok"] and r[3]["ok"]
    assert "Unknown sticker" in r[4]["error"] and "No animated version" in r[5]["error"]
    tl = store.get_timeline(pid)["data"]
    refs = [st["sticker"] for st in tl["stickers"]]
    assert refs == ["circle", "emoji:🔥", f"asset:{up}", "emoji:🚀"]
    rocket = tl["stickers"][-1]
    kw = next(t for t in tl["text"] if t["style"] == "keyword")
    assert rocket["start"] == kw["start"] and rocket["y"] < 0.64                     # above the keyword
    changes = client.get(f"/v2/projects/{pid}/timeline/changes", params=U).json()["changes"]
    assert any(c.startswith("Added stickers: circle, 🔥") for c in changes)

    job = client.post(f"/v2/projects/{pid}/renders", params=U, json={"preview": True}).json()
    worker.run_until_empty()
    done = client.get(f"/v2/jobs/{job['id']}", params=U).json()
    assert done["status"] == "succeeded", done["error"]


def test_sticker_api(client):
    cat = client.get("/v2/stickers").json()
    assert [c["id"] for c in cat["callouts"]] == list(stickers.CALLOUTS) and "CC BY 4.0" in cat["emoji_credit"]
    lot = client.get("/v2/stickers/lottie", params={"sticker": "arrow", "color": "#112233"})
    assert lot.status_code == 200 and lot.json()["layers"][0]["shapes"][0]["it"][-2]["c"]["k"][:3] == pytest.approx(
        [0x11 / 255, 0x22 / 255, 0x33 / 255])
    assert client.get("/v2/stickers/lottie", params={"sticker": "emoji:🔥"}).status_code == 200
    assert client.get("/v2/stickers/lottie", params={"sticker": "lasers"}).status_code == 404
