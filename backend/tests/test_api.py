"""End-to-end through the HTTP API and the worker, using the editor router alone
(the legacy app needs an OpenAI key just to import)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from editor import worker
from editor.router import router

U = {"user_id": "creator_1"}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def upload(client, pid, path, transcribe=True):
    with open(path, "rb") as f:
        r = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": transcribe},
                        files={"file": (path.name, f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def test_full_flow_upload_timeline_render_download(client, media_dir):
    pid = client.post("/v2/projects", params=U, json={"name": "My first short"}).json()["id"]

    up = upload(client, pid, media_dir / "landscape.mp4")
    img = upload(client, pid, media_dir / "card.png", transcribe=False)
    assert up["job"]["status"] == "queued"
    assert worker.run_until_empty() == 2

    project = client.get(f"/v2/projects/{pid}", params=U).json()
    by_id = {a["id"]: a for a in project["assets"]}
    vid = by_id[up["asset"]["id"]]
    assert vid["status"] == "ready" and vid["kind"] == "video" and vid["has_audio"]
    # No API key in tests: transcription is skipped with a reason, the asset is still usable.
    assert vid["transcript_status"] == "skipped" and "OPENAI_API_KEY" in vid["transcript_error"]
    assert by_id[img["asset"]["id"]]["kind"] == "image"
    assert "storage_key" not in vid

    timeline = {"output": {"width": 540, "height": 960, "fit": "cover"}, "clips": [
        {"id": "hook", "asset_id": vid["id"], "in": 2, "out": 3.5},
        {"id": "card", "asset_id": img["asset"]["id"], "in": 0, "out": 1},
    ]}
    r = client.put(f"/v2/projects/{pid}/timeline", params=U, json={"timeline": timeline, "note": "first cut"})
    assert r.status_code == 200 and r.json() == {"version": 1, "duration": 2.5, "warnings": [], "preview_job_id": None}

    job = client.post(f"/v2/projects/{pid}/renders", params=U, json={"preview": False}).json()
    assert client.get(f"/v2/jobs/{job['id']}/output", params=U).status_code == 409  # not done yet
    worker.run_until_empty()

    done = client.get(f"/v2/jobs/{job['id']}", params=U).json()
    assert done["status"] == "succeeded", done
    assert done["result"]["width"] == 540 and done["result"]["timeline_version"] == 1

    dl = client.get(f"/v2/jobs/{job['id']}/output", params=U)
    assert dl.status_code == 200 and dl.headers["content-type"] == "video/mp4" and len(dl.content) > 1000

    # another user can't see any of it
    other = {"user_id": "someone_else"}
    assert client.get(f"/v2/projects/{pid}", params=other).status_code == 404
    assert client.get(f"/v2/jobs/{job['id']}", params=other).status_code == 404
    assert client.get(f"/v2/jobs/{job['id']}/output", params=other).status_code == 404


def test_timeline_validation_and_versioning(client, media_dir):
    pid = client.post("/v2/projects", params=U, json={"name": "p"}).json()["id"]
    up = upload(client, pid, media_dir / "portrait_silent.mp4", transcribe=False)

    bad = client.put(f"/v2/projects/{pid}/timeline", params=U,
                     json={"timeline": {"clips": [{"id": "x", "asset_id": "a", "in": 2, "out": 1}]}})
    assert bad.status_code == 422

    # Saved while the asset is still processing -> warning, and rendering is refused.
    t = {"clips": [{"id": "x", "asset_id": up["asset"]["id"], "in": 0, "out": 10}]}
    r = client.put(f"/v2/projects/{pid}/timeline", params=U, json={"timeline": t}).json()
    assert r["version"] == 1 and "not ready" in r["warnings"][0]
    assert client.post(f"/v2/projects/{pid}/renders", params=U, json={}).status_code == 409

    worker.run_until_empty()
    # Now ready, but 10 s is past the 4 s source.
    assert "past the end" in client.post(f"/v2/projects/{pid}/renders", params=U, json={}).json()["detail"][0]

    t["clips"][0]["out"] = 3
    assert client.put(f"/v2/projects/{pid}/timeline", params=U, json={"timeline": t}).json()["version"] == 2
    assert client.get(f"/v2/projects/{pid}/timeline", params={**U, "version": 1}).json()["data"]["clips"][0]["out"] == 10
    assert client.get(f"/v2/projects/{pid}/timeline", params=U).json()["version"] == 2


def test_upload_and_url_guards(client, tmp_path):
    pid = client.post("/v2/projects", params=U, json={"name": "p"}).json()["id"]
    exe = tmp_path / "evil.exe"
    exe.write_bytes(b"MZ")
    with open(exe, "rb") as f:
        r = client.post(f"/v2/projects/{pid}/assets/upload", params=U, files={"file": ("evil.exe", f)})
    assert r.status_code == 400

    r = client.post(f"/v2/projects/{pid}/assets/url", params=U, json={"url": "http://169.254.169.254/latest"})
    assert r.status_code == 400
    assert client.post("/v2/projects", params={"user_id": "../etc"}, json={"name": "x"}).status_code == 422


def test_corrupt_upload_marks_asset_failed(client, tmp_path):
    pid = client.post("/v2/projects", params=U, json={"name": "p"}).json()["id"]
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video at all" * 100)
    up = upload(client, pid, junk)
    worker.run_until_empty()
    asset = client.get(f"/v2/projects/{pid}/assets/{up['asset']['id']}", params=U).json()
    job = client.get(f"/v2/jobs/{up['job']['id']}", params=U).json()
    assert asset["status"] == "failed" and job["status"] == "failed" and job["error"]


def test_restore_version_and_clip_text(client, media_dir):
    from editor import store

    pid = client.post("/v2/projects", params=U, json={"name": "p"}).json()["id"]
    aid = upload(client, pid, media_dir / "landscape.mp4", transcribe=False)["asset"]["id"]
    worker.run_until_empty()
    store.update_asset(aid, transcript={"segments": [{"start": 0, "end": 2, "text": "Hello there."},
                                                     {"start": 2, "end": 6, "text": "Second line."}]},
                       transcript_status="done")

    for out in (1.5, 4.0):
        client.put(f"/v2/projects/{pid}/timeline", params=U,
                   json={"timeline": {"clips": [{"id": "a", "asset_id": aid, "in": 0, "out": out}]}})
    r = client.post(f"/v2/projects/{pid}/timeline/restore", params=U, json={"version": 1})
    assert r.json() == {"version": 3, "restored_from": 1}

    project = client.get(f"/v2/projects/{pid}", params=U).json()
    assert project["timeline"]["version"] == 3 and project["timeline"]["data"]["clips"][0]["out"] == 1.5
    assert project["timeline"]["clip_text"] == {"a": "Hello there."}
    assert [v["note"] for v in project["timeline_versions"]][0] == "Restored version 1"
    assert client.get(f"/v2/projects/{pid}/timeline", params={**U, "version": 2}).json()["clip_text"]["a"] == \
        "Hello there. Second line."
    assert client.post(f"/v2/projects/{pid}/timeline/restore", params=U, json={"version": 9}).status_code == 404


def test_app_health_and_routes():
    import api

    h = TestClient(api.app).get("/health").json()
    assert h["status"] == "healthy" and isinstance(h["openai_key"], bool) and h["ffmpeg"] is True
    paths = set(api.app.openapi()["paths"])
    assert "/v2/projects/{project_id}/chat" in paths and "/query" not in paths
