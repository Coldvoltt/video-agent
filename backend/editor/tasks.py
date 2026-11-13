"""
Job handlers run by the worker: ingest (download / probe / transcribe / index) and render.
Each handler gets the job dict and a `progress(fraction, message)` callback and
returns a JSON-serialisable result.
"""

import shutil
from pathlib import Path
from typing import Callable

from . import matte, media, multicam, renderer, storage, store
from .timeline import Timeline, check_assets

Progress = Callable[[float, str | None], None]


class JobError(RuntimeError):
    """Expected failure with a message safe to show the user."""


def _asset_path(asset: dict) -> Path:
    if not asset.get("storage_key"):
        raise JobError(f"Asset {asset['id']} has no stored file")
    return storage.path(asset["storage_key"])


# ---------------------------------------------------------------- ingest

def ingest(job: dict, progress: Progress) -> dict:
    payload = job["payload"]
    asset = store.get_asset(payload["asset_id"])
    if asset is None:
        raise JobError("Asset was deleted")
    store.update_asset(asset["id"], status="processing", error=None)

    try:
        if asset["source"] == "youtube":
            progress(0.0, "Downloading video")
            out_dir = storage.dir_path(storage.asset_dir_key(asset["user_id"], asset["project_id"]))
            dl = media.download_youtube(
                asset["source_url"], out_dir, asset["id"],
                on_progress=lambda f, m: progress(0.5 * f, m),
            )
            key = f"{storage.asset_dir_key(asset['user_id'], asset['project_id'])}/{dl['path'].name}"
            store.update_asset(asset["id"], storage_key=key, filename=dl["title"])
            asset = store.get_asset(asset["id"])

        progress(0.5, "Reading media info")
        path = _asset_path(asset)
        if path.suffix.lower() in media.LUT_EXTS:
            info = media.inspect_lut(path)
        elif path.suffix.lower() in media.LOTTIE_EXTS:
            from . import stickers

            info = stickers.inspect_lottie(path)
        else:
            info = media.probe(path)
        store.update_asset(asset["id"], **info)
    except Exception as exc:
        store.update_asset(asset["id"], status="failed", error=str(exc)[-1000:])
        raise

    if info["kind"] == "video" and asset.get("role", "footage") in ("footage", "broll"):
        progress(0.52, "Preparing a preview copy")
        try:
            proxy_key = storage.asset_key(asset["user_id"], asset["project_id"], f"{asset['id']}.proxy", ".mp4")
            media.make_proxy(path, storage.path(proxy_key))
            store.update_asset(asset["id"], proxy_key=proxy_key)
        except Exception:
            pass   # the live preview falls back to the original file

    # The asset is usable for editing even if transcription fails.
    store.update_asset(asset["id"], status="ready")
    result = {"asset_id": asset["id"], **info, "transcript_status": "none"}

    if asset["transcript_status"] == "pending":
        if not info["has_audio"]:
            store.update_asset(asset["id"], transcript_status="skipped", transcript_error="No audio track")
            result["transcript_status"] = "skipped"
        elif info["kind"] == "audio":
            # Music and sound effects: never clips, nothing to caption or search.
            store.update_asset(asset["id"], transcript_status="skipped",
                               transcript_error="Music and sound effects aren't transcribed")
            result["transcript_status"] = "skipped"
        else:
            result["transcript_status"] = _transcribe_and_index(asset, path, info, payload.get("language"), progress)

    if asset.get("role") == "broll":
        if info["kind"] == "video":
            result["analysis_status"] = _describe_broll(asset, path, info, progress)
        else:
            store.update_asset(asset["id"], analysis_status="none")   # stills need no shot list

    if asset.get("role") == "reference":
        result["analysis_status"] = _analyse_reference(asset, path, info, progress)

    progress(1.0, "Done")
    return result


def _describe_broll(asset: dict, path: Path, info: dict, progress: Progress) -> str:
    """Split a B-roll clip into shots and describe each, so the agent can match shots to speech."""
    from . import broll
    from .config import settings

    progress(0.7, "Finding and describing shots")
    vision = None
    if settings.openai_api_key:
        from .llm import get_provider

        provider = get_provider()
        if hasattr(provider, "vision_json"):
            vision = lambda images: provider.vision_json(broll.VISION_SYSTEM, broll.VISION_PROMPT, images)  # noqa: E731
    try:
        store.update_asset(asset["id"], analysis=broll.describe_clip(path, info, vision), analysis_status="done")
        return "done"
    except Exception as exc:
        store.update_asset(asset["id"], analysis_status="failed", error=f"Shot analysis failed: {exc}"[:1000])
        return "failed"


def stock_import(job: dict, progress: Progress) -> dict:
    """Download a stock clip into the project as B-roll, then ingest it like an upload."""
    from . import broll

    payload = job["payload"]
    asset = store.get_asset(payload["asset_id"])
    if asset is None:
        raise JobError("Asset was deleted")
    store.update_asset(asset["id"], status="processing", error=None)
    progress(0.05, "Downloading stock clip")
    try:
        key = storage.asset_key(asset["user_id"], asset["project_id"], asset["id"], ".mp4")
        broll.download(payload["download_url"], storage.path(key))
        store.update_asset(asset["id"], storage_key=key)
    except Exception as exc:
        store.update_asset(asset["id"], status="failed", error=str(exc)[:1000])
        raise JobError(str(exc)) from exc
    return ingest({**job, "payload": {"asset_id": asset["id"]}}, lambda f, m: progress(0.2 + 0.8 * f, m))


def generate_image(job: dict, progress: Progress) -> dict:
    """AI still for B-roll: generate, then ingest like an upload (with the prompt as its description)."""
    from . import genimage

    payload = job["payload"]
    asset = store.get_asset(payload["asset_id"])
    if asset is None:
        raise JobError("Asset was deleted")
    store.update_asset(asset["id"], status="processing", error=None)
    progress(0.1, "Generating the image")
    try:
        key = storage.asset_key(asset["user_id"], asset["project_id"], asset["id"], ".png")
        genimage.generate(payload["prompt"], storage.path(key), payload.get("orientation") or "landscape")
        info = media.probe(storage.path(key))
    except Exception as exc:
        store.update_asset(asset["id"], status="failed", error=str(exc)[:1000])
        raise JobError(str(exc)) from exc
    store.update_asset(asset["id"], storage_key=key, status="ready", analysis_status="done",
                       analysis={"version": 1, "described": True,
                                 "shots": [{"start": 0.0, "end": 0.0, "description": payload["prompt"][:200]}]},
                       **info)
    return {"asset_id": asset["id"], **info}


def _analyse_reference(asset: dict, path: Path, info: dict, progress: Progress) -> str:
    """Build the style profile of a reference video. The asset stays usable if this fails."""
    from . import style
    from .config import settings

    if info.get("kind") != "video":
        store.update_asset(asset["id"], analysis_status="failed", error="References must be videos")
        return "failed"
    vision = None
    if settings.openai_api_key:
        from .llm import get_provider

        provider = get_provider()
        if hasattr(provider, "vision_json"):
            vision = lambda images: provider.vision_json(style.VISION_SYSTEM, style.VISION_PROMPT, images)  # noqa: E731
    transcript = (store.get_asset(asset["id"], with_transcript=True) or {}).get("transcript")
    try:
        profile = style.build_profile(path, info, transcript, vision,
                                      progress=lambda f, m: progress(0.6 + 0.38 * f, m))
        profile["summary"] = style.summarize(profile)
        store.update_asset(asset["id"], analysis=profile, analysis_status="done")
        return "done"
    except Exception as exc:
        store.update_asset(asset["id"], analysis_status="failed", error=f"Style analysis failed: {exc}"[:1000])
        return "failed"


def _transcribe_and_index(asset: dict, path: Path, info: dict, language: str | None, progress: Progress) -> str:
    from .config import settings

    if not settings.openai_api_key:
        store.update_asset(asset["id"], transcript_status="skipped", transcript_error="OPENAI_API_KEY is not set")
        return "skipped"

    work = storage.dir_path(storage.work_dir_key(f"ingest_{asset['id']}"))
    try:
        transcript = media.transcribe(
            path, work, info["duration"], language,
            on_progress=lambda f, m: progress(0.55 + 0.35 * f, m),
        )
        store.update_asset(asset["id"], transcript=transcript, transcript_status="done", transcript_error=None)

        if transcript["segments"]:
            progress(0.92, "Indexing transcript for search")
            import rag_engine  # legacy module; heavy import, only needed here

            collection = rag_engine.index_transcript(transcript, video_id=f"asset_{asset['id']}")
            store.update_asset(asset["id"], collection_name=collection)
        return "done"
    except Exception as exc:
        store.update_asset(asset["id"], transcript_status="failed", transcript_error=str(exc)[-1000:])
        return "failed"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def refresh_video_dimensions() -> int:
    """
    Re-probe ready videos and fix stored width/height. Videos ingested before rotation
    handling was added have phone-portrait clips recorded as landscape.
    """
    fixed = 0
    with store.connect() as c:
        rows = c.execute(
            "SELECT id, storage_key, width, height FROM editor_assets WHERE status = 'ready' AND kind = 'video'"
        ).fetchall()
    for row in rows:
        try:
            path = storage.path(row["storage_key"])
            if not path.is_file():
                continue
            info = media.probe(path)
        except Exception:
            continue
        if (info["width"], info["height"]) != (row["width"], row["height"]):
            store.update_asset(row["id"], width=info["width"], height=info["height"])
            fixed += 1
    return fixed


# ---------------------------------------------------------------- render

def render(job: dict, progress: Progress) -> dict:
    payload = job["payload"]
    project_id = job["project_id"]
    row = store.get_timeline(project_id, payload["timeline_version"])
    if row is None:
        raise JobError(f"Timeline version {payload['timeline_version']} not found")
    timeline = Timeline.model_validate(row["data"])

    assets = {a["id"]: a for a in store.list_assets(project_id)}
    for a in assets.values():
        if a.get("kind") == "multicam":
            a["multicam"] = store.get_analysis(a["id"]) or {}
    problems = check_assets(timeline, assets, project_id)
    if problems:
        raise JobError("Timeline is not renderable: " + "; ".join(problems))

    used = ({c.asset_id for c in timeline.clips} | {o.asset_id for o in timeline.overlays}
            | {t.asset_id for t in timeline.audio if t.asset_id} | {b.asset_id for b in timeline.broll}
            | {st.sticker[6:] for st in timeline.stickers if st.sticker.startswith("asset:")})
    if timeline.grade and timeline.grade.lut_asset_id:
        used.add(timeline.grade.lut_asset_id)
    used |= {c.background.asset_id for c in timeline.clips if c.background and c.background.asset_id}
    used |= {multicam.angle_source(assets[c.asset_id], c.angle)[0] for c in timeline.clips
             if assets[c.asset_id].get("kind") == "multicam"}
    paths = {aid: _asset_path(assets[aid]) for aid in used}
    transcripts = {}
    if timeline.captions:
        for aid in {c.asset_id for c in timeline.clips}:
            transcripts[aid] = (store.get_asset(aid, with_transcript=True) or {}).get("transcript")
    key = storage.render_key(job["user_id"], project_id, job["id"])

    mattes, share = _mattes(timeline, assets, paths, progress)
    label = "Rendering preview" if payload.get("preview") else "Rendering"
    progress(share, label)
    info = renderer.render(
        timeline, assets, paths, storage.path(key),
        preview=bool(payload.get("preview")),
        on_progress=lambda f: progress(share + (1 - share) * f, None),
        transcripts=transcripts,
        mattes=mattes,
    )
    return {"storage_key": key, "timeline_version": payload["timeline_version"], **info}


def _mattes(timeline: Timeline, assets: dict, paths: dict, progress: Progress) -> tuple[dict, float]:
    """Cut-outs for clips with a new background; computes (and caches) missing ones.
    Returns {clip_id: (foreground, alpha, source time they start at)} and the share of progress used."""
    groups: dict[tuple, list] = {}
    for c in timeline.clips:
        bg = c.background
        if bg:
            # multicam clips are cut out of the angle's own footage, at its own times
            aid, shift = c.asset_id, 0.0
            if assets[aid].get("kind") == "multicam":
                aid, shift = multicam.angle_source(assets[aid], c.angle)
            key = (aid, bg.method, bg.key_color if bg.method == "chroma" else None,
                   bg.key_tolerance if bg.method == "chroma" else None)
            groups.setdefault(key, []).append((c, c.in_ + shift, c.out + shift))
    if not groups:
        return {}, 0.0
    tag = lambda k: matte.method_tag(k[1], k[2], k[3])  # noqa: E731
    missing = any(matte.find(assets[k[0]], a, b, tag=tag(k)) is None for k, cs in groups.items() for _, a, b in cs)
    share = 0.6 if missing else 0.0
    out = {}
    try:
        n = len(groups)
        for idx, (k, clips) in enumerate(groups.items()):
            aid, method, key_color, tol = k
            found = matte.ensure(assets[aid], paths[aid], [(a, b) for _, a, b in clips], method, key_color, tol,
                                 progress=lambda f, m, _i=idx: progress(share * (_i + f) / n, m))
            for c, a, b in clips:
                out[c.id] = found[(a, b)]
    except matte.MatteError as e:
        raise JobError(str(e)) from e
    return out, share


# ---------------------------------------------------------------- agent

def agent_turn(job: dict, progress: Progress) -> dict:
    from . import agent, jobs
    from .llm import LLMError, get_provider

    payload = job["payload"]
    project_id = job["project_id"]
    history = [m for m in store.list_messages(project_id) if m["id"] < payload["message_id"]]

    progress(0.05, "Thinking")
    try:
        provider = get_provider()
        result = agent.run(provider, project_id, payload["text"], history,
                           on_step=lambda step, tool: progress(min(0.9, 0.1 + step * 0.04), f"Working ({tool})"))
    except LLMError as exc:
        store.add_message(project_id, "assistant", f"Sorry, I couldn't reach the editing model: {exc}",
                          job_id=job["id"], meta={"error": str(exc)})
        raise JobError(str(exc)) from exc

    preview_job = None
    if result.timeline_version and payload.get("render_preview", True):
        preview_job = jobs.enqueue("render", job["user_id"],
                                   {"timeline_version": result.timeline_version, "preview": True}, project_id)["id"]

    meta = {"trace": result.trace, "usage": result.usage, "preview_job_id": preview_job,
            "errors": result.errors, "provider": provider.name, "model": provider.model}
    message_id = store.add_message(project_id, "assistant", result.reply, job_id=job["id"],
                                   timeline_version=result.timeline_version, meta=meta)
    return {"message_id": message_id, "reply": result.reply, "timeline_version": result.timeline_version,
            "preview_job_id": preview_job, "tool_calls": len(result.trace), "errors": result.errors}


def make_proxy(job: dict, progress: Progress) -> dict:
    """Background job: browser-friendly copy for footage uploaded before proxies existed."""
    asset = store.get_asset(job["payload"]["asset_id"])
    if asset is None or asset.get("proxy_key"):
        return {"skipped": True}
    progress(0.1, "Preparing a preview copy")
    key = storage.asset_key(asset["user_id"], asset["project_id"], f"{asset['id']}.proxy", ".mp4")
    media.make_proxy(_asset_path(asset), storage.path(key))
    store.update_asset(asset["id"], proxy_key=key)
    return {"asset_id": asset["id"]}


def queue_missing_proxies() -> int:
    """Queue proxy jobs for ready footage without one (once per asset)."""
    from . import jobs

    with store.connect() as c:
        rows = c.execute(
            """SELECT a.id, a.user_id, a.project_id FROM editor_assets a
               WHERE a.status = 'ready' AND a.kind = 'video' AND a.role = 'footage' AND a.proxy_key IS NULL
               AND NOT EXISTS (SELECT 1 FROM editor_jobs j WHERE j.kind = 'proxy'
                               AND j.payload LIKE '%' || a.id || '%')"""
        ).fetchall()
    for r in rows:
        jobs.enqueue("proxy", r["user_id"], {"asset_id": r["id"]}, r["project_id"])
    return len(rows)


def vision_or_none():
    """The vision model as a plain function, or None without an API key."""
    from .config import settings

    if not settings.openai_api_key:
        return None
    try:
        from .llm import get_provider

        provider = get_provider()
    except Exception:  # noqa: BLE001
        return None
    return provider.vision_json if hasattr(provider, "vision_json") else None


def make_multicam(job: dict, progress: Progress) -> dict:
    """Sync 2-3 camera angles (and an optional separate audio file) into one multicam asset."""
    payload = job["payload"]
    try:
        asset = multicam.create(job["project_id"], payload["asset_ids"], payload.get("audio_asset_id"),
                                vision_or_none(), progress)
    except multicam.MulticamError as exc:
        raise JobError(str(exc)) from exc
    return {"asset_id": asset["id"], "duration": asset["duration"]}


HANDLERS: dict[str, Callable[[dict, Progress], dict]] = {
    "multicam": make_multicam,
    "stock": stock_import,
    "genimage": generate_image,
    "proxy": make_proxy,
    "ingest": ingest,
    "render": render,
    "agent": agent_turn,
}
