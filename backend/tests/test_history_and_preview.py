"""Phase 5: undo/redo, plain-language version changes, proxies for the live preview, hand edits."""

import pytest

from editor import diff, store, worker
from test_agent import TRANSCRIPT, U, client, project, talk_video  # noqa: F401
from test_render import ffprobe


def put(client, pid, timeline, **extra):
    r = client.put(f"/v2/projects/{pid}/timeline", params=U, json={"timeline": timeline, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def clip(cid, aid, a, b, **kw):
    return {"id": cid, "asset_id": aid, "in": a, "out": b, **kw}


def test_undo_redo_walks_history_without_losing_versions(client, project):
    pid, aid = project
    v1 = {"clips": [clip("a", aid, 0, 8)]}
    v2 = {"clips": [clip("a", aid, 0, 4)]}
    v3 = {"clips": [clip("a", aid, 0, 4)], "captions": {"style": "pop"}}
    for t in (v1, v2, v3):
        put(client, pid, t)
    state = client.get(f"/v2/projects/{pid}", params=U).json()["history"]
    assert state == {"can_undo": True, "can_redo": False}

    current = lambda: client.get(f"/v2/projects/{pid}/timeline", params=U).json()  # noqa: E731
    u1 = client.post(f"/v2/projects/{pid}/timeline/undo", params=U).json()
    assert u1["version"] == 4 and u1["restored_from"] == 2 and u1["can_redo"] is True
    assert "captions" not in current()["data"]
    u2 = client.post(f"/v2/projects/{pid}/timeline/undo", params=U).json()
    assert u2["restored_from"] == 1 and current()["data"]["clips"][0]["out"] == 8
    assert client.post(f"/v2/projects/{pid}/timeline/undo", params=U).status_code == 409   # nothing left

    r1 = client.post(f"/v2/projects/{pid}/timeline/redo", params=U).json()
    assert current()["data"]["clips"][0]["out"] == 4 and "captions" not in current()["data"]
    r2 = client.post(f"/v2/projects/{pid}/timeline/redo", params=U).json()
    assert current()["data"]["captions"]["style"] == "pop" and r2["can_redo"] is False
    assert client.post(f"/v2/projects/{pid}/timeline/redo", params=U).status_code == 409
    assert r1["preview_job_id"] and r2["preview_job_id"]                        # previews queued

    # A new edit after undoing clears redo, like every editor.
    client.post(f"/v2/projects/{pid}/timeline/undo", params=U)
    put(client, pid, {"clips": [clip("a", aid, 1, 3)]})
    assert client.get(f"/v2/projects/{pid}", params=U).json()["history"]["can_redo"] is False
    notes = [v["note"] for v in client.get(f"/v2/projects/{pid}", params=U).json()["timeline_versions"]]
    assert notes[1].startswith("Undo: back to version")
    assert len(notes) == 9                                                        # nothing overwritten


def test_agent_and_caption_edits_are_undoable(client, project):
    pid, aid = project
    put(client, pid, {"clips": [clip("a", aid, 0, 8)], "captions": {"style": "boxed"}})
    client.post(f"/v2/projects/{pid}/captions", params=U, json={"settings": {"style": "pop"}, "render_preview": False})
    assert client.get(f"/v2/projects/{pid}/captions", params=U).json()["settings"]["style"] == "pop"
    client.post(f"/v2/projects/{pid}/timeline/undo", params=U)
    assert client.get(f"/v2/projects/{pid}/captions", params=U).json()["settings"]["style"] == "boxed"


def test_changes_in_plain_words(client, project):
    pid, aid = project
    put(client, pid, {"clips": [clip("a", aid, 0, 8)]})
    put(client, pid, {"output": {"width": 1080, "height": 1920, "fit": "cover"},
                      "clips": [clip("a", aid, 0, 4), clip("b", aid, 5, 7, zoom=1.2,
                                                           transition_in={"type": "fade", "duration": 0.3})],
                      "captions": {"style": "karaoke"}, "grade": {"preset": "warm"},
                      "text": [{"id": "t1", "text": "Hello", "start": 0, "end": 1}]})
    first = client.get(f"/v2/projects/{pid}/timeline/changes", params={**U, "version": 1}).json()
    assert first["changes"] == ["Started the edit: 1 clip, 8.0s"]
    got = client.get(f"/v2/projects/{pid}/timeline/changes", params=U).json()
    assert got["version"] == 2 and got["against"] == 1
    c = got["changes"]
    assert "Format 1920×1080 → 1080×1920" in c and "Added 1 clip" in c and "Trimmed 1 clip" in c
    assert "Transitions: fade" in c and "Captions on (karaoke, bottom)" in c and "Colour preset: warm" in c
    assert "Added text “Hello”" in c and any(x.startswith("Length 8.0s →") for x in c)


def test_diff_details():
    base = {"clips": [clip("a", "x", 0, 4), clip("b", "x", 5, 9)], "captions": {"style": "pop"},
            "audio": [{"id": "m", "asset_id": "song", "loop": True}]}
    same = diff.describe_changes(base, base)
    assert same == []
    swapped = {**base, "clips": [base["clips"][1], base["clips"][0]]}
    assert diff.describe_changes(base, swapped) == ["Reordered clips"]
    recut = {**base, "clips": [clip(f"a-{i}", "x", i, i + 1) for i in range(5)]}
    assert diff.describe_changes(base, recut)[0] == "Re-cut into 5 clips (was 2)"
    fixed = {**base, "captions": {"style": "pop", "corrections": [
        {"asset_id": "x", "start": 0, "end": 1, "text": "hi"}]}, "audio": []}
    assert diff.describe_changes(base, fixed, {"song": "song.mp3"}) == [
        "Edited 1 caption line", "Removed audio “song.mp3”"]


def test_ingest_makes_a_browser_proxy(client, media_dir):
    pid = client.post("/v2/projects", params=U, json={"name": "p"}).json()["id"]
    with open(media_dir / "phone_portrait.mp4", "rb") as f:
        aid = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": False},
                          files={"file": ("phone.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    asset = client.get(f"/v2/projects/{pid}/assets/{aid}", params=U).json()
    assert asset["has_proxy"] is True and "proxy_key" not in asset
    r = client.get(f"/v2/projects/{pid}/assets/{aid}/file", params={**U, "proxy": True})
    assert r.status_code == 200
    proxy_path = store.get_asset(aid)["proxy_key"]
    from editor import storage
    _, v, a = ffprobe(storage.path(proxy_path))
    assert v["codec_name"] == "h264" and (v["width"], v["height"]) == (270, 480)   # rotation applied
    assert not v.get("side_data_list")                                            # no rotation flag left


def test_hand_edit_with_preview(client, project):
    pid, aid = project
    r = put(client, pid, {"clips": [clip("a", aid, 0, 3), clip("b", aid, 4, 7)]}, note="Trimmed by hand",
            render_preview=True)
    assert r["preview_job_id"]
    worker.run_until_empty()
    assert client.get(f"/v2/jobs/{r['preview_job_id']}", params=U).json()["status"] == "succeeded"
    assert client.get(f"/v2/projects/{pid}", params=U).json()["timeline_versions"][0]["note"] == "Trimmed by hand"


def test_caption_lines_for_unsaved_timeline(client, project):
    pid, aid = project
    draft = {"clips": [clip("b", aid, 4.0, 7.9)], "captions": {"style": "minimal"}}
    lines = client.post(f"/v2/projects/{pid}/captions/preview", params=U, json={"timeline": draft}).json()["lines"]
    # The clip opens on "Um," (4.0-4.5 s), which captions skip, so the first line starts at 0.6 s.
    assert lines[0]["start"] == 0.6 and lines[0]["text"].lower().startswith("today")
    assert client.get(f"/v2/projects/{pid}", params=U).json()["timeline"] is None          # nothing saved
    other = client.post("/v2/projects", params={"user_id": "intruder"}, json={"name": "x"}).json()["id"]
    leaked = client.post(f"/v2/projects/{other}/captions/preview", params={"user_id": "intruder"},
                         json={"timeline": draft}).json()["lines"]
    assert leaked == []                                         # can't read another project's transcript


def test_split_is_described_as_a_split():
    before = {"clips": [clip("a", "x", 0, 6), clip("b", "x", 8, 9)]}
    after = {"clips": [clip("a", "x", 0, 2.5), clip("a-2", "x", 2.5, 6), clip("b", "x", 8, 9)]}
    assert diff.describe_changes(before, after) == ["Split 1 clip"]


def test_music_is_not_transcribed(client, media_dir):
    pid = client.post("/v2/projects", params=U, json={"name": "p"}).json()["id"]
    with open(media_dir / "tone.mp3", "rb") as f:
        aid = client.post(f"/v2/projects/{pid}/assets/upload", params=U,
                          files={"file": ("music.mp3", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    a = client.get(f"/v2/projects/{pid}/assets/{aid}", params=U).json()
    assert a["transcript_status"] == "skipped" and "Music" in a["transcript_error"]


def test_missing_proxies_are_backfilled_once(client, media_dir):
    from editor.tasks import queue_missing_proxies

    pid = client.post("/v2/projects", params=U, json={"name": "p"}).json()["id"]
    with open(media_dir / "landscape.mp4", "rb") as f:
        aid = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": False},
                          files={"file": ("old.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    store.update_asset(aid, proxy_key=None)            # as if uploaded before proxies existed
    assert queue_missing_proxies() == 1
    assert queue_missing_proxies() == 0                # not queued twice
    worker.run_until_empty()
    assert client.get(f"/v2/projects/{pid}/assets/{aid}", params=U).json()["has_proxy"] is True
