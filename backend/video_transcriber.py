"""
Video extraction and transcription module.
Handles: extracting YouTube transcripts, local video transcription with Whisper API.
"""

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from moviepy import VideoFileClip
from openai import OpenAI

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SUPPORTED_VIDEO_FORMATS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv"}
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


def is_url(input_str: str) -> bool:
    """Check if input is a URL."""
    parsed = urlparse(input_str)
    return parsed.scheme in ("http", "https")


def is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube video."""
    parsed = urlparse(url)
    return parsed.netloc in (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
    )


def extract_youtube_transcript(url: str, language: str = "en") -> dict:
    """
    Extract transcript directly from YouTube without downloading the video.

    Args:
        url: YouTube video URL
        language: Preferred language code (default: 'en')

    Returns:
        Dictionary with segments, full_text, title, duration, language

    Raises:
        ValueError: If no transcript is available for the video
    """
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # Extract video info
        info = ydl.extract_info(url, download=False)

        if not info:
            raise ValueError(f"Could not extract video info from URL: {url}")

        title = info.get("title", "Untitled Video")
        duration = info.get("duration", 0)

        # Check for subtitles (manual or auto-generated)
        subtitles = info.get("subtitles", {})
        automatic_captions = info.get("automatic_captions", {})

        # Try to find transcript in order of preference
        transcript_data = None
        detected_language = None

        # First try manual subtitles in preferred language
        for lang in [language, "en"]:
            if lang in subtitles:
                transcript_data = subtitles[lang]
                detected_language = lang
                break

        # Then try auto-generated captions
        if not transcript_data:
            for lang in [language, "en"]:
                if lang in automatic_captions:
                    transcript_data = automatic_captions[lang]
                    detected_language = lang
                    break

        # If still no transcript, check for any available
        if not transcript_data:
            if subtitles:
                first_lang = list(subtitles.keys())[0]
                transcript_data = subtitles[first_lang]
                detected_language = first_lang
            elif automatic_captions:
                first_lang = list(automatic_captions.keys())[0]
                transcript_data = automatic_captions[first_lang]
                detected_language = first_lang

        if not transcript_data:
            # No captions available - return None to trigger audio download fallback
            return None

        # Get the transcript URL (prefer json3 format)
        transcript_url = None
        for fmt in transcript_data:
            if fmt.get("ext") == "json3":
                transcript_url = fmt.get("url")
                break

        if not transcript_url and transcript_data:
            transcript_url = transcript_data[0].get("url")

        if not transcript_url:
            raise ValueError(f"Could not get transcript URL for video: {title}")

        # Download and parse the transcript
        segments = _fetch_and_parse_transcript(transcript_url)

        if not segments:
            raise ValueError(f"Transcript is empty for video: {title}")

        full_text = " ".join(seg["text"] for seg in segments)

        return {
            "segments": segments,
            "full_text": full_text,
            "language": detected_language,
            "duration": duration,
            "title": title,
            "video_url": url,
        }


def _fetch_and_parse_transcript(transcript_url: str) -> list[dict]:
    """Fetch and parse transcript from URL."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(transcript_url) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to fetch transcript: {e}")

    segments = []

    # Parse json3 format
    events = data.get("events", [])
    for event in events:
        # Skip non-caption events
        if "segs" not in event:
            continue

        start_ms = event.get("tStartMs", 0)
        duration_ms = event.get("dDurationMs", 0)

        # Combine text segments
        text_parts = []
        for seg in event.get("segs", []):
            text = seg.get("utf8", "").strip()
            if text and text != "\n":
                text_parts.append(text)

        if text_parts:
            text = " ".join(text_parts)
            # Clean up text
            text = re.sub(r"\s+", " ", text).strip()

            if text:
                segments.append({
                    "start": start_ms / 1000.0,
                    "end": (start_ms + duration_ms) / 1000.0,
                    "text": text,
                })

    return segments


def validate_video_path(file_path: str) -> str:
    """Validate that video file exists and is supported format."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {file_path}")

    if path.suffix.lower() not in SUPPORTED_VIDEO_FORMATS:
        raise ValueError(f"Unsupported format: {path.suffix}. Supported: {SUPPORTED_VIDEO_FORMATS}")

    return str(path.absolute())


def extract_audio(video_path: str, output_audio_path: str = None) -> str:
    """Extract audio from video file."""
    if output_audio_path:
        audio_path = output_audio_path
    else:
        video_name = Path(video_path).stem
        audio_path = str(OUTPUT_DIR / f"{video_name}_audio.mp3")

    video = VideoFileClip(video_path)
    video.audio.write_audiofile(audio_path, logger=None)
    video.close()

    return audio_path


def download_youtube_audio(url: str, output_path: str) -> dict:
    """
    Download audio from YouTube video using yt-dlp.

    Args:
        url: YouTube video URL
        output_path: Path where the audio file should be saved

    Returns:
        Dictionary with title, duration, and audio_path
    """
    import yt_dlp

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Remove extension from output_path for yt-dlp template
    output_base = str(Path(output_path).with_suffix(""))

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_base + ".%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "Untitled Video")
        duration = info.get("duration", 0)

    # The actual output file will have .mp3 extension
    final_path = output_base + ".mp3"

    return {
        "title": title,
        "duration": duration,
        "audio_path": final_path,
    }


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds."""
    video = VideoFileClip(video_path)
    duration = video.duration
    video.close()
    return duration


def get_video_title(video_path: str) -> str:
    """Extract clean title from video path."""
    name = Path(video_path).stem
    name = re.sub(r"[\[\(].*?[\]\)]", "", name)
    name = re.sub(r"[_-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name if name else "Untitled Video"


def transcribe_audio(audio_path: str, language: str = None) -> dict:
    """Transcribe audio using OpenAI Whisper API."""
    file_size = os.path.getsize(audio_path)
    max_size = 25 * 1024 * 1024  # 25MB limit

    if file_size > max_size:
        return _transcribe_chunked(audio_path, language)

    return _transcribe_single(audio_path, language)


def _transcribe_single(audio_path: str, language: str = None) -> dict:
    """Transcribe a single audio file."""
    with open(audio_path, "rb") as audio_file:
        kwargs = {
            "model": "whisper-1",
            "file": audio_file,
            "response_format": "verbose_json",
            "timestamp_granularities": ["segment"],
        }
        if language:
            kwargs["language"] = language

        response = client.audio.transcriptions.create(**kwargs)

    segments = [
        {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        }
        for seg in response.segments
    ]

    return {
        "segments": segments,
        "full_text": " ".join(s["text"] for s in segments),
        "language": response.language,
        "duration": response.duration,
    }


def _transcribe_chunked(audio_path: str, language: str = None) -> dict:
    """Transcribe large audio files by splitting into chunks."""
    from pydub import AudioSegment

    audio = AudioSegment.from_file(audio_path)
    total_duration = len(audio) / 1000.0

    chunk_duration_ms = 10 * 60 * 1000  # 10 minutes
    all_segments = []
    detected_language = None

    for i in range(0, len(audio), chunk_duration_ms):
        offset_seconds = i / 1000.0
        chunk = audio[i : i + chunk_duration_ms]

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            chunk.export(tmp.name, format="mp3")
            tmp_path = tmp.name

        try:
            chunk_result = _transcribe_single(tmp_path, language)

            if not detected_language:
                detected_language = chunk_result["language"]

            for seg in chunk_result["segments"]:
                all_segments.append({
                    "start": seg["start"] + offset_seconds,
                    "end": seg["end"] + offset_seconds,
                    "text": seg["text"],
                })
        finally:
            os.unlink(tmp_path)

    return {
        "segments": all_segments,
        "full_text": " ".join(s["text"] for s in all_segments),
        "language": detected_language,
        "duration": total_duration,
    }


def process_video(video_input: str, language: str = None, output_audio_path: str = None) -> dict:
    """
    Main function: process video from file path or YouTube URL.

    For YouTube URLs: Extracts transcript directly if available, otherwise
    downloads audio and transcribes with Whisper.
    For local files: Uses Whisper API for transcription.

    Args:
        video_input: File path or YouTube URL
        language: Optional language code
        output_audio_path: Optional path for extracted audio

    Returns:
        Dictionary with title, duration, transcript, source info, and fallback_used flag
    """
    if is_url(video_input):
        if is_youtube_url(video_input):
            print("Checking for YouTube captions...")
            result = extract_youtube_transcript(video_input, language or "en")

            if result is not None:
                # Captions found - use them (fast and free)
                return {
                    "source": "youtube",
                    "video_url": video_input,
                    "title": result["title"],
                    "duration": result["duration"],
                    "transcript": {
                        "segments": result["segments"],
                        "full_text": result["full_text"],
                        "language": result["language"],
                        "duration": result["duration"],
                    },
                    "fallback_used": False,
                }
            else:
                # No captions - fallback to downloading audio and transcribing
                print("No captions available. Downloading audio for transcription...")

                # Get video info for title/duration
                import yt_dlp
                with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                    info = ydl.extract_info(video_input, download=False)
                    title = info.get("title", "Untitled Video")
                    duration = info.get("duration", 0)

                # Generate audio path if not provided
                if not output_audio_path:
                    safe_title = re.sub(r'[^\w\s-]', '', title)[:50]
                    output_audio_path = str(OUTPUT_DIR / f"{safe_title}_audio.mp3")

                print("Downloading YouTube audio...")
                audio_info = download_youtube_audio(video_input, output_audio_path)

                print("Transcribing audio with Whisper (this may take a few minutes)...")
                transcript = transcribe_audio(audio_info["audio_path"], language)

                return {
                    "source": "youtube",
                    "video_url": video_input,
                    "audio_path": audio_info["audio_path"],
                    "title": title,
                    "duration": duration,
                    "transcript": transcript,
                    "fallback_used": True,
                }
        else:
            raise ValueError(
                f"Only YouTube URLs are supported. "
                f"For other video sources, download the file and provide the local path."
            )
    else:
        # Local file processing
        video_path = validate_video_path(video_input)
        title = get_video_title(video_path)
        duration = get_video_duration(video_path)

        print("Extracting audio...")
        audio_path = extract_audio(video_path, output_audio_path)

        print("Transcribing audio (this may take a few minutes)...")
        transcript = transcribe_audio(audio_path, language)

        return {
            "source": "local",
            "video_path": video_path,
            "audio_path": audio_path,
            "title": title,
            "duration": duration,
            "transcript": transcript,
        }


def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def extract_video_id(url: str) -> str:
    """Extract video ID from YouTube URL."""
    parsed = urlparse(url)

    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        return parsed.path.lstrip("/")

    if parsed.netloc in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[2]
        elif parsed.path.startswith("/v/"):
            return parsed.path.split("/")[2]

    return None


def generate_youtube_timestamp_link(video_url: str, start_time: float) -> str:
    """
    Generate a YouTube link that starts at a specific timestamp.

    Args:
        video_url: Original YouTube URL
        start_time: Start time in seconds

    Returns:
        YouTube URL with timestamp parameter
    """
    video_id = extract_video_id(video_url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {video_url}")

    start_seconds = int(start_time)
    return f"https://www.youtube.com/watch?v={video_id}&t={start_seconds}"


def generate_youtube_embed_link(video_url: str, start_time: float, end_time: float = None) -> str:
    """
    Generate a YouTube embed link with start (and optional end) time.

    Args:
        video_url: Original YouTube URL
        start_time: Start time in seconds
        end_time: Optional end time in seconds

    Returns:
        YouTube embed URL with time parameters
    """
    video_id = extract_video_id(video_url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {video_url}")

    start_seconds = int(start_time)
    url = f"https://www.youtube.com/embed/{video_id}?start={start_seconds}"

    if end_time:
        end_seconds = int(end_time)
        url += f"&end={end_seconds}"

    return url


def generate_youtube_snippet_links(video_url: str, start_time: float, end_time: float) -> dict:
    """
    Generate all YouTube link formats for a snippet.

    Args:
        video_url: Original YouTube URL
        start_time: Start time in seconds
        end_time: End time in seconds

    Returns:
        Dictionary with different link formats
    """
    video_id = extract_video_id(video_url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {video_url}")

    start_seconds = int(start_time)
    end_seconds = int(end_time)

    return {
        "watch_url": f"https://www.youtube.com/watch?v={video_id}&t={start_seconds}",
        "short_url": f"https://youtu.be/{video_id}?t={start_seconds}",
        "embed_url": f"https://www.youtube.com/embed/{video_id}?start={start_seconds}&end={end_seconds}",
        "start_time": start_seconds,
        "end_time": end_seconds,
        "duration": end_seconds - start_seconds,
        "timestamp_display": f"{format_timestamp(start_time)} - {format_timestamp(end_time)}",
    }


def create_video_snippet(video_path: str, start_time: float, end_time: float, output_name: str = None, output_path: str = None) -> str:
    """
    Cut a snippet from a local video file.

    Args:
        video_path: Source video path
        start_time: Start time in seconds
        end_time: End time in seconds
        output_name: Optional output filename (used with default OUTPUT_DIR)
        output_path: Optional full output path (overrides output_name)

    Returns:
        Path to created snippet
    """
    if output_path:
        final_path = output_path
    elif output_name:
        final_path = str(OUTPUT_DIR / output_name)
    else:
        final_path = str(OUTPUT_DIR / f"snippet_{int(start_time)}-{int(end_time)}.mp4")

    video = VideoFileClip(video_path)
    end_time = min(end_time, video.duration)
    start_time = max(0, start_time)

    # Handle both moviepy 1.x (subclip) and 2.x (subclipped) APIs
    if hasattr(video, 'subclip'):
        clip = video.subclip(start_time, end_time)  # moviepy 1.x
    else:
        clip = video.subclipped(start_time, end_time)  # moviepy 2.x

    clip.write_videofile(final_path, codec="libx264", audio_codec="aac", logger=None)

    clip.close()
    video.close()

    return final_path
