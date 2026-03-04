"""
FastAPI endpoints for the Video AI Processor.
Multi-user support with SQLite database and user-isolated storage.
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

import video_transcriber
import rag_engine
import query_handler
import database as db


# === API Tags (Sections) ===

tags_metadata = [
    {
        "name": "1. Video Processing",
        "description": "Process videos from YouTube URLs or upload local files. Returns a `session_id` to use with other endpoints.",
    },
    {
        "name": "2. Helper Documents",
        "description": "Generate AI-powered summary documents with **key points**, **timestamps**, and **action items**.",
    },
    {
        "name": "2b. How-To Guides",
        "description": "Extract step-by-step how-to guides from the video transcript, or request custom guides.",
    },
    {
        "name": "3. Search & Query",
        "description": "Search the transcript or ask natural language questions. Queries require `user_id`, `session_id`, and `conversation_id`.",
    },
    {
        "name": "4. Snippets",
        "description": "Create video snippets/clips. YouTube returns **shareable timestamp links**. Local files create actual video files.",
    },
    {
        "name": "5. Transcript",
        "description": "Get the full transcript with timestamps.",
    },
    {
        "name": "6. Sessions",
        "description": "Manage your video sessions - list all or delete specific ones.",
    },
    {
        "name": "Health",
        "description": "System health checks.",
    },
]

app = FastAPI(
    title="Video AI Processor API",
    description="""
## AI-powered video processing for transcription, RAG search, and snippet creation.

### Quick Start
1. **Process a video** (provide `user_id`) → Get a `session_id`
2. **Query the video** (provide `user_id`, `session_id`, `conversation_id`) → Get AI responses

### Parameters
- **user_id**: Your unique identifier (you provide this)
- **session_id**: Returned when you process a video
- **conversation_id**: Your unique conversation identifier (you provide this, tracks query history)

### Features
- Extract transcripts from YouTube (no download needed)
- Generate helper documents with key points
- Semantic search through video content
- Create shareable video snippets with timestamps
- Ask natural language questions about videos
    """,
    version="2.0.0",
    openapi_tags=tags_metadata,
)

# CORS — allow frontend origins
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === Pydantic Models ===

class VideoUrlInput(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user_123",
            "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en"
        }
    })

    user_id: str
    video_url: str
    language: Optional[str] = "en"


class QueryInput(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user_123",
            "session_id": "abc123def456",
            "conversation_id": "conv_789xyz",
            "query": "What is the main topic of this video?"
        }
    })

    user_id: str
    session_id: str
    conversation_id: str
    query: str


class SearchInput(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user_123",
            "session_id": "abc123def456",
            "query": "machine learning",
            "n_results": 5
        }
    })

    user_id: str
    session_id: str
    query: str
    n_results: int = 5


class SnippetInput(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user_123",
            "session_id": "abc123def456",
            "query": "introduction",
            "max_duration": 60.0,
            "n_results": 5
        }
    })

    user_id: str
    session_id: str
    query: str
    max_duration: float = 60.0
    n_results: int = 5


class TimestampSnippetInput(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user_123",
            "session_id": "abc123def456",
            "start_time": 60.0,
            "end_time": 120.0
        }
    })

    user_id: str
    session_id: str
    start_time: float
    end_time: float
    output_name: Optional[str] = None


class SessionResponse(BaseModel):
    session_id: str
    title: str
    duration: float
    message: str


class HelperDocResponse(BaseModel):
    session_id: str
    title: str
    overview: str
    key_points: list
    action_items: list
    markdown: str


class HowToStep(BaseModel):
    step_number: int
    instruction: str
    detail: str = ""
    timestamp: Optional[float] = None


class HowToGuide(BaseModel):
    title: str
    description: str
    steps: list[HowToStep]
    timestamp_start: Optional[float] = None
    timestamp_end: Optional[float] = None


class HowToGuidesResponse(BaseModel):
    session_id: str
    guides: list[HowToGuide]


class CustomHowToInput(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user_123",
            "session_id": "abc123def456",
            "query": "How do I set up the development environment?"
        }
    })

    user_id: str
    session_id: str
    query: str


class CustomHowToResponse(BaseModel):
    guide: HowToGuide
    confidence: str
    note: Optional[str] = None


class PdfExportInput(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user_123",
            "session_id": "abc123def456",
            "selected_sections": ["overview", "key_point_0", "key_point_2", "action_items"],
            "title": "My Video",
            "overview": "Overview text...",
            "key_points": [],
            "action_items": [],
        }
    })

    user_id: str
    session_id: str
    selected_sections: list[str]
    title: str
    duration: Optional[float] = None
    overview: str = ""
    key_points: list = []
    action_items: list = []


# ============================================================
# 1. VIDEO PROCESSING
# ============================================================

@app.post("/process/url", response_model=SessionResponse, tags=["1. Video Processing"])
def process_video_url(input_data: VideoUrlInput):
    """
    Process a YouTube video URL.

    - Extracts transcript directly from YouTube if captions are available (free, instant)
    - Falls back to downloading audio and transcribing with Whisper if no captions (~$0.36/hr)
    - Indexes transcript for semantic search
    - Returns a `session_id` for use with other endpoints
    """
    try:
        # Generate audio path for fallback case
        audio_output_path = str(db.get_user_storage_path(input_data.user_id, "audio") / "youtube_audio.mp3")

        result = video_transcriber.process_video(
            input_data.video_url,
            language=input_data.language,
            output_audio_path=audio_output_path,
        )

        collection_name = rag_engine.index_transcript(result["transcript"])

        session_id = db.create_session(
            user_id=input_data.user_id,
            source=result.get("source", "youtube"),
            title=result["title"],
            duration=result["duration"],
            collection_name=collection_name,
            video_url=result.get("video_url"),
            audio_path=result.get("audio_path"),
        )

        db.save_transcript(session_id, result["transcript"])

        # Inform user if fallback was used
        if result.get("fallback_used"):
            message = "No captions available. Audio was downloaded and transcribed with Whisper."
        else:
            message = "Transcript extracted successfully from captions."

        return SessionResponse(
            session_id=session_id,
            title=result["title"],
            duration=result["duration"],
            message=message,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/process/upload", response_model=SessionResponse, tags=["1. Video Processing"])
async def process_video_upload(
    user_id: str = Query(..., description="Your unique user identifier"),
    file: UploadFile = File(...),
    language: Optional[str] = Query(None, description="Language code (e.g., 'en', 'es')"),
):
    """
    Upload and process a local video file.

    - Extracts audio and transcribes using OpenAI Whisper API
    - Indexes transcript for semantic search
    - Returns a `session_id` for use with other endpoints

    **Note:** This uses the Whisper API which has costs (~$0.36/hour of audio).
    """
    # Save to user-specific directory
    video_path = db.get_user_video_path(user_id, file.filename)
    with open(video_path, "wb") as f:
        content = await file.read()
        f.write(content)

    try:
        # Get user-specific audio output path
        video_name = Path(file.filename).stem
        audio_path = db.get_user_audio_path(user_id, video_name)

        result = video_transcriber.process_video(
            str(video_path),
            language=language,
            output_audio_path=str(audio_path),
        )

        collection_name = rag_engine.index_transcript(result["transcript"])

        session_id = db.create_session(
            user_id=user_id,
            source="local",
            title=result["title"],
            duration=result["duration"],
            collection_name=collection_name,
            video_path=str(video_path),
            audio_path=str(audio_path),
        )

        db.save_transcript(session_id, result["transcript"])

        return SessionResponse(
            session_id=session_id,
            title=result["title"],
            duration=result["duration"],
            message="Video processed successfully",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 2. HELPER DOCUMENTS
# ============================================================

@app.get("/document/{session_id}", response_model=HelperDocResponse, tags=["2. Helper Documents"])
def get_helper_document(
    session_id: str,
    request: Request,
    user_id: str = Query(..., description="Your unique user identifier"),
):
    """
    Generate a helper document with key points and screenshots.

    Returns:
    - **Overview**: Summary of the video
    - **Key Points**: Important topics with timestamps, importance levels, and screenshots
    - **Action Items**: Actionable takeaways
    - **Markdown**: Formatted document ready to save
    """
    session = db.get_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        doc = query_handler.generate_helper_document(
            session["transcript"],
            session["title"],
        )

        # Extract screenshots for each key point
        base_url = "/api"
        video_transcriber.extract_screenshots_for_key_points(
            key_points=doc["key_points"],
            session=session,
            user_id=user_id,
            base_url=base_url,
        )

        markdown = query_handler.format_helper_document_markdown(doc)

        return HelperDocResponse(
            session_id=session_id,
            title=doc["title"],
            overview=doc["overview"],
            key_points=doc["key_points"],
            action_items=doc.get("action_items", []),
            markdown=markdown,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/document/{session_id}/download", tags=["2. Helper Documents"])
def download_helper_document(
    session_id: str,
    request: Request,
    user_id: str = Query(..., description="Your unique user identifier"),
):
    """
    Download the helper document as a Markdown file (includes screenshot references).
    """
    session = db.get_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    doc = query_handler.generate_helper_document(
        session["transcript"],
        session["title"],
    )

    # Extract screenshots for each key point
    base_url = str(request.base_url).rstrip("/")
    video_transcriber.extract_screenshots_for_key_points(
        key_points=doc["key_points"],
        session=session,
        user_id=user_id,
        base_url=base_url,
    )

    markdown = query_handler.format_helper_document_markdown(doc)

    # Save to user-specific directory
    output_path = db.get_user_storage_path(user_id) / f"{session_id}_helper.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    return FileResponse(
        output_path,
        media_type="text/markdown",
        filename=f"{session['title']}_helper.md",
    )


@app.post("/document/pdf", tags=["2. Helper Documents"])
def export_helper_document_pdf(
    input_data: PdfExportInput,
):
    """
    Generate a richly elaborated PDF for selected helper-document sections.

    For each selected section the LLM expands the original summary into
    detailed, standalone paragraphs.  Multiple screenshots are extracted
    per key point (evenly spaced across the timestamp range).

    **selected_sections** can include:
    - `"overview"` — the video overview
    - `"key_point_0"`, `"key_point_1"`, ... — individual key points by index
    - `"action_items"` — the action items list
    """
    user_id = input_data.user_id
    session_id = input_data.session_id

    print(f"[PDF] Request received — session={session_id}, sections={input_data.selected_sections}")

    session = db.get_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        doc = {
            "title": input_data.title,
            "duration": input_data.duration,
            "overview": input_data.overview,
            "key_points": input_data.key_points,
            "action_items": input_data.action_items,
        }

        # 1. LLM elaboration — expand each selected section (concurrent)
        print("[PDF] Step 1/3: Elaborating sections via LLM...")
        elaborated = query_handler.elaborate_document_for_pdf(
            doc, input_data.selected_sections
        )

        # 2. Extract multiple screenshots per selected key point
        #    Resolve the frame source once (stream URL or storyboard) then reuse.
        #    Also checks for cached screenshots from helper doc generation.
        print("[PDF] Step 2/3: Extracting screenshots...")
        screenshot_dir = db.get_user_storage_path(user_id, "screenshots")
        screenshot_paths: dict[int, list[str]] = {}

        frame_source = video_transcriber.resolve_youtube_frame_source(session)

        for i, kp in enumerate(elaborated["key_points"]):
            if f"key_point_{i}" not in input_data.selected_sections:
                continue

            ts_start = kp.get("timestamp_start", 0)
            ts_end = kp.get("timestamp_end", ts_start)
            paths = []

            # Try extracting new screenshots
            if frame_source["method"] != "none":
                paths = video_transcriber.extract_multiple_screenshots(
                    session=session,
                    user_id=user_id,
                    timestamp_start=ts_start,
                    timestamp_end=ts_end,
                    session_id=session_id,
                    n_screenshots=3,
                    _frame_source=frame_source,
                )

            # Fallback: use cached screenshot from helper doc generation
            if not paths:
                midpoint = int((ts_start + ts_end) / 2)
                cached_name = f"screenshot_{session_id}_{midpoint}.jpg"
                cached_path = screenshot_dir / cached_name
                if cached_path.exists():
                    paths = [str(cached_path)]
                    print(f"[PDF]   Key point {i}: using cached screenshot")

            if paths:
                screenshot_paths[i] = paths
                print(f"[PDF]   Key point {i}: {len(paths)} screenshot(s)")
            else:
                print(f"[PDF]   Key point {i}: no screenshots available")

        # 3. Render PDF
        print("[PDF] Step 3/3: Rendering PDF...")
        pdf_bytes = query_handler.generate_helper_document_pdf(
            doc=elaborated,
            selected_sections=input_data.selected_sections,
            screenshot_paths=screenshot_paths,
        )
        print(f"[PDF] Done — {len(pdf_bytes)} bytes")

        safe_title = "".join(
            c if c.isalnum() or c in " _-" else "_" for c in input_data.title
        ).strip() or "helper_document"
        filename = f"{safe_title}_helper.pdf"

        return Response(
            content=bytes(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{__import__('urllib.parse', fromlist=['quote']).quote(filename)}"
            },
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/screenshots/{user_id}/{filename}", tags=["2. Helper Documents"])
def get_screenshot(user_id: str, filename: str):
    """
    Serve a screenshot image extracted from a video.

    Screenshots are generated when helper documents are created and cached
    for subsequent requests.
    """
    file_path = db.get_user_screenshot_path(user_id, filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")

    return FileResponse(
        file_path,
        media_type="image/jpeg",
        filename=filename,
    )


# ============================================================
# 2b. HOW-TO GUIDES
# ============================================================

@app.get("/howto/{session_id}", response_model=HowToGuidesResponse, tags=["2b. How-To Guides"])
def get_howto_guides(
    session_id: str,
    user_id: str = Query(..., description="Your unique user identifier"),
):
    """
    Generate step-by-step how-to guides from the video transcript.

    Extracts 3-10 actionable guides with numbered steps and timestamps.
    """
    session = db.get_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        guides = query_handler.generate_howto_guides(
            session["transcript"],
            session["title"],
        )
        return HowToGuidesResponse(session_id=session_id, guides=guides)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/howto/custom", response_model=CustomHowToResponse, tags=["2b. How-To Guides"])
def get_custom_howto(input_data: CustomHowToInput):
    """
    Generate a custom how-to guide from a user's natural language query.

    Returns a single guide with confidence level and optional note.
    """
    session = db.get_session(input_data.session_id, input_data.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        result = query_handler.generate_custom_howto(
            session["transcript"],
            session["title"],
            input_data.query,
        )
        return CustomHowToResponse(
            guide=result["guide"],
            confidence=result.get("confidence", "medium"),
            note=result.get("note"),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 3. SEARCH & QUERY
# ============================================================

@app.post("/search", tags=["3. Search & Query"])
def search_video(input_data: SearchInput):
    """
    Semantic search within the video transcript.

    Returns relevant segments with:
    - **Text**: The matching transcript text
    - **Start/End**: Timestamps in seconds
    - **Relevance**: Score from 0-1 (higher = more relevant)
    """
    session = db.get_session(input_data.session_id, input_data.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not rag_engine.ensure_collection_indexed(session["collection_name"], session.get("transcript")):
        raise HTTPException(status_code=500, detail="Failed to load or rebuild search index")

    try:
        results = rag_engine.search(input_data.query, n_results=input_data.n_results)
        return {
            "query": input_data.query,
            "results": results,
            "count": len(results),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/conversation/{conversation_id}/messages", tags=["3. Search & Query"])
def get_conversation_messages(
    conversation_id: str,
    user_id: str = Query(..., description="Your unique user identifier"),
    limit: int = Query(50, description="Maximum number of messages to return"),
):
    """
    Get messages from a conversation.

    Returns the conversation history for loading previous messages.
    """
    conversation = db.get_conversation(conversation_id, user_id)
    if not conversation:
        return {"messages": [], "count": 0}

    messages = db.get_messages(conversation_id, limit=limit)
    return {
        "conversation_id": conversation_id,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "count": len(messages),
    }


@app.post("/query", tags=["3. Search & Query"])
def handle_query(input_data: QueryInput):
    """
    Ask a natural language question about the video.

    The AI will:
    - Detect your intent (question, search, summary request, etc.)
    - Find relevant context from the transcript
    - Provide an answer with timestamp references

    The query and response are saved to the conversation history.
    Conversations are auto-created if the conversation_id doesn't exist.
    """
    session = db.get_session(input_data.session_id, input_data.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Auto-create conversation if it doesn't exist
    conversation = db.get_conversation(input_data.conversation_id)
    if not conversation:
        db.create_conversation_with_id(
            input_data.conversation_id,
            input_data.session_id,
            input_data.user_id,
        )

    if not rag_engine.ensure_collection_indexed(session["collection_name"], session.get("transcript")):
        raise HTTPException(status_code=500, detail="Failed to load or rebuild search index")

    try:
        # Get conversation history for context
        messages = db.get_messages(input_data.conversation_id, limit=10)

        result = query_handler.handle_user_query(
            input_data.query,
            transcript=session["transcript"],
            video_path=session.get("video_path"),
            conversation_history=messages,
        )

        # Save query and response to conversation
        db.add_message(input_data.conversation_id, "user", input_data.query)
        db.add_message(input_data.conversation_id, "assistant", result.get("response", str(result)))

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 4. SNIPPETS
# ============================================================

@app.post("/snippet/query", tags=["4. Snippets"])
def create_snippet_from_query(input_data: SnippetInput):
    """
    Create multiple video snippets based on a content query.

    For **YouTube videos**: Returns shareable timestamp links for each relevant segment.
    For **local files**: Creates actual video clip files.

    Each snippet includes:
    - **Relevance score**
    - **Context text**
    - **YouTube links** (watch, short, embed URLs) or local file path
    """
    session = db.get_session(input_data.session_id, input_data.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not rag_engine.ensure_collection_indexed(session["collection_name"], session.get("transcript")):
        raise HTTPException(status_code=500, detail="Failed to load or rebuild search index")

    try:
        results = rag_engine.search(input_data.query, n_results=input_data.n_results)

        if not results:
            raise HTTPException(status_code=404, detail="No relevant content found")

        snippets = []
        padding = 2.0

        is_youtube = session.get("source") == "youtube" and session.get("video_url")

        for i, result in enumerate(results):
            start = max(0, result["start"] - padding)
            end = result["end"] + padding

            if end - start > input_data.max_duration:
                center = (result["start"] + result["end"]) / 2
                start = max(0, center - input_data.max_duration / 2)
                end = center + input_data.max_duration / 2

            if is_youtube:
                links = video_transcriber.generate_youtube_snippet_links(
                    session["video_url"],
                    start,
                    end,
                )
                snippets.append({
                    "index": i + 1,
                    "relevance": round(result["relevance"], 3),
                    "context": result["text"],
                    "links": links,
                })
            else:
                output_name = f"snippet_{input_data.query[:20]}_{i+1}_{int(start)}-{int(end)}.mp4"
                output_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in output_name)

                snippet_path = db.get_user_snippet_path(input_data.user_id, output_name)
                video_transcriber.create_video_snippet(
                    session["video_path"],
                    start,
                    end,
                    output_path=str(snippet_path),
                )
                snippets.append({
                    "index": i + 1,
                    "relevance": round(result["relevance"], 3),
                    "context": result["text"],
                    "snippet_path": str(snippet_path),
                    "start_time": start,
                    "end_time": end,
                })

        return {
            "source": "youtube" if is_youtube else "local",
            "query": input_data.query,
            "total_snippets": len(snippets),
            "snippets": snippets,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/snippet/timestamp", tags=["4. Snippets"])
def create_snippet_from_timestamp(input_data: TimestampSnippetInput):
    """
    Create a video snippet from explicit start/end timestamps.

    For **YouTube videos**: Returns shareable timestamp links.
    For **local files**: Creates actual video clip file.
    """
    session = db.get_session(input_data.session_id, input_data.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        if session.get("source") == "youtube" and session.get("video_url"):
            links = video_transcriber.generate_youtube_snippet_links(
                session["video_url"],
                input_data.start_time,
                input_data.end_time,
            )
            return {
                "source": "youtube",
                "links": links,
            }
        else:
            output_name = input_data.output_name or f"snippet_{int(input_data.start_time)}-{int(input_data.end_time)}.mp4"
            snippet_path = db.get_user_snippet_path(input_data.user_id, output_name)

            video_transcriber.create_video_snippet(
                session["video_path"],
                input_data.start_time,
                input_data.end_time,
                output_path=str(snippet_path),
            )
            return {
                "source": "local",
                "snippet_path": str(snippet_path),
                "start_time": input_data.start_time,
                "end_time": input_data.end_time,
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/snippet/download/{user_id}/{filename}", tags=["4. Snippets"])
def download_snippet(user_id: str, filename: str):
    """
    Download a created snippet file (local files only).
    """
    file_path = db.get_user_snippet_path(user_id, filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Snippet not found")

    return FileResponse(file_path, media_type="video/mp4", filename=filename)


# ============================================================
# 5. TRANSCRIPT
# ============================================================

@app.get("/transcript/{session_id}", tags=["5. Transcript"])
def get_transcript(
    session_id: str,
    user_id: str = Query(..., description="Your unique user identifier"),
    with_timestamps: bool = Query(True, description="Include timestamps in response"),
):
    """
    Get the full transcript for a session.

    - **with_timestamps=true**: Returns segments with start/end times
    - **with_timestamps=false**: Returns plain text only
    """
    session = db.get_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    transcript = session["transcript"]

    if with_timestamps:
        return {
            "segments": transcript["segments"],
            "duration": transcript["duration"],
            "language": transcript["language"],
        }
    else:
        return {
            "text": transcript["full_text"],
            "duration": transcript["duration"],
            "language": transcript["language"],
        }


# ============================================================
# 6. SESSIONS
# ============================================================

@app.get("/sessions", tags=["6. Sessions"])
def list_sessions(user_id: str = Query(..., description="Your unique user identifier")):
    """
    List all your video sessions.
    """
    sessions = db.list_sessions(user_id)
    return {
        "sessions": [
            {
                "session_id": s["id"],
                "title": s["title"],
                "duration": s["duration"],
                "source": s["source"],
                "created_at": s["created_at"],
            }
            for s in sessions
        ],
        "total": len(sessions),
    }


@app.delete("/sessions/{session_id}", tags=["6. Sessions"])
def delete_session(
    session_id: str,
    user_id: str = Query(..., description="Your unique user identifier"),
):
    """
    Delete a session and all associated data: video file, audio file,
    snippets, ChromaDB collection, and database records.
    """
    session = db.get_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete ChromaDB collection
    try:
        rag_engine.delete_collection(session["collection_name"])
    except Exception:
        pass

    # Delete video file
    if session.get("video_path"):
        try:
            Path(session["video_path"]).unlink(missing_ok=True)
        except Exception:
            pass

    # Delete audio file
    if session.get("audio_path"):
        try:
            Path(session["audio_path"]).unlink(missing_ok=True)
        except Exception:
            pass

    # Delete user snippets directory
    try:
        snippets_dir = db.get_user_storage_path(user_id, "snippets")
        if snippets_dir.exists():
            import shutil
            shutil.rmtree(snippets_dir, ignore_errors=True)
    except Exception:
        pass

    # Delete screenshots for this session
    try:
        screenshots_dir = db.get_user_storage_path(user_id, "screenshots")
        if screenshots_dir.exists():
            for f in screenshots_dir.glob(f"screenshot_{session_id}_*.jpg"):
                f.unlink(missing_ok=True)
    except Exception:
        pass

    # Delete database records (cascades to transcript, conversations, messages)
    db.delete_session(session_id, user_id)

    return {"message": "Session and all associated files deleted", "session_id": session_id}


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health", tags=["Health"])
def health_check():
    """
    Check if the API is running and configured correctly.
    """
    return {
        "status": "healthy",
        "api_key_set": bool(os.getenv("OPENAI_API_KEY")),
    }


# ============================================================
# RUN SERVER
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
