"""
B-roll: cutaway footage shown over the main video while its audio carries on.

Two sources:
- the user's own clips (role "broll"): split into shots, each described by a vision model,
  so the agent can match shots to what's being said;
- stock footage from Pexels (needs PEXELS_API_KEY), downloaded into the project.
"""

import logging
from pathlib import Path
from typing import Callable, Optional

import httpx

from . import analysis
from .config import settings

log = logging.getLogger("editor.broll")

MAX_SHOTS = 24          # shots described per clip (longer clips are sampled)
PEXELS_URL = "https://api.pexels.com/videos/search"
PIXABAY_URL = "https://pixabay.com/api/videos/"
NO_KEY = ("Stock footage needs an API key: add PIXABAY_API_KEY (free at pixabay.com/api/docs) "
          "to backend/.env")
CACHE_SECONDS = 24 * 3600     # Pixabay requires caching search results for 24 hours

VISION_SYSTEM = "You describe B-roll shots for a video editor. Answer in JSON."
VISION_PROMPT = """The images are frames from different shots of one clip, in order (image 1 = shot 1, ...).
For each shot give a short, concrete description an editor could search (subject, action, setting, mood) and
up to 6 lowercase keyword tags. Answer as {"shots": [{"n": 1, "description": "...", "tags": ["..."]}, ...]}."""


class StockError(RuntimeError):
    pass


# ---------------------------------------------------------------- your own B-roll

def shot_ranges(path: Path, duration: float, min_shot: float = 0.6) -> list[tuple[float, float]]:
    """Shots of a clip, from cut detection; tiny slivers are merged into the previous shot."""
    cuts = [c for c in analysis.detect_cuts(path, min(duration, 1200)) if 0 < c < duration]
    edges = [0.0, *cuts, duration]
    shots: list[list[float]] = []
    for a, b in zip(edges, edges[1:]):
        if shots and b - a < min_shot:
            shots[-1][1] = b
        else:
            shots.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in shots]


def describe_clip(path: Path, info: dict, vision: Optional[Callable[[list[bytes]], dict]] = None) -> dict:
    """Shot list of a B-roll clip, with a description and tags per shot when a vision model is available."""
    shots = shot_ranges(path, float(info["duration"]))
    picked = shots if len(shots) <= MAX_SHOTS else [shots[round(i * (len(shots) - 1) / (MAX_SHOTS - 1))]
                                                     for i in range(MAX_SHOTS)]
    out = [{"start": a, "end": b} for a, b in picked]
    if vision and out:
        images = analysis.jpeg_frames(path, [(s["start"] + s["end"]) / 2 for s in out], width=384)
        try:
            answer = vision(images) if len(images) == len(out) else {}
            by_n = {int(x.get("n", 0)): x for x in answer.get("shots", []) if isinstance(x, dict)}
            for i, s in enumerate(out, 1):
                d = by_n.get(i, {})
                s["description"] = str(d.get("description", ""))[:200]
                s["tags"] = [str(t)[:30] for t in (d.get("tags") or [])][:6]
        except Exception as exc:          # the shot list is still useful without descriptions
            log.warning("B-roll description failed: %s", exc)
    return {"version": 1, "shots": out, "described": any(s.get("description") for s in out)}


# ---------------------------------------------------------------- stock footage (Pexels)

def stock_providers() -> list[str]:
    """Stock sources with a key configured, in order of preference."""
    return [name for name, key in (("pixabay", settings.pixabay_api_key), ("pexels", settings.pexels_api_key)) if key]


def stock_available() -> bool:
    return bool(stock_providers())


def _pick_file(files: list[dict], target_short_side: int) -> Optional[dict]:
    """The smallest MP4 that is at least the target size (or the largest one if none is)."""
    mp4 = [f for f in files if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height")]
    if not mp4:
        return None
    short = lambda f: min(f["width"], f["height"])  # noqa: E731
    big_enough = [f for f in mp4 if short(f) >= target_short_side]
    return min(big_enough, key=short) if big_enough else max(mp4, key=short)


def search_pexels(query: str, orientation: Optional[str] = None, per_page: int = 8,
                  target_short_side: int = 1080, client: Optional[httpx.Client] = None) -> list[dict]:
    """Search Pexels videos. Returns compact candidates (no download)."""
    if not settings.pexels_api_key:
        raise StockError(NO_KEY)
    params = {"query": query[:100], "per_page": max(1, min(per_page, 20))}
    if orientation in ("landscape", "portrait", "square"):
        params["orientation"] = orientation
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = client.get(PEXELS_URL, params=params, headers={"Authorization": settings.pexels_api_key})
        if r.status_code == 401:
            raise StockError("Pexels rejected the API key; check PEXELS_API_KEY")
        if r.status_code == 429:
            raise StockError("Pexels rate limit reached (200 searches an hour); try again later")
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as exc:
        raise StockError(f"Pexels search failed: {exc}") from exc
    finally:
        if own:
            client.close()
    results = []
    for v in data.get("videos", []):
        f = _pick_file(v.get("video_files", []), target_short_side)
        if not f:
            continue
        results.append({
            "provider": "pexels",
            "id": str(v["id"]),
            "duration": v.get("duration"),
            "width": f["width"],
            "height": f["height"],
            "thumbnail": v.get("image"),
            "page_url": v.get("url"),
            "credit": (v.get("user") or {}).get("name"),
            "credit_url": (v.get("user") or {}).get("url"),
            "download_url": f["link"],
        })
    return results


def download(url: str, dest: Path, client: Optional[httpx.Client] = None,
             max_bytes: int = 500 * 1024 * 1024) -> Path:
    if not url.startswith("https://"):
        raise StockError("Refusing a non-HTTPS download")
    dest.parent.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=True)
    written = 0
    try:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise StockError("Stock clip is too large")
                    f.write(chunk)
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        raise StockError(f"Download failed: {exc}") from exc
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    finally:
        if own:
            client.close()
    return dest


def get_pexels(video_id: str, target_short_side: int = 1080, client: Optional[httpx.Client] = None) -> dict:
    """One Pexels video by id (the server resolves the download link itself)."""
    if not settings.pexels_api_key:
        raise StockError(NO_KEY)
    if not video_id.isdigit():
        raise StockError("Invalid stock clip id")
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = client.get(f"https://api.pexels.com/videos/videos/{video_id}",
                       headers={"Authorization": settings.pexels_api_key})
        if r.status_code == 404:
            raise StockError("That stock clip no longer exists")
        r.raise_for_status()
        v = r.json()
    except httpx.HTTPError as exc:
        raise StockError(f"Pexels lookup failed: {exc}") from exc
    finally:
        if own:
            client.close()
    f = _pick_file(v.get("video_files", []), target_short_side)
    if not f:
        raise StockError("No downloadable MP4 for that clip")
    return {"provider": "pexels", "id": str(v["id"]), "duration": v.get("duration"), "width": f["width"],
            "height": f["height"], "page_url": v.get("url"), "credit": (v.get("user") or {}).get("name"),
            "credit_url": (v.get("user") or {}).get("url"), "download_url": f["link"]}


# ---------------------------------------------------------------- Pixabay

def _cache_get(kind: str, params: dict) -> Optional[dict]:
    import hashlib
    import json
    import time

    path = settings.storage_root / "cache" / kind / (hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")
    if path.is_file() and time.time() - path.stat().st_mtime < CACHE_SECONDS:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


def _cache_put(kind: str, params: dict, data: dict) -> None:
    import hashlib
    import json

    path = settings.storage_root / "cache" / kind / (hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _pixabay_query(params: dict, client: Optional[httpx.Client]) -> dict:
    if not settings.pixabay_api_key:
        raise StockError(NO_KEY)
    cached = _cache_get("pixabay", params)
    if cached is not None:
        return cached
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = client.get(PIXABAY_URL, params={**params, "key": settings.pixabay_api_key})
        if r.status_code in (400, 401, 403) and "key" in r.text.lower():
            raise StockError("Pixabay rejected the API key; check PIXABAY_API_KEY")
        if r.status_code == 429:
            raise StockError("Pixabay rate limit reached (100 searches a minute); try again shortly")
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as exc:
        raise StockError(f"Pixabay request failed: {exc}") from exc
    finally:
        if own:
            client.close()
    _cache_put("pixabay", params, data)
    return data


def _pixabay_result(hit: dict) -> Optional[dict]:
    videos = hit.get("videos") or {}
    f = next((videos[k] for k in ("medium", "large", "small") if (videos.get(k) or {}).get("url")), None)
    if not f:
        return None
    user, uid = hit.get("user") or "Pixabay", hit.get("user_id")
    return {
        "provider": "pixabay", "id": str(hit["id"]), "duration": hit.get("duration"),
        "width": f.get("width"), "height": f.get("height"), "thumbnail": f.get("thumbnail"),
        "page_url": hit.get("pageURL"), "credit": user,
        "credit_url": f"https://pixabay.com/users/{user}-{uid}/" if uid else hit.get("pageURL"),
        "download_url": f["url"], "tags": hit.get("tags"),
    }


def _shape(w: int, h: int) -> str:
    return "portrait" if h > w * 1.05 else "landscape" if w > h * 1.05 else "square"


def search_pixabay(query: str, orientation: Optional[str] = None, per_page: int = 8,
                   client: Optional[httpx.Client] = None) -> list[dict]:
    # Pixabay has no orientation filter for video: fetch more and filter by shape here.
    data = _pixabay_query({"q": query[:100], "per_page": 40 if orientation else max(3, min(per_page, 40)),
                           "safesearch": "true"}, client)
    results = [r for r in (_pixabay_result(h) for h in data.get("hits", [])) if r]
    if orientation in ("landscape", "portrait", "square"):
        matching = [r for r in results if r["width"] and _shape(r["width"], r["height"]) == orientation]
        results = matching or results       # better a cropped shot than none
    return results[:per_page]


def get_pixabay(video_id: str, client: Optional[httpx.Client] = None) -> dict:
    if not video_id.isdigit():
        raise StockError("Invalid stock clip id")
    hits = _pixabay_query({"id": video_id}, client).get("hits", [])
    result = _pixabay_result(hits[0]) if hits else None
    if not result:
        raise StockError("That stock clip no longer exists")
    return result


# ---------------------------------------------------------------- provider-neutral

def search_stock(query: str, orientation: Optional[str] = None, per_page: int = 8,
                 provider: Optional[str] = None, client: Optional[httpx.Client] = None) -> list[dict]:
    providers = [provider] if provider else stock_providers()
    if not providers:
        raise StockError(NO_KEY)
    results: list[dict] = []
    errors = []
    for name in providers:
        try:
            if name == "pixabay":
                results += search_pixabay(query, orientation, per_page, client=client)
            elif name == "pexels":
                results += search_pexels(query, orientation, per_page, client=client)
        except StockError as exc:
            errors.append(str(exc))
    if not results and errors:
        raise StockError(errors[0])
    return results[:per_page]


def get_stock(video_id: str, provider: str = "pixabay", client: Optional[httpx.Client] = None) -> dict:
    if provider == "pixabay":
        return get_pixabay(video_id, client=client)
    if provider == "pexels":
        return get_pexels(video_id, client=client)
    raise StockError(f"Unknown stock provider {provider!r}")


def provider_name(provider: str) -> str:
    return {"pixabay": "Pixabay", "pexels": "Pexels"}.get(provider, provider)
