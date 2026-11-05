"""
Divverse: agentic video editor API.

Run the API with `uvicorn api:app` and the job worker with `python -m editor.worker`
(both from backend/). All editor routes live in editor/router.py under /v2.
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from editor.config import settings  # noqa: E402  (after load_dotenv)
from editor.router import router as editor_router  # noqa: E402

app = FastAPI(
    title="Agentic Video Editor API",
    description="Upload footage or add a YouTube link, describe the edit in chat, and get a rendered video back. "
                "Slow work (downloads, transcription, the editing agent, renders) runs as jobs: poll `/v2/jobs/{id}`.",
    version="3.0.0",
)

allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(editor_router)


@app.get("/health", tags=["Health"])
def health_check():
    """Whether the API is up and which optional capabilities are configured."""
    return {
        "status": "healthy",
        "ffmpeg": bool(settings.ffmpeg and settings.ffprobe),
        "openai_key": bool(settings.openai_api_key),
        "stock": bool(settings.pixabay_api_key or settings.pexels_api_key),
        "stock_providers": [n for n, k in (("pixabay", settings.pixabay_api_key),
                                           ("pexels", settings.pexels_api_key)) if k],
    }
