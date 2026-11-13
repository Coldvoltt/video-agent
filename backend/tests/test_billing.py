"""Plans and vid-coins: allowances, expiry, demo purchases, export charges and refunds, feature gates."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from editor import agent, billing, jobs, store, worker
from editor.llm import AssistantTurn
from editor.router import router
from test_agent import TRANSCRIPT, ScriptedProvider, call, talk_video, tool_results  # noqa: F401


@pytest.fixture(autouse=True)
def billing_on(monkeypatch):
    monkeypatch.setenv("EDITOR_BILLING", "1")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def signed_in(client, email="ada@example.com"):
    client.post("/v2/auth/signup", json={"email": email, "password": "correct horse", "name": "Ada"})
    return client.get("/v2/auth/me").json()["user"]["id"]


def set_now(monkeypatch, when):
    monkeypatch.setattr(billing, "_now", lambda: when)


def test_plans_are_public_with_fixed_naira(client, monkeypatch):
    monkeypatch.setenv("EDITOR_PRICE_PRO_NGN", "16000")
    plans = {p["id"]: p for p in client.get("/v2/plans").json()["plans"]}
    assert [plans[p]["coins"] for p in ("free", "basic", "pro")] == [2, 10, 25]
    assert plans["basic"]["price"] == {"USD": 3, "NGN": 4500} and plans["pro"]["price"] == {"USD": 10, "NGN": 16000}
    assert plans["basic"]["months"] == 1 and plans["pro"]["months"] == 3
    assert [plans[p]["max_angles"] for p in ("free", "basic", "pro")] == [1, 2, 3]
    assert plans["free"]["max_broll"] == 1 and plans["free"]["caption_styles"] == ["karaoke", "minimal"]


def test_free_coins_top_up_monthly_and_dont_stack(monkeypatch):
    set_now(monkeypatch, datetime(2026, 11, 13, tzinfo=timezone.utc))
    assert billing.state("u1")["coins"] == 2
    billing.charge("u1", 2, "Export", "j1")
    assert billing.state("u1")["coins"] == 0
    set_now(monkeypatch, datetime(2026, 11, 30, tzinfo=timezone.utc))
    assert billing.state("u1")["coins"] == 0                   # same month: nothing new
    set_now(monkeypatch, datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert billing.state("u1")["coins"] == 2                   # new month: back to 2
    set_now(monkeypatch, datetime(2027, 1, 2, tzinfo=timezone.utc))
    assert billing.state("u1")["coins"] == 2                   # unused coins don't stack
    with pytest.raises(billing.NotEnoughCoins):
        billing.charge("u1", 3, "Export", "j2")


def test_demo_purchase_expiry_and_stacking(client, monkeypatch):
    start = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
    set_now(monkeypatch, start)
    uid = signed_in(client)
    assert client.post("/v2/billing/checkout", json={"plan": "gold"}).status_code == 422
    co = client.post("/v2/billing/checkout", json={"plan": "basic", "currency": "NGN"}).json()
    assert co["amount"] == 4500 and co["currency"] == "NGN" and co["provider"] == "demo"
    declined = client.post("/v2/billing/demo/confirm", json={"reference": co["reference"], "success": False}).json()
    assert declined["status"] == "failed" and billing.state(uid)["plan"] == "free"
    assert client.post("/v2/billing/demo/confirm", json={"reference": co["reference"]}).status_code == 400  # used
    co = client.post("/v2/billing/checkout", json={"plan": "basic"}).json()
    paid = client.post("/v2/billing/demo/confirm", json={"reference": co["reference"]}).json()
    assert paid["plan"] == "basic" and paid["coins"] == 12            # 2 free + 10
    assert paid["period_end"] == "2026-02-28T12:00:00Z"                # one month (end of a short month)

    co = client.post("/v2/billing/checkout", json={"plan": "pro", "currency": "USD"}).json()
    assert co["amount"] == 10
    paid = client.post("/v2/billing/demo/confirm", json={"reference": co["reference"]}).json()
    assert paid["plan"] == "pro" and paid["coins"] == 37 and paid["period_end"] == "2026-04-30T12:00:00Z"
    r = client.post("/v2/billing/checkout", json={"plan": "basic"})
    assert r.status_code == 400 and "step down" in r.json()["detail"]

    # buying Pro again while on Pro extends from the current end
    co = client.post("/v2/billing/checkout", json={"plan": "pro"}).json()
    paid = client.post("/v2/billing/demo/confirm", json={"reference": co["reference"]}).json()
    assert paid["period_end"] == "2026-07-30T12:00:00Z" and paid["coins"] == 62

    set_now(monkeypatch, datetime(2026, 7, 30, 12, 1, tzinfo=timezone.utc))
    st = billing.state(uid)
    assert st["plan"] == "free" and st["coins"] == 2                   # paid coins end with the plan
    hist = client.get("/v2/billing").json()
    assert hist["history"][0]["reason"] == "Pro plan ended"
    assert [p["status"] for p in hist["payments"]].count("paid") == 3
    other = TestClient(client.app)
    assert other.post("/v2/billing/checkout", json={"plan": "basic"}).status_code == 401   # needs an account


@pytest.fixture
def project(client, talk_video):
    uid = signed_in(client)
    pid = client.post("/v2/projects", json={"name": "talk"}).json()["id"]
    with open(talk_video, "rb") as f:
        aid = client.post(f"/v2/projects/{pid}/assets/upload", params={"transcribe": False},
                          files={"file": ("talk.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    store.update_asset(aid, transcript=TRANSCRIPT, transcript_status="done")
    return uid, pid, aid


def test_exports_cost_coins_and_failures_refund(client, project):
    uid, pid, aid = project
    client.put(f"/v2/projects/{pid}/timeline", json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}})
    prev = client.post(f"/v2/projects/{pid}/renders", json={"preview": True})
    assert prev.status_code == 200 and billing.state(uid)["coins"] == 2       # previews are free
    worker.run_until_empty()
    full = client.post(f"/v2/projects/{pid}/renders", json={"preview": False})
    assert full.status_code == 200 and billing.state(uid)["coins"] == 1       # 8 s = 1 coin
    jobs.fail(full.json()["id"], "boom")                                     # pretend it failed
    jobs.fail(full.json()["id"], "boom again")                               # refunds once only
    assert billing.state(uid)["coins"] == 2
    client.post(f"/v2/projects/{pid}/renders", json={"preview": False})
    client.post(f"/v2/projects/{pid}/renders", json={"preview": False})
    r = client.post(f"/v2/projects/{pid}/renders", json={"preview": False})
    assert r.status_code == 402 and "needs 1 vid-coin and you have 0" in r.json()["detail"]["message"]
    assert r.json()["detail"]["upgrade"] == "basic"
    assert billing.export_cost(60) == 1 and billing.export_cost(60.5) == 2 and billing.export_cost(0) == 1


def test_free_plan_limits_on_save(client, project):
    uid, pid, aid = project
    base = {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}
    one = {**base, "broll": [{"id": "b1", "asset_id": aid, "start": 2, "duration": 1.5, "in": 0}]}
    two = {**base, "broll": [*one["broll"], {"id": "b2", "asset_id": aid, "start": 5, "duration": 1.5, "in": 0}]}
    assert client.put(f"/v2/projects/{pid}/timeline", json={"timeline": one}).status_code == 200
    r = client.put(f"/v2/projects/{pid}/timeline", json={"timeline": two})
    assert r.status_code == 402 and "1 B-roll" in r.json()["detail"]["message"]
    assert client.put(f"/v2/projects/{pid}/timeline", json={"timeline": {**base, "captions": {"style": "minimal"}}}).status_code == 200
    r = client.put(f"/v2/projects/{pid}/timeline", json={"timeline": {**base, "captions": {"style": "pop"}}})
    assert r.status_code == 402 and "Basic" in r.json()["detail"]["message"]
    r = client.post(f"/v2/projects/{pid}/captions", json={"settings": {"font": "bangers"}})
    assert r.status_code == 402
    r = client.put(f"/v2/projects/{pid}/timeline", json={"timeline": {**base, "clips": [
        {"id": "c1", "asset_id": aid, "in": 0, "out": 8, "background": {"replace_with": "blur"}}]}})
    assert r.status_code == 402 and r.json()["detail"]["upgrade"] == "pro"
    assert client.post(f"/v2/projects/{pid}/broll/generate", json={"prompt": "sunset"}).status_code == 402
    assert client.post(f"/v2/projects/{pid}/multicam", json={"asset_ids": [aid, aid + "x"]}).status_code == 402

    # Basic unlocks styles, fonts and B-roll, but not Pro features
    co = client.post("/v2/billing/checkout", json={"plan": "basic"}).json()
    client.post("/v2/billing/demo/confirm", json={"reference": co["reference"]})
    assert client.put(f"/v2/projects/{pid}/timeline", json={"timeline": {**two, "captions": {
        "style": "pop", "font": "bangers", "emphasis": {"words": ["editor"]}}}}).status_code == 200
    assert client.post(f"/v2/projects/{pid}/broll/generate", json={"prompt": "sunset"}).status_code == 402


def test_agent_respects_the_plan(client, project):
    uid, pid, aid = project
    client.put(f"/v2/projects/{pid}/timeline", json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 8}]}})
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_captions", style="pop"),
                             call("set_captions", style="karaoke"),
                             call("create_multicam", asset_ids=[aid, "x"]),
                             call("generate_broll_image", prompt="a sunset")]),
        AssistantTurn("Done.", []),
    ])
    agent.run(p, pid, "pop captions and a sunset", history=[])
    r = tool_results(p)
    assert any("Plan limit" in e for e in r[0]["errors"])
    assert r[1]["ok"]
    assert "Plan limit" in r[2]["error"] and "Plan limit" in r[3]["error"]
    assert store.get_timeline(pid)["data"]["captions"]["style"] == "karaoke"
