"""Phase 6, step 1: B-roll (own clips and Pexels stock), rendering, API and agent tools."""

import json
import subprocess

import httpx
import numpy as np
import pytest

from editor import agent, broll, diff, media, renderer, store, worker
from editor.config import require_ffmpeg, settings
from editor.llm import AssistantTurn
from editor.timeline import Timeline, check_assets
from test_agent import TRANSCRIPT, U, ScriptedProvider, call, client, project, talk_video, tool_results  # noqa: F401
from test_render_styling import frame


@pytest.fixture(scope="module")
def multishot(tmp_path_factory):
    """6 s clip with three distinct 2 s shots (red, green, blue), like a B-roll clip."""
    out = tmp_path_factory.mktemp("broll") / "shots.mp4"
    subprocess.run([require_ffmpeg(), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=red:s=320x180:d=2:r=30",
                    "-f", "lavfi", "-i", "color=c=green:s=320x180:d=2:r=30",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2:r=30",
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p",
                    str(out)], check=True)
    return out


def test_shot_ranges_and_descriptions(multishot):
    info = media.probe(multishot)
    assert broll.shot_ranges(multishot, info["duration"]) == [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]
    seen = {}

    def vision(images):
        seen["n"] = len(images)
        return {"shots": [{"n": i, "description": f"shot {i}", "tags": ["colour"]} for i in (1, 2, 3)]}

    d = broll.describe_clip(multishot, info, vision)
    assert seen["n"] == 3 and d["described"] and d["shots"][1] == {
        "start": 2.0, "end": 4.0, "description": "shot 2", "tags": ["colour"]}
    plain = broll.describe_clip(multishot, info, None)
    assert not plain["described"] and len(plain["shots"]) == 3


# ---------------------------------------------------------------- Pexels client (mocked HTTP)

PEXELS_VIDEO = {
    "id": 123, "duration": 9, "url": "https://www.pexels.com/video/123/", "image": "https://images.pexels.com/x.jpg",
    "user": {"name": "Ada", "url": "https://www.pexels.com/@ada"},
    "video_files": [
        {"file_type": "video/mp4", "width": 640, "height": 360, "link": "https://videos.pexels.com/small.mp4"},
        {"file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://videos.pexels.com/hd.mp4"},
        {"file_type": "video/mp4", "width": 3840, "height": 2160, "link": "https://videos.pexels.com/4k.mp4"},
    ],
}


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_stock_picks_right_size_and_sends_key(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "pk-test")
    seen = {}

    def handler(req):
        seen["auth"], seen["params"] = req.headers.get("Authorization"), dict(req.url.params)
        return httpx.Response(200, json={"videos": [PEXELS_VIDEO]})

    results = broll.search_pexels("coffee", "portrait", client=mock_client(handler))
    assert seen["auth"] == "pk-test" and seen["params"] == {"query": "coffee", "per_page": "8", "orientation": "portrait"}
    assert results[0]["download_url"].endswith("hd.mp4")             # smallest file >= 1080 on the short side
    assert results[0]["credit"] == "Ada" and results[0]["page_url"].startswith("https://www.pexels.com/")


def test_stock_errors(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    with pytest.raises(broll.StockError, match="PIXABAY_API_KEY"):
        broll.search_stock("x")
    monkeypatch.setenv("PEXELS_API_KEY", "bad")
    with pytest.raises(broll.StockError, match="rejected"):
        broll.search_pexels("x", client=mock_client(lambda r: httpx.Response(401)))
    with pytest.raises(broll.StockError, match="rate limit"):
        broll.search_pexels("x", client=mock_client(lambda r: httpx.Response(429)))
    with pytest.raises(broll.StockError, match="Invalid"):
        broll.get_stock("../../etc", "pexels")
    with pytest.raises(broll.StockError, match="Unknown stock provider"):
        broll.get_stock("1", "shutterstock")
    with pytest.raises(broll.StockError, match="non-HTTPS"):
        broll.download("http://example.com/a.mp4", settings.storage_root / "x.mp4")


def test_download_streams_to_disk(tmp_path):
    body = b"x" * 3_000_000
    dest = broll.download("https://videos.pexels.com/hd.mp4", tmp_path / "clip.mp4",
                          client=mock_client(lambda r: httpx.Response(200, content=body)))
    assert dest.read_bytes() == body
    with pytest.raises(broll.StockError, match="too large"):
        broll.download("https://videos.pexels.com/hd.mp4", tmp_path / "big.mp4",
                       client=mock_client(lambda r: httpx.Response(200, content=body)), max_bytes=1000)
    assert not (tmp_path / "big.mp4").exists()


# ---------------------------------------------------------------- schema and rendering

def test_broll_schema_and_checks():
    t = Timeline.model_validate({
        "clips": [{"id": "a", "asset_id": "v", "in": 0, "out": 10}],
        "broll": [{"id": "b1", "asset_id": "br", "start": 1, "duration": 3, "in": 5},
                  {"id": "b2", "asset_id": "ref", "start": 5, "duration": 2}],
    })
    assets = {
        "v": {"project_id": "P", "status": "ready", "kind": "video", "duration": 10.0, "has_audio": True},
        "br": {"project_id": "P", "status": "ready", "kind": "video", "duration": 6.0, "role": "broll"},
        "ref": {"project_id": "P", "status": "ready", "kind": "video", "duration": 6.0, "role": "reference"},
    }
    errors = check_assets(t, assets, "P")
    assert any("b1" in e and "needs 8.0s" in e for e in errors)
    assert any("b2" in e and "style reference" in e for e in errors)
    with pytest.raises(Exception):
        Timeline.model_validate({"clips": [{"id": "a", "asset_id": "v", "in": 0, "out": 1}],
                                 "broll": [{"id": "a", "asset_id": "x", "start": 0, "duration": 1}]})  # dup id


@pytest.mark.parametrize("motion", ["push_in", "pan_left", "none"])
def test_broll_renders_over_main_video(media_dir, multishot, tmp_path, motion):
    assets = {"v": {"id": "v", "project_id": "P", "status": "ready", **media.probe(media_dir / "landscape.mp4")},
              "s": {"id": "s", "project_id": "P", "status": "ready", **media.probe(multishot)},
              "img": {"id": "img", "project_id": "P", "status": "ready", **media.probe(media_dir / "card.png")}}
    paths = {"v": media_dir / "landscape.mp4", "s": multishot, "img": media_dir / "card.png"}
    t = Timeline.model_validate({
        "output": {"width": 320, "height": 180},
        "clips": [{"id": "a", "asset_id": "v", "in": 0, "out": 6}],
        "broll": [{"id": "b1", "asset_id": "s", "start": 1.0, "duration": 1.5, "in": 2.2, "motion": motion},
                  {"id": "b2", "asset_id": "img", "start": 4.0, "duration": 1.0, "motion": motion}],
    })
    out = tmp_path / "out.mp4"
    renderer.render(t, assets, paths, out)
    green = frame(out, 1.7, 320, 180).mean(axis=(0, 1))
    orange = frame(out, 4.5, 320, 180).mean(axis=(0, 1))
    main = frame(out, 3.0, 320, 180).mean(axis=(0, 1))
    assert green[1] > 100 and green[0] < 60 and green[2] < 60          # the green shot (source 2-4 s)
    assert orange[0] > 200 and 100 < orange[1] < 200 and orange[2] < 60  # the orange still
    assert not (main[1] > 100 and main[0] < 60)                          # back to the speaker in between
    dur = float(json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                                           str(out)], capture_output=True, text=True).stdout)["format"]["duration"])
    assert dur == pytest.approx(6.0, abs=0.1)                            # B-roll never changes the length


def test_broll_with_its_own_sound(media_dir, tmp_path):
    assets = {"q": {"id": "q", "project_id": "P", "status": "ready", **media.probe(media_dir / "portrait_silent.mp4")},
              "v": {"id": "v", "project_id": "P", "status": "ready", **media.probe(media_dir / "landscape.mp4")}}
    paths = {"q": media_dir / "portrait_silent.mp4", "v": media_dir / "landscape.mp4"}
    t = Timeline.model_validate({"output": {"width": 320, "height": 180},
                                 "clips": [{"id": "a", "asset_id": "q", "in": 0, "out": 4}],
                                 "broll": [{"id": "b", "asset_id": "v", "start": 1, "duration": 2, "volume": 1}]})
    out = tmp_path / "o.mp4"
    renderer.render(t, assets, paths, out)
    vol = lambda a, b: float(subprocess.run(  # noqa: E731
        [require_ffmpeg(), "-hide_banner", "-nostats", "-ss", str(a), "-t", str(b), "-i", str(out), "-af",
         "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr.split("mean_volume:")[1].split("dB")[0])
    assert vol(1.2, 1.5) > vol(3.2, 0.7) + 20          # the B-roll's tone only plays while it's on screen


def test_diff_describes_broll():
    base = {"clips": [{"id": "a", "asset_id": "v", "in": 0, "out": 10}]}
    one = {**base, "broll": [{"id": "b1", "asset_id": "s", "start": 1, "duration": 2}]}
    two = {**base, "broll": [{"id": "b1", "asset_id": "s", "start": 2, "duration": 2},
                             {"id": "b2", "asset_id": "s", "start": 5, "duration": 2}]}
    assert diff.describe_changes(base, one) == ["B-roll: added 1 (1 cutaway now)"]
    assert diff.describe_changes(one, two) == ["B-roll: added 1, changed 1 (2 cutaways now)"]


# ---------------------------------------------------------------- API and agent

def test_broll_upload_is_analysed_not_transcribed(client, project, multishot):
    pid, _ = project
    with open(multishot, "rb") as f:
        r = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "role": "broll", "transcribe": True},
                        files={"file": ("shots.mp4", f)}).json()
    worker.run_until_empty()
    a = client.get(f"/v2/projects/{pid}", params=U).json()["assets"]
    b = next(x for x in a if x["id"] == r["asset"]["id"])
    assert b["role"] == "broll" and b["transcript_status"] == "none" and b["analysis_status"] == "done"
    assert [s["start"] for s in b["shots"]] == [0.0, 2.0, 4.0] and b["has_proxy"]


def test_stock_api(client, project, monkeypatch, media_dir):
    pid, _ = project
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    r = client.get(f"/v2/projects/{pid}/stock/search", params={**U, "q": "coffee"})
    assert r.status_code == 409 and "PIXABAY_API_KEY" in r.json()["detail"]

    monkeypatch.setenv("PEXELS_API_KEY", "pk")
    found = {**PEXELS_VIDEO_RESULT}
    monkeypatch.setattr(broll, "search_stock", lambda q, o=None, **k: [found])
    monkeypatch.setattr(broll, "get_stock", lambda vid, provider="pixabay", **k: found)
    monkeypatch.setattr(broll, "download", lambda url, dest, **k: dest.write_bytes((media_dir / "landscape.mp4").read_bytes()) or dest)
    res = client.get(f"/v2/projects/{pid}/stock/search", params={**U, "q": "coffee"}).json()["results"]
    assert res[0]["credit"] == "Ada" and "download_url" not in res[0]          # the browser never gets file links

    added = client.post(f"/v2/projects/{pid}/stock/add", params=U, json={"provider": "pexels", "id": "123"}).json()
    assert added["asset"]["role"] == "broll" and added["credit"] == "Ada"
    assert client.post(f"/v2/projects/{pid}/stock/add", params=U, json={"id": "http://evil"}).status_code == 422
    worker.run_until_empty()
    asset = client.get(f"/v2/projects/{pid}/assets/{added['asset']['id']}", params=U).json()
    assert asset["status"] == "ready" and asset["kind"] == "video" and asset["source"] == "pexels"
    assert asset["filename"] == "Ada on Pexels (123).mp4" and asset["source_url"].startswith("https://www.pexels.com/")


PEXELS_VIDEO_RESULT = {"provider": "pexels", "id": "123", "duration": 9, "width": 1920, "height": 1080,
                       "thumbnail": "https://images.pexels.com/x.jpg", "page_url": "https://www.pexels.com/video/123/",
                       "credit": "Ada", "credit_url": "https://www.pexels.com/@ada",
                       "download_url": "https://videos.pexels.com/hd.mp4"}


def test_agent_places_broll_on_a_phrase(client, project, multishot, monkeypatch, media_dir):
    pid, vid = project
    with open(multishot, "rb") as f:
        own = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "role": "broll"},
                          files={"file": ("shots.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    monkeypatch.setenv("PEXELS_API_KEY", "pk")
    monkeypatch.setattr(broll, "search_stock", lambda q, o=None, **k: [PEXELS_VIDEO_RESULT])
    monkeypatch.setattr(broll, "get_stock", lambda vid_, provider="pixabay", **k: PEXELS_VIDEO_RESULT)
    monkeypatch.setattr(broll, "download", lambda url, dest, **k: dest.write_bytes((media_dir / "landscape.mp4").read_bytes()) or dest)

    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", snap_to_words=False, clips=[{"asset_id": vid, "in": 0, "out": 8}]),
                             call("find_phrase", text="video editor"), call("list_broll"),
                             call("search_stock_broll", query="laptop editing")]),
        AssistantTurn(None, [call("add_stock_broll", stock_id="pexels-123", description="editing on a laptop")]),
        AssistantTurn(None, [call("add_broll", asset_id=own, start=5.5, duration=2.0, source_in=2.0),
                             call("get_timeline")]),
        AssistantTurn("Added B-roll.", []),
    ])
    agent.run(p, pid, "add b-roll when I mention the video editor", history=[])
    results = tool_results(p)
    phrase = results[1]["matches"][0]
    assert phrase["start"] == pytest.approx(5.7, abs=0.01) and "video editor" in phrase["context"]
    shots = results[2]["broll"][0]["shots"]
    assert [s["start"] for s in shots] == [0.0, 2.0, 4.0]
    assert results[3]["orientation"] == "landscape" and results[3]["results"][0]["by"] == "Ada"
    assert results[3]["results"][0]["stock_id"] == "pexels-123" and results[3]["results"][0]["source"] == "Pexels"
    stock_id = results[4]["asset_id"]
    assert store.get_asset(stock_id)["role"] == "broll"
    assert results[5]["ok"] and results[5]["placed"]["in"] == 2.0
    listed = {b["id"] for b in store.list_assets(pid) if b["role"] == "broll"}
    assert listed == {own, stock_id}
    tl = store.get_timeline(pid)["data"]
    assert tl["broll"] == [{"id": "broll1", "asset_id": own, "start": 5.5, "duration": 2.0, "in": 2.0,
                            "motion": "push_in", "volume": 0.0, "fade": 0.15}]
    job = client.post(f"/v2/projects/{pid}/renders", params=U, json={"preview": True}).json()
    worker.run_until_empty()
    assert client.get(f"/v2/jobs/{job['id']}", params=U).json()["status"] == "succeeded"


# ---------------------------------------------------------------- Pixabay and AI stills

def pixabay_hit(i, w, h):
    return {"id": i, "pageURL": f"https://pixabay.com/videos/x-{i}/", "duration": 12, "tags": "coffee, cup",
            "user": "Bob", "user_id": 77,
            "videos": {"large": {"url": "", "width": 0, "height": 0, "size": 0, "thumbnail": ""},
                       "medium": {"url": f"https://cdn.pixabay.com/video/{i}_medium.mp4", "width": w, "height": h,
                                  "size": 1, "thumbnail": f"https://cdn.pixabay.com/video/{i}.jpg"}}}


def test_pixabay_search_filters_shape_and_caches(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "px")
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        return httpx.Response(200, json={"hits": [pixabay_hit(1, 1920, 1080), pixabay_hit(2, 1080, 1920)]})

    tall = broll.search_pixabay("coffee", "portrait", client=mock_client(handler))
    assert [r["id"] for r in tall] == ["2"] and tall[0]["provider"] == "pixabay"
    assert tall[0]["credit_url"] == "https://pixabay.com/users/Bob-77/"
    assert tall[0]["download_url"].endswith("2_medium.mp4")
    assert calls[0]["key"] == "px" and calls[0]["safesearch"] == "true"
    again = broll.search_pixabay("coffee", "portrait", client=mock_client(handler))
    assert again == tall and len(calls) == 1               # served from the required 24-hour cache
    square = broll.search_pixabay("coffee", "square", client=mock_client(handler))
    assert len(square) == 2                                 # nothing square: better a cropped shot than none
    one = broll.get_pixabay("2", client=mock_client(
        lambda r: httpx.Response(200, json={"hits": [pixabay_hit(2, 1080, 1920)]})))
    assert one["id"] == "2"


def test_stock_providers_follow_keys(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.setenv("PIXABAY_API_KEY", "px")
    assert broll.stock_providers() == ["pixabay"]
    monkeypatch.setenv("PEXELS_API_KEY", "pk")
    assert broll.stock_providers() == ["pixabay", "pexels"]


class StubImages:
    def __init__(self, png):
        self.png, self.kwargs = png, None

    def generate(self, **kwargs):
        import base64
        from types import SimpleNamespace as NS
        self.kwargs = kwargs
        return NS(data=[NS(b64_json=base64.b64encode(self.png).decode())])


def test_generate_still(media_dir, tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    from editor import genimage
    monkeypatch.setenv("EDITOR_IMAGE_MODEL", "gpt-image-test")
    stub = StubImages((media_dir / "card.png").read_bytes())
    out = genimage.generate("a cup of coffee", tmp_path / "x.png", "portrait", client=NS(images=stub))
    assert media.probe(out)["kind"] == "image"
    assert stub.kwargs["model"] == "gpt-image-test" and stub.kwargs["size"] == "1024x1536"
    assert stub.kwargs["prompt"].endswith("a cup of coffee") and "no text" in stub.kwargs["prompt"]


def test_agent_generates_a_still_for_broll(project, media_dir, monkeypatch):
    from editor import genimage
    pid, vid = project
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)

    def fake_generate(prompt, dest, orientation="landscape", client=None):
        dest.write_bytes((media_dir / "card.png").read_bytes())
        return dest

    monkeypatch.setattr(genimage, "generate", fake_generate)
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", snap_to_words=False, clips=[{"asset_id": vid, "in": 0, "out": 8}]),
                             call("search_stock_broll", query="elephants")]),
        AssistantTurn(None, [call("generate_broll_image", prompt="an elephant raising its trunk")]),
        AssistantTurn(None, [call("list_broll")]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "b-roll", history=[])
    results = tool_results(p)
    assert "generate_broll_image" in results[1]["error"]                # no stock source: steer to alternatives
    aid = results[2]["asset_id"]
    listed = results[3]["broll"][0]
    assert listed["asset_id"] == aid and listed["source"] == "AI-generated still" and listed["kind"] == "image"
    assert listed["shots"][0]["description"] == "an elephant raising its trunk"



def test_add_broll_stays_inside_one_shot(client, project, multishot):
    pid, vid = project
    with open(multishot, "rb") as f:
        own = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "role": "broll"},
                          files={"file": ("shots.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    store.update_asset(own, analysis={"version": 1, "described": True, "shots": [
        {"start": 0.0, "end": 2.0, "description": "red"}, {"start": 2.0, "end": 4.0, "description": "green"},
        {"start": 4.0, "end": 6.0, "description": "blue"}]})
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", snap_to_words=False, clips=[{"asset_id": vid, "in": 0, "out": 8}]),
                             call("add_broll", asset_id=own, start=3, duration=3.5, source_in=2.0),
                             call("add_broll", asset_id=own, start=1, duration=2, source_in=3.8)]),
        AssistantTurn("ok", []),
    ])
    agent.run(p, pid, "x", history=[])
    placed, too_short = tool_results(p)[1], tool_results(p)[2]
    assert placed["placed"]["duration"] == 2.0 and placed["placed"]["shows"] == "green" and "inside one shot" in placed["note"]
    assert "less than 0.5 s" in too_short["error"]
