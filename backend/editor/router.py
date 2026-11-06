"""
Editor HTTP API (mounted under /v2).

Everything slow (downloads, transcription, rendering) is queued as a job and
returns immediately with a job id; poll GET /v2/jobs/{job_id}.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from . import captions as captions_mod
from . import diff as diff_mod
from . import edits, jobs, media, storage, store
from . import luts as luts_mod
from .config import require_ffmpeg, settings
from .timeline import Timeline, check_assets, default_output

router = APIRouter(prefix="/v2", tags=["Editor"])

USER_ID = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$",
                description="Your unique user identifier")
UPLOAD_CHUNK = 8 * 1024 * 1024


# ---------------------------------------------------------------- helpers

def _project_or_404(project_id: str, user_id: str) -> dict:
    project = store.get_project(project_id, user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _public_asset(a: dict) -> dict:
    out = {k: v for k, v in a.items() if k not in ("storage_key", "collection_name", "user_id", "proxy_key")}
    out["has_proxy"] = bool(a.get("proxy_key"))
    if a.get("role") == "broll" and a.get("analysis_status") == "done":
        shots = (store.get_analysis(a["id"]) or {}).get("shots", [])
        out["shots"] = [{k: v for k, v in sh.items() if k in ("start", "end", "description")} for sh in shots]
    if a.get("role") == "reference" and a.get("analysis_status") == "done":
        profile = store.get_analysis(a["id"]) or {}
        out["style"] = {
            "summary": profile.get("summary"),
            "pacing": {k: v for k, v in profile.get("pacing", {}).items() if k != "cut_times"},
            "color": {k: profile.get("color", {}).get(k) for k in ("brightness", "contrast", "saturation", "warmth")},
            "audio": profile.get("audio"),
            "visual": profile.get("visual"),
            "suggestions": profile.get("suggestions"),
        }
    return out


def _with_clip_text(row: dict | None) -> dict | None:
    """Attach the words spoken in each clip (what the timeline UI labels clips with)."""
    if not row:
        return row
    transcripts: dict[str, dict | None] = {}
    texts = {}
    for c in row["data"]["clips"]:
        aid = c["asset_id"]
        if aid not in transcripts:
            transcripts[aid] = (store.get_asset(aid, with_transcript=True) or {}).get("transcript")
        texts[c["id"]] = edits.spoken_text(transcripts[aid], c["in"], c["out"])
    return {**row, "clip_text": texts}


def _validation_detail(exc: ValidationError) -> list[dict]:
    return [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]


# ---------------------------------------------------------------- projects

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/projects")
def create_project(body: ProjectIn, user_id: str = USER_ID):
    return store.create_project(user_id, body.name)


@router.get("/projects")
def list_projects(user_id: str = USER_ID):
    return {"projects": store.list_projects(user_id)}


@router.get("/projects/{project_id}")
def get_project(project_id: str, user_id: str = USER_ID):
    project = _project_or_404(project_id, user_id)
    timeline = _with_clip_text(store.get_timeline(project_id))
    return {
        **project,
        "assets": [_public_asset(a) for a in store.list_assets(project_id)],
        "timeline": timeline,
        "timeline_versions": store.list_timeline_versions(project_id),
        "history": store.history_state(project_id),
    }


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, user_id: str = USER_ID):
    _project_or_404(project_id, user_id)
    assets = store.list_assets(project_id)
    store.delete_project(project_id, user_id)
    for a in assets:
        if a.get("collection_name"):
            try:
                import rag_engine

                rag_engine.delete_collection(a["collection_name"])
            except Exception:
                pass
    project_dir = settings.storage_root / "users" / user_id / "projects" / project_id
    if project_dir.is_dir():
        import shutil

        shutil.rmtree(project_dir, ignore_errors=True)
    return {"deleted": project_id}


# ---------------------------------------------------------------- assets

@router.post("/projects/{project_id}/assets/upload")
async def upload_asset(
    project_id: str,
    user_id: str = USER_ID,
    file: UploadFile = File(...),
    transcribe: bool = Query(True, description="Transcribe speech (needed for captions and smart cuts)"),
    role: str = Query("footage", pattern="^(footage|reference|broll)$",
                      description="footage = material to edit; reference = a video whose style to copy"),
    language: Optional[str] = Query(None, max_length=8, description="Language code, e.g. 'en'"),
):
    _project_or_404(project_id, user_id)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in media.ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type {ext or '(none)'}. "
                            f"Allowed: {', '.join(sorted(media.ALLOWED_UPLOAD_EXTS))}")

    asset_id = store.new_id()
    key = storage.asset_key(user_id, project_id, asset_id, ext)
    dest = storage.path(key)
    written = 0
    try:
        with open(dest, "wb") as f:
            while chunk := await file.read(UPLOAD_CHUNK):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="File too large")
                f.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if written == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Empty file")

    if role == "broll":
        transcribe = False                      # cutaways are shown without their sound
        if ext not in media.VIDEO_EXTS | media.IMAGE_EXTS:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="B-roll must be a video or an image")
    if role == "reference" and ext not in media.VIDEO_EXTS:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="A reference must be a video file")
    asset = store.create_asset(project_id, user_id, "upload", asset_id=asset_id, filename=file.filename,
                               storage_key=key, transcribe=transcribe, role=role)
    job = jobs.enqueue("ingest", user_id, {"asset_id": asset_id, "language": language}, project_id)
    return {"asset": _public_asset(asset), "job": jobs.public_view(job)}


class UrlAssetIn(BaseModel):
    role: str = Field("footage", pattern="^(footage|reference|broll)$")
    url: str = Field(max_length=500)
    transcribe: bool = True
    language: Optional[str] = Field(None, max_length=8)


@router.post("/projects/{project_id}/assets/url")
def add_url_asset(project_id: str, body: UrlAssetIn, user_id: str = USER_ID):
    _project_or_404(project_id, user_id)
    if not media.is_youtube_url(body.url):
        raise HTTPException(status_code=400, detail="Only YouTube links are supported")
    asset = store.create_asset(project_id, user_id, "youtube", source_url=body.url, transcribe=body.transcribe,
                               role=body.role)
    job = jobs.enqueue("ingest", user_id, {"asset_id": asset["id"], "language": body.language}, project_id)
    return {"asset": _public_asset(asset), "job": jobs.public_view(job)}


@router.get("/projects/{project_id}/assets/{asset_id}")
def get_asset(project_id: str, asset_id: str, user_id: str = USER_ID,
              include_transcript: bool = Query(False)):
    _project_or_404(project_id, user_id)
    asset = store.get_asset(asset_id, with_transcript=include_transcript)
    if not asset or asset["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    return _public_asset(asset)


@router.get("/projects/{project_id}/assets/{asset_id}/file")
def get_asset_file(project_id: str, asset_id: str, user_id: str = USER_ID,
                   proxy: bool = Query(False, description="The small browser-friendly copy, if there is one")):
    _project_or_404(project_id, user_id)
    asset = store.get_asset(asset_id)
    if not asset or asset["project_id"] != project_id or not asset.get("storage_key"):
        raise HTTPException(status_code=404, detail="Asset not found")
    key = asset.get("proxy_key") if proxy and asset.get("proxy_key") else asset["storage_key"]
    path = storage.path(key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Asset file missing")
    return FileResponse(path, filename=Path(asset["filename"] or path.name).name)


@router.get("/projects/{project_id}/assets/{asset_id}/screen-color")
def get_screen_color(project_id: str, asset_id: str, user_id: str = USER_ID, t: float = Query(0.5, ge=0)):
    """The green/blue screen colour of a video (for chroma keying), or null if it isn't shot on one."""
    from . import matte

    _project_or_404(project_id, user_id)
    asset = store.get_asset(asset_id)
    if not asset or asset["project_id"] != project_id or asset.get("kind") != "video" or not asset.get("storage_key"):
        raise HTTPException(status_code=404, detail="Video not found")
    return {"color": matte.screen_color(storage.path(asset["storage_key"]), t)}


# ---------------------------------------------------------------- timeline

class TimelineIn(BaseModel):
    timeline: dict
    note: Optional[str] = Field(None, max_length=500)
    render_preview: bool = False


@router.get("/projects/{project_id}/timeline")
def get_timeline(project_id: str, user_id: str = USER_ID, version: Optional[int] = Query(None, ge=1)):
    _project_or_404(project_id, user_id)
    row = store.get_timeline(project_id, version)
    if not row:
        raise HTTPException(status_code=404, detail="No timeline yet")
    return _with_clip_text(row)


@router.put("/projects/{project_id}/timeline")
def save_timeline(project_id: str, body: TimelineIn, user_id: str = USER_ID):
    """Validate and store a new timeline version. Asset problems are reported but do not block saving."""
    _project_or_404(project_id, user_id)
    assets = {a["id"]: a for a in store.list_assets(project_id)}
    data = dict(body.timeline)
    if "output" not in data and data.get("clips") and isinstance(data["clips"][0], dict):
        # No format given: match the first clip's footage so portrait stays portrait.
        data["output"] = default_output(assets.get(data["clips"][0].get("asset_id"))) or {}
    try:
        timeline = Timeline.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc))
    warnings = check_assets(timeline, assets, project_id)
    version = store.save_timeline(project_id, timeline.to_json(), body.note)
    preview = _queue_preview(project_id, user_id, version, timeline, assets) if body.render_preview else None
    return {"version": version, "duration": round(timeline.duration, 3), "warnings": warnings,
            "preview_job_id": preview}


def _queue_preview(project_id: str, user_id: str, version: int, timeline: Timeline, assets: dict) -> Optional[str]:
    if check_assets(timeline, assets, project_id):
        return None
    return jobs.enqueue("render", user_id, {"timeline_version": version, "preview": True}, project_id)["id"]


def _history_step(project_id: str, user_id: str, undo: bool) -> dict:
    _project_or_404(project_id, user_id)
    result = store.undo_timeline(project_id) if undo else store.redo_timeline(project_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Nothing to undo" if undo else "Nothing to redo")
    row = store.get_timeline(project_id, result["version"])
    assets = {a["id"]: a for a in store.list_assets(project_id)}
    preview = _queue_preview(project_id, user_id, result["version"], Timeline.model_validate(row["data"]), assets)
    return {**result, "preview_job_id": preview, **store.history_state(project_id)}


@router.post("/projects/{project_id}/timeline/undo")
def undo(project_id: str, user_id: str = USER_ID):
    """Go back one edit (saved as a new version, so redo and history keep working)."""
    return _history_step(project_id, user_id, undo=True)


@router.post("/projects/{project_id}/timeline/redo")
def redo(project_id: str, user_id: str = USER_ID):
    return _history_step(project_id, user_id, undo=False)


@router.get("/projects/{project_id}/timeline/changes")
def timeline_changes(project_id: str, user_id: str = USER_ID, version: Optional[int] = Query(None, ge=1),
                     against: Optional[int] = Query(None, ge=1)):
    """What a version changed compared with the one before it (or `against`), in plain words."""
    _project_or_404(project_id, user_id)
    row = store.get_timeline(project_id, version)
    if not row:
        raise HTTPException(status_code=404, detail="Timeline version not found")
    base_no = against or row["version"] - 1
    base = store.get_timeline(project_id, base_no) if base_no >= 1 else None
    names = {a["id"]: a.get("filename") or "media" for a in store.list_assets(project_id)}
    return {"version": row["version"], "against": base["version"] if base else None, "note": row.get("note"),
            "changes": diff_mod.describe_changes(base["data"] if base else None, row["data"], names)}


class RestoreIn(BaseModel):
    version: int = Field(ge=1)


@router.post("/projects/{project_id}/timeline/restore")
def restore_timeline(project_id: str, body: RestoreIn, user_id: str = USER_ID):
    """Make an earlier version current again by saving a copy of it as the newest version."""
    _project_or_404(project_id, user_id)
    row = store.get_timeline(project_id, body.version)
    if not row:
        raise HTTPException(status_code=404, detail="Timeline version not found")
    version = store.save_timeline(project_id, row["data"], note=f"Restored version {body.version}")
    return {"version": version, "restored_from": body.version}


# ---------------------------------------------------------------- stock B-roll (Pexels)

@router.get("/projects/{project_id}/stock/search")
def stock_search(project_id: str, q: str = Query(..., min_length=1, max_length=100), user_id: str = USER_ID,
                 orientation: Optional[str] = Query(None, pattern="^(landscape|portrait|square)$")):
    """Search stock video. Results include a thumbnail and the creator credit; nothing is downloaded."""
    from . import broll

    _project_or_404(project_id, user_id)
    try:
        results = broll.search_stock(q, orientation)
    except broll.StockError as exc:
        raise HTTPException(status_code=409 if "API key" in str(exc) else 502, detail=str(exc))
    return {"query": q, "providers": broll.stock_providers(),
            "results": [{k: v for k, v in r.items() if k != "download_url"} for r in results]}


class StockAddIn(BaseModel):
    provider: str = Field("pixabay", pattern="^(pixabay|pexels)$")
    id: str = Field(min_length=1, max_length=20, pattern="^[0-9]+$")


@router.post("/projects/{project_id}/stock/add")
def stock_add(project_id: str, body: StockAddIn, user_id: str = USER_ID):
    """Bring a stock clip into the project as B-roll (downloaded and prepared in the background)."""
    from . import broll

    _project_or_404(project_id, user_id)
    try:
        clip = broll.get_stock(body.id, body.provider)
    except broll.StockError as exc:
        raise HTTPException(status_code=409 if "API key" in str(exc) else 502, detail=str(exc))
    name = f"{clip['credit'] or 'Stock'} on {broll.provider_name(body.provider)} ({clip['id']}).mp4"
    asset = store.create_asset(project_id, user_id, body.provider, filename=name, source_url=clip["page_url"],
                               transcribe=False, role="broll")
    job = jobs.enqueue("stock", user_id, {"asset_id": asset["id"], "download_url": clip["download_url"]},
                       project_id)
    return {"asset": _public_asset(asset), "job": jobs.public_view(job), "credit": clip["credit"],
            "credit_url": clip["credit_url"]}


class GenerateImageIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=500)
    orientation: Optional[str] = Field(None, pattern="^(landscape|portrait|square)$")


@router.post("/projects/{project_id}/broll/generate")
def generate_broll(project_id: str, body: GenerateImageIn, user_id: str = USER_ID):
    """Generate an AI still to use as B-roll (animated with camera motion when placed)."""
    _project_or_404(project_id, user_id)
    if not settings.openai_api_key:
        raise HTTPException(status_code=409, detail="Image generation needs OPENAI_API_KEY")
    orientation = body.orientation
    if orientation is None:
        row = store.get_timeline(project_id)
        o = (row or {}).get("data", {}).get("output", {})
        orientation = ("portrait" if o.get("height", 0) > o.get("width", 1) else
                       "square" if o and o.get("height") == o.get("width") else "landscape")
    asset = store.create_asset(project_id, user_id, "ai", filename=f"AI: {body.prompt[:60]}.png",
                               transcribe=False, role="broll")
    job = jobs.enqueue("genimage", user_id, {"asset_id": asset["id"], "prompt": body.prompt,
                                             "orientation": orientation}, project_id)
    return {"asset": _public_asset(asset), "job": jobs.public_view(job)}


# ---------------------------------------------------------------- stickers and callouts (Lottie)

POPULAR_EMOJI = ["🔥", "😂", "🤯", "😍", "👏", "💯", "🚀", "💡", "✅", "❌", "👀", "🎉", "💰", "❤️", "😱", "🙌"]


@router.get("/stickers")
def list_stickers():
    from . import stickers

    return {"callouts": [{"id": k, "description": v} for k, v in stickers.CALLOUTS.items()],
            "default_color": stickers.DEFAULT_COLOR, "popular_emoji": POPULAR_EMOJI,
            "emoji_credit": stickers.NOTO_CREDIT}


@router.get("/stickers/lottie")
def sticker_lottie(sticker: str = Query(..., max_length=80), color: Optional[str] = Query(None, pattern=r"^#[0-9a-fA-F]{6}$")):
    """Lottie JSON for a built-in callout or an emoji (the live preview plays it with lottie-web)."""
    from fastapi.responses import Response

    from . import stickers

    if sticker.startswith("asset:"):
        raise HTTPException(status_code=400, detail="Use the asset file endpoint for uploaded animations")
    try:
        data = stickers.resolve(sticker, color)
    except stickers.StickerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(content=data, media_type="application/json",
                    headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------- sound effects (built-in)

@router.get("/sfx")
def list_sfx():
    from . import sfx

    return {"sounds": [sfx.info(k) for k in sfx.SOUNDS]}


@router.get("/sfx/{sound_id}.wav")
def get_sfx(sound_id: str):
    from . import sfx

    if sound_id not in sfx.SOUNDS:
        raise HTTPException(status_code=404, detail="Unknown sound")
    return FileResponse(sfx.path(sound_id), media_type="audio/wav", filename=f"{sound_id}.wav")


# ---------------------------------------------------------------- colour looks (built-in LUTs)

@router.get("/luts")
def list_luts():
    """The built-in looks. `css` is a rough filter the live preview uses; renders use the real LUT."""
    return {"looks": [{"id": p.id, "name": p.name, "description": p.description, "css": p.css}
                      for p in luts_mod.PRESETS.values()]}


@router.get("/luts/{look_id}.cube")
def download_lut(look_id: str):
    if look_id not in luts_mod.PRESETS:
        raise HTTPException(status_code=404, detail="Unknown look")
    filename = luts_mod.PRESETS[look_id].name.replace(" & ", "-").replace(" ", "-") + ".cube"
    return FileResponse(luts_mod.preset_path(look_id), media_type="text/plain", filename=filename)


def _jpeg(img) -> str:
    import base64
    import subprocess

    import numpy as np

    if img.dtype != np.uint8:
        img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    h, w, _ = img.shape
    out = subprocess.run([require_ffmpeg(), "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                          "-i", "-", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "-"],
                         input=img.tobytes(), capture_output=True, check=True).stdout
    return "data:image/jpeg;base64," + base64.b64encode(out).decode("ascii")


@router.get("/projects/{project_id}/luts/thumbnails")
def lut_thumbnails(project_id: str, user_id: str = USER_ID, at: Optional[float] = Query(None, ge=0)):
    """A frame of the project's footage shown through every built-in look (for the look picker)."""
    from . import analysis

    _project_or_404(project_id, user_id)
    assets = {a["id"]: a for a in store.list_assets(project_id)}
    row = store.get_timeline(project_id)
    source, t = None, 0.0
    if row:
        tl = Timeline.model_validate(row["data"])
        target = min(at if at is not None else tl.duration * 0.4, tl.duration)
        for c, (a, b) in zip(tl.clips, tl.clip_spans()):
            if a <= target <= b and assets.get(c.asset_id, {}).get("kind") == "video":
                source, t = assets[c.asset_id], c.in_ + (target - a)
                break
    if source is None:
        source = next((a for a in assets.values()
                       if a.get("kind") == "video" and a["status"] == "ready" and a.get("role") == "footage"), None)
        t = (source or {}).get("duration", 0) * 0.4
    if source is None:
        raise HTTPException(status_code=409, detail="Add a video first")
    w = 240
    h = max(2, int(round(w * (source.get("height") or 9) / (source.get("width") or 16) / 2)) * 2)
    frame = analysis.frames_at(storage.path(source["storage_key"]), [t], w, h)[0]
    images = [{"id": "none", "name": "Original", "image": _jpeg(frame)}]
    for p in luts_mod.PRESETS.values():
        images.append({"id": p.id, "name": p.name, "image": _jpeg(luts_mod.apply(frame, luts_mod.build(p.id, 17)))})
    return {"asset_id": source["id"], "at": round(t, 2), "looks": images}


# ---------------------------------------------------------------- captions (manual editing)

def _transcripts_for(timeline: Timeline) -> dict:
    return {aid: (store.get_asset(aid, with_transcript=True) or {}).get("transcript")
            for aid in {c.asset_id for c in timeline.clips}}


@router.get("/projects/{project_id}/captions")
def get_captions(project_id: str, user_id: str = USER_ID, version: Optional[int] = Query(None, ge=1)):
    """Caption settings and every caption line as it will be rendered, with its source range for editing."""
    _project_or_404(project_id, user_id)
    row = store.get_timeline(project_id, version)
    if not row:
        raise HTTPException(status_code=404, detail="No timeline yet")
    timeline = Timeline.model_validate(row["data"])
    settings_ = timeline.captions.model_dump(exclude={"corrections"}) if timeline.captions else None
    return {"version": row["version"], "settings": settings_,
            "lines": captions_mod.caption_lines(timeline, _transcripts_for(timeline))}


class CaptionPreviewIn(BaseModel):
    timeline: dict


@router.post("/projects/{project_id}/captions/preview")
def preview_captions(project_id: str, body: CaptionPreviewIn, user_id: str = USER_ID):
    """Caption lines for an unsaved timeline (the live preview of hand edits). Nothing is saved."""
    _project_or_404(project_id, user_id)
    try:
        timeline = Timeline.model_validate(body.timeline)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc))
    if not timeline.captions:
        return {"lines": []}
    owned = {a["id"] for a in store.list_assets(project_id)}   # never read another project's transcripts
    transcripts = {aid: t for aid, t in _transcripts_for(timeline).items() if aid in owned}
    return {"lines": captions_mod.caption_lines(timeline, transcripts)}


class CaptionEditIn(BaseModel):
    asset_id: str = Field(min_length=1, max_length=64)
    start: float = Field(ge=0, description="Source time, from a caption line's src_start")
    end: float = Field(gt=0, description="Source time, from a caption line's src_end")
    text: str = Field("", max_length=300, description="New text; empty hides the line")


class CaptionsUpdateIn(BaseModel):
    enabled: bool = True
    settings: Optional[dict] = Field(None, description="Any of: style, position, size, color, highlight_color, "
                                                       "uppercase, max_words")
    edits: list[CaptionEditIn] = Field(default_factory=list, max_length=500)
    render_preview: bool = True


_EDITABLE_CAPTION_SETTINGS = {"style", "position", "size", "color", "highlight_color", "uppercase", "max_words"}


@router.post("/projects/{project_id}/captions")
def update_captions(project_id: str, body: CaptionsUpdateIn, user_id: str = USER_ID):
    """Turn captions on/off, change their look, or replace the text of caption lines. Saves a new version."""
    _project_or_404(project_id, user_id)
    row = store.get_timeline(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="No timeline yet")
    data = dict(row["data"])
    if not body.enabled:
        data.pop("captions", None)
        note = "Captions turned off"
    else:
        unknown = set(body.settings or {}) - _EDITABLE_CAPTION_SETTINGS
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown caption settings: {sorted(unknown)}")
        caps = {**(data.get("captions") or {}), **(body.settings or {})}
        caps["corrections"] = captions_mod.merge_caption_edits(caps.get("corrections", []), [e.model_dump() for e in body.edits])
        data["captions"] = caps
        parts = []
        if body.settings:
            parts.append("style")
        if body.edits:
            parts.append(f"{len(body.edits)} line{'s' if len(body.edits) != 1 else ''}")
        note = "Edited captions" + (f" ({', '.join(parts)})" if parts else "")
    try:
        timeline = Timeline.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc))
    version = store.save_timeline(project_id, timeline.to_json(), note)
    preview = None
    assets = {a["id"]: a for a in store.list_assets(project_id)}
    if body.render_preview and not check_assets(timeline, assets, project_id):
        preview = jobs.enqueue("render", user_id, {"timeline_version": version, "preview": True}, project_id)["id"]
    return {"version": version, "preview_job_id": preview}


# ---------------------------------------------------------------- renders & jobs

class RenderIn(BaseModel):
    preview: bool = True
    timeline_version: Optional[int] = Field(None, ge=1, description="Defaults to the latest version")


@router.post("/projects/{project_id}/renders")
def start_render(project_id: str, body: RenderIn, user_id: str = USER_ID):
    _project_or_404(project_id, user_id)
    row = store.get_timeline(project_id, body.timeline_version)
    if not row:
        raise HTTPException(status_code=404, detail="Timeline version not found")
    timeline = Timeline.model_validate(row["data"])
    assets = {a["id"]: a for a in store.list_assets(project_id)}
    problems = check_assets(timeline, assets, project_id)
    if problems:
        raise HTTPException(status_code=409, detail=problems)
    job = jobs.enqueue("render", user_id, {"timeline_version": row["version"], "preview": body.preview}, project_id)
    return jobs.public_view(job)


@router.get("/projects/{project_id}/jobs")
def list_jobs(project_id: str, user_id: str = USER_ID):
    _project_or_404(project_id, user_id)
    return {"jobs": [jobs.public_view(j) for j in jobs.list_for_project(project_id)]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user_id: str = USER_ID):
    job = jobs.get(job_id, user_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs.public_view(job)


@router.get("/jobs/{job_id}/output")
def download_render(job_id: str, user_id: str = USER_ID):
    job = jobs.get(job_id, user_id)
    if not job or job["kind"] != "render":
        raise HTTPException(status_code=404, detail="Render not found")
    if job["status"] != "succeeded":
        raise HTTPException(status_code=409, detail=f"Render is {job['status']}")
    path = storage.path(job["result"]["storage_key"])
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Render file no longer exists")
    return FileResponse(path, media_type="video/mp4", filename=f"render_{job_id}.mp4")


# ---------------------------------------------------------------- chat with the editing agent

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    render_preview: bool = Field(True, description="Queue a preview render when the agent changes the timeline")


@router.post("/projects/{project_id}/chat")
def chat(project_id: str, body: ChatIn, user_id: str = USER_ID):
    """Send an editing request. Returns the queued agent job; its result holds the reply,
    the new timeline version and the preview render job id."""
    _project_or_404(project_id, user_id)
    if any(j["kind"] == "agent" and j["status"] in ("queued", "running") for j in jobs.list_for_project(project_id, 20)):
        raise HTTPException(status_code=409, detail="The editor is still working on your previous request")
    message_id = store.add_message(project_id, "user", body.message)
    job = jobs.enqueue("agent", user_id, {"message_id": message_id, "text": body.message,
                                          "render_preview": body.render_preview}, project_id)
    store.set_message_job(message_id, job["id"])
    return {"message_id": message_id, "job": jobs.public_view(job)}


@router.get("/projects/{project_id}/messages")
def list_messages(project_id: str, user_id: str = USER_ID):
    _project_or_404(project_id, user_id)
    out = []
    for m in store.list_messages(project_id):
        meta = m.get("meta") or {}
        out.append({"id": m["id"], "role": m["role"], "content": m["content"], "job_id": m["job_id"],
                    "timeline_version": m["timeline_version"], "preview_job_id": meta.get("preview_job_id"),
                    "created_at": m["created_at"]})
    return {"messages": out}


# ---------------------------------------------------------------- search (foundation for agent tools)

@router.get("/projects/{project_id}/search")
def search_project(project_id: str, q: str = Query(..., min_length=1, max_length=500),
                   user_id: str = USER_ID, n: int = Query(5, ge=1, le=20)):
    """Semantic search over the transcripts of every transcribed asset in the project."""
    _project_or_404(project_id, user_id)
    indexed = [a for a in store.list_assets(project_id) if a.get("collection_name") and a.get("role") == "footage"]
    if not indexed:
        raise HTTPException(status_code=409, detail="No transcribed assets in this project yet")
    import rag_engine

    results = []
    for a in indexed:
        for r in rag_engine.search(q, a["collection_name"], n_results=n):
            results.append({"asset_id": a["id"], **r})
    results.sort(key=lambda r: r["relevance"], reverse=True)
    return {"query": q, "results": results[:n]}
