from editor import jobs, store
from editor.config import settings


def test_claim_is_fifo_and_exclusive():
    a = jobs.enqueue("render", "u1", {"n": 1}, "p1")
    b = jobs.enqueue("ingest", "u1", {"n": 2}, "p1")

    first = jobs.claim()
    assert first["id"] == a["id"] and first["status"] == "running" and first["attempts"] == 1
    assert first["payload"] == {"n": 1}
    second = jobs.claim()
    assert second["id"] == b["id"]
    assert jobs.claim() is None


def test_claim_filters_by_kind():
    jobs.enqueue("render", "u1", {}, "p1")
    ing = jobs.enqueue("ingest", "u1", {}, "p1")
    assert jobs.claim(("ingest",))["id"] == ing["id"]
    assert jobs.claim(("ingest",)) is None


def test_progress_complete_fail_and_ownership():
    j = jobs.enqueue("render", "u1", {}, "p1")
    jobs.claim()
    jobs.heartbeat(j["id"], 0.4, "halfway-ish")
    got = jobs.get(j["id"])
    assert got["progress"] == 0.4 and got["message"] == "halfway-ish"

    jobs.complete(j["id"], {"storage_key": "secret/path.mp4", "duration": 3})
    view = jobs.public_view(jobs.get(j["id"]))
    assert view["status"] == "succeeded" and view["progress"] == 1
    assert "storage_key" not in view["result"]

    assert jobs.get(j["id"], user_id="someone_else") is None

    k = jobs.enqueue("render", "u1", {}, "p1")
    jobs.claim()
    jobs.fail(k["id"], "boom")
    assert jobs.get(k["id"])["status"] == "failed"


def _age_heartbeat(job_id: str, seconds: int) -> None:
    with store.connect() as c:
        c.execute("UPDATE editor_jobs SET heartbeat_at = datetime('now', ?) WHERE id = ?", (f"-{seconds} seconds", job_id))


def test_stale_jobs_requeued_then_failed():
    j = jobs.enqueue("render", "u1", {}, "p1")
    jobs.claim()
    _age_heartbeat(j["id"], settings.job_stale_seconds + 10)
    assert jobs.recover_stale() == 1
    assert jobs.get(j["id"])["status"] == "queued"

    jobs.claim()  # attempt 2 == max attempts
    _age_heartbeat(j["id"], settings.job_stale_seconds + 10)
    jobs.recover_stale()
    assert jobs.get(j["id"])["status"] == "failed"


def test_fresh_running_job_not_recovered():
    jobs.enqueue("render", "u1", {}, "p1")
    jobs.claim()
    assert jobs.recover_stale() == 0
