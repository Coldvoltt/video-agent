"""
AI-generated B-roll stills (OpenAI image API). The renderer's camera moves (push-in,
pan) turn a still into a moving cutaway, so any moment can get a visual even without
stock footage. Model: EDITOR_IMAGE_MODEL (default gpt-image-2.5-flare).
"""

import base64
from pathlib import Path

from .config import settings

SIZES = {"portrait": "1024x1536", "landscape": "1536x1024", "square": "1024x1024"}
STYLE_HINT = ("Photorealistic B-roll still for a video, natural light, no text, no captions, no watermarks, "
              "no logos. ")


class ImageError(RuntimeError):
    pass


def generate(prompt: str, dest: Path, orientation: str = "landscape", client=None) -> Path:
    if not settings.openai_api_key and client is None:
        raise ImageError("Image generation needs OPENAI_API_KEY")
    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=180, max_retries=1)
    try:
        resp = client.images.generate(model=settings.image_model, prompt=STYLE_HINT + prompt[:900],
                                      size=SIZES.get(orientation, SIZES["landscape"]), n=1)
    except Exception as exc:
        raise ImageError(f"Image generation failed: {exc}") from exc
    item = resp.data[0]
    data = getattr(item, "b64_json", None)
    if not data:
        raise ImageError("The image model returned no image data")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(base64.b64decode(data))
    return dest
