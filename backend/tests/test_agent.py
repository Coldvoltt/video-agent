"""Agent loop, tools and chat flow with a scripted stand-in for the model."""

import json
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from editor import agent, analysis, config, store, worker
from editor.llm import AssistantTurn, ToolCall
from editor.router import router

U = {"user_id": "creator_1"}


class ScriptedProvider:
    """Plays back a fixed list of turns and records what the agent sent each time."""
    name, model = "fake", "scripted"

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    def complete(self, system, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.turns.pop(0)


def call(name, n=[0], **args):
    n[0] += 1
    return ToolCall(id=f"call_{n[0]}", name=name, arguments=args, raw_arguments=json.dumps(args))


def tool_results(provider) -> list[dict]:
    return [json.loads(m["content"]) for m in provider.seen[-1] if m["role"] == "tool"]


@pytest.fixture(scope="session")
def talk_video(tmp_path_factory):
    """8 s video: tone 0-2 s, silence 2-4 s, tone 4-8 s."""
    d = tmp_path_factory.mktemp("talk")
    out = d / "talk.mp4"
    subprocess.run([config.require_ffmpeg(), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=8",
                    "-f", "lavfi", "-i", "aevalsrc='if(between(t,2,4),0,0.5*sin(440*2*PI*t))':s=48000:d=8",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


TRANSCRIPT = {
    "language": "english", "duration": 8.0,
    "segments": [{"start": 0.0, "end": 2.0, "text": "Hello and welcome."},
                 {"start": 4.0, "end": 8.0, "text": "Um, today we build a video editor."}],
    "words": [{"word": "Hello", "start": 0.0, "end": 0.6}, {"word": "and", "start": 0.6, "end": 0.9},
              {"word": "welcome.", "start": 0.9, "end": 1.9}, {"word": "Um,", "start": 4.0, "end": 4.5},
              {"word": "today", "start": 4.6, "end": 5.0}, {"word": "we", "start": 5.0, "end": 5.2},
              {"word": "build", "start": 5.2, "end": 5.6}, {"word": "a", "start": 5.6, "end": 5.7},
              {"word": "video", "start": 5.7, "end": 6.2}, {"word": "editor.", "start": 6.2, "end": 7.9}],
    "full_text": "Hello and welcome. Um, today we build a video editor.",
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def project(client, talk_video):
    pid = client.post("/v2/projects", params=U, json={"name": "talk"}).json()["id"]
    with open(talk_video, "rb") as f:
        aid = client.post(f"/v2/projects/{pid}/assets/upload", params={**U, "transcribe": False},
                          files={"file": ("talk.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    store.update_asset(aid, transcript=TRANSCRIPT, transcript_status="done")
    return pid, aid


def test_detect_silences(talk_video):
    sil = analysis.detect_silences(talk_video, 8.0, min_silence=0.5)
    assert len(sil) == 1
    assert sil[0][0] == pytest.approx(2.0, abs=0.1) and sil[0][1] == pytest.approx(4.0, abs=0.1)


def test_agent_builds_short_and_tightens(project):
    pid, aid = project
    p = ScriptedProvider([
        AssistantTurn(None, [call("list_assets"), call("get_transcript", asset_id=aid, include_words=True)]),
        # cut points land mid-word; they should snap to word edges
        AssistantTurn(None, [call("set_timeline", aspect="9:16",
                                  clips=[{"asset_id": aid, "in": 0.3, "out": 7.0}])]),
        AssistantTurn(None, [call("tighten_pacing")]),
        AssistantTurn("Made a vertical cut and removed the pause and the 'um'.", []),
    ])
    res = agent.run(p, pid, "make a tight vertical short", history=[])

    assert res.reply.startswith("Made a vertical cut") and res.timeline_version == 1
    assert [t["tool"] for t in res.trace] == ["list_assets", "get_transcript", "set_timeline", "tighten_pacing"]
    assert all(t["ok"] for t in res.trace)

    tl = store.get_timeline(pid)["data"]
    assert (tl["output"]["width"], tl["output"]["height"], tl["output"]["fit"]) == (1080, 1920, "cover")
    clips = [(c["in"], c["out"]) for c in tl["clips"]]
    assert clips[0][0] == 0.0 and clips[-1][1] == pytest.approx(7.9)     # snapped to word edges
    # the 2-4 s silence (minus padding) and the "um" at 4.0-4.5 are gone
    assert not any(c_in < 3.0 < c_out for c_in, c_out in clips)
    assert not any(c_in < 4.25 < c_out for c_in, c_out in clips)
    # the first message carried the project state
    assert "[Project state]" in p.seen[0][-1]["content"]


def test_agent_recovers_from_bad_tool_input(project):
    pid, aid = project
    bad_json = ToolCall(id="x1", name="set_timeline", arguments=None, raw_arguments="{oops")
    p = ScriptedProvider([
        AssistantTurn(None, [call("set_timeline", clips=[{"asset_id": "nope", "in": 0, "out": 1}]),
                             call("set_timeline", clips=[{"asset_id": aid, "in": 0, "out": 99}], snap_to_words=False),
                             bad_json, call("no_such_tool")]),
        AssistantTurn("Sorry, I could not do that.", []),
    ])
    res = agent.run(p, pid, "do something odd", history=[])
    results = tool_results(p)
    assert "Unknown asset_id" in results[0]["error"]
    assert results[1]["ok"] is True and results[1]["duration_seconds"] == pytest.approx(8.0, abs=0.1)  # clamped to source
    assert "not valid JSON" in results[2]["error"] and "Unknown tool" in results[3]["error"]
    assert [t["ok"] for t in res.trace] == [False, True, False, False]
    assert res.timeline_version == 1


def test_agent_without_changes_saves_nothing(project):
    pid, _ = project
    p = ScriptedProvider([AssistantTurn(None, [call("get_timeline")]), AssistantTurn("Nothing to do yet.", [])])
    res = agent.run(p, pid, "what's on my timeline?", history=[])
    assert res.timeline_version is None and store.get_timeline(pid) is None


def test_chat_endpoint_runs_agent_and_renders_preview(client, project, monkeypatch):
    pid, aid = project
    p = ScriptedProvider([
        AssistantTurn(None, [call("cut_ranges", ranges=[{"asset_id": aid, "start": 0, "end": 1}])]),
        AssistantTurn(None, [call("set_timeline", clips=[{"asset_id": aid, "in": 4.6, "out": 7.9}], aspect="1:1")]),
        AssistantTurn("Kept the line about building the editor, square format.", []),
    ])
    monkeypatch.setattr("editor.llm.get_provider", lambda: p)

    r = client.post(f"/v2/projects/{pid}/chat", params=U, json={"message": "keep only the editor line, square"})
    assert r.status_code == 200
    # a second request while the first is queued is refused
    assert client.post(f"/v2/projects/{pid}/chat", params=U, json={"message": "again"}).status_code == 409

    worker.run_until_empty()   # agent job, then the preview render it queued

    job = client.get(f"/v2/jobs/{r.json()['job']['id']}", params=U).json()
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert result["timeline_version"] == 1 and result["reply"].startswith("Kept the line")

    preview = client.get(f"/v2/jobs/{result['preview_job_id']}", params=U).json()
    assert preview["status"] == "succeeded" and preview["result"]["preview"] is True
    assert (preview["result"]["width"], preview["result"]["height"]) == (854, 854)

    msgs = client.get(f"/v2/projects/{pid}/messages", params=U).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["timeline_version"] == 1 and msgs[1]["preview_job_id"] == result["preview_job_id"]

    # history is passed on the next turn
    p2 = ScriptedProvider([AssistantTurn("Sure.", [])])
    monkeypatch.setattr("editor.llm.get_provider", lambda: p2)
    client.post(f"/v2/projects/{pid}/chat", params=U, json={"message": "thanks"})
    worker.run_until_empty()
    assert [m["role"] for m in p2.seen[0]] == ["user", "assistant", "user"]


def test_chat_without_api_key_fails_cleanly(client, project):
    pid, _ = project
    r = client.post(f"/v2/projects/{pid}/chat", params=U, json={"message": "cut the ums"})
    worker.run_until_empty()
    job = client.get(f"/v2/jobs/{r.json()['job']['id']}", params=U).json()
    assert job["status"] == "failed" and "OPENAI_API_KEY" in job["error"]
    msgs = client.get(f"/v2/projects/{pid}/messages", params=U).json()["messages"]
    assert msgs[-1]["role"] == "assistant" and "OPENAI_API_KEY" in msgs[-1]["content"]
