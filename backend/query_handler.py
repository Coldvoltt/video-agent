"""
Query handler module.
Handles: understanding user intent, generating responses, creating helper documents.
"""

import os
import json
from pathlib import Path

from openai import OpenAI
from fpdf import FPDF

import rag_engine

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# GPT model - gpt-5-mini for all tasks (400k context, fast, cost-efficient)
GPT_MODEL = "gpt-5-mini"


def detect_intent(user_query: str) -> dict:
    """
    Detect user's intent from their query.

    Args:
        user_query: The user's natural language query

    Returns:
        Dictionary with intent type and extracted parameters
        Intent types: search, question, snippet, summary, keypoints
    """
    system_prompt = """You are an intent classifier for a video analysis system.
Classify the user's intent into one of these categories:

1. "search" - User wants to find specific content/timestamps in the video
2. "question" - User is asking a question about the video content
3. "snippet" - User wants to create a video clip about a specific topic
4. "summary" - User wants a summary or overview of the video
5. "keypoints" - User wants key points or main takeaways

Respond with JSON:
{
    "intent": "<intent_type>",
    "topic": "<extracted topic or question>",
    "parameters": {
        "max_duration": <number if mentioned, null otherwise>,
        "detail_level": "<brief|detailed if mentioned, null otherwise>"
    }
}"""

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {"role": "developer", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        reasoning_effort="minimal",  # Fast classification task
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)


def answer_question(question: str, transcript: dict = None, conversation_history: list = None) -> str:
    """
    Answer a question about the video content.

    Args:
        question: User's question
        transcript: Optional full transcript dict
        conversation_history: Optional list of previous messages

    Returns:
        Answer string
    """
    # Get relevant context from RAG
    context = rag_engine.get_context_for_query(question, n_results=5)

    system_prompt = """You are a helpful assistant answering questions about video content.
Use the provided transcript context to answer questions accurately.
Always reference timestamps when relevant.
If the information isn't in the context, say so."""

    messages = [{"role": "developer", "content": system_prompt}]

    # Add conversation history if provided
    if conversation_history:
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": f"Context from video:\n{context}\n\nQuestion: {question}"})

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=messages,
        reasoning_effort="low",  # Q&A needs some reasoning
    )

    return response.choices[0].message.content


def generate_summary(transcript: dict) -> str:
    """
    Generate a brief summary of the video.

    Args:
        transcript: Full transcript dict

    Returns:
        Summary string
    """
    # GPT-5 has 256k context, but still truncate very long transcripts for cost efficiency
    text = transcript["full_text"]
    if len(text) > 100000:
        text = text[:100000] + "..."

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {
                "role": "developer",
                "content": "Summarize this video transcript in 2-3 concise paragraphs. Focus on the main topics and key information.",
            },
            {"role": "user", "content": text},
        ],
        reasoning_effort="low",
    )

    return response.choices[0].message.content


def generate_helper_document(transcript: dict, video_title: str) -> dict:
    """
    Generate a comprehensive helper document with key points.

    Args:
        transcript: Full transcript dict
        video_title: Title of the video

    Returns:
        Dictionary with overview, key_points, action_items
    """
    # Format transcript with timestamps
    formatted = _format_transcript_with_timestamps(transcript)

    # GPT-5 has 256k context, but truncate extremely long transcripts for cost
    if len(formatted) > 200000:
        formatted = formatted[:200000] + "\n...[truncated]"

    system_prompt = """You are an expert at analyzing video content and extracting key information.
Create a comprehensive helper document from this video transcript.

Respond with JSON:
{
    "overview": "2-3 paragraph overview of the video",
    "key_points": [
        {
            "title": "Brief title (5-10 words)",
            "summary": "Detailed explanation (2-4 sentences)",
            "timestamp_start": <start time in seconds>,
            "timestamp_end": <end time in seconds>,
            "importance": "high" | "medium" | "low"
        }
    ],
    "action_items": ["List of actionable takeaways"]
}

Extract 5-15 key points depending on video length.
Ensure timestamps accurately reflect where each point is discussed."""

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {"role": "developer", "content": system_prompt},
            {
                "role": "user",
                "content": f"Video: {video_title}\nDuration: {transcript['duration']:.0f}s\n\nTranscript:\n{formatted}",
            },
        ],
        response_format={"type": "json_object"},
    )

    result = json.loads(response.choices[0].message.content)
    result["title"] = video_title
    result["duration"] = transcript["duration"]

    return result


def format_helper_document_markdown(doc: dict) -> str:
    """
    Format helper document as markdown.

    Args:
        doc: Helper document dict from generate_helper_document

    Returns:
        Markdown string
    """
    md = f"# {doc['title']}\n\n"
    md += f"**Duration:** {_format_duration(doc['duration'])}\n\n"
    md += f"## Overview\n\n{doc['overview']}\n\n"
    md += "## Key Points\n\n"

    for i, kp in enumerate(doc["key_points"], 1):
        timestamp = _format_time(kp["timestamp_start"])
        md += f"### {i}. {kp['title']}\n"
        md += f"**Timestamp:** {timestamp} | **Importance:** {kp['importance']}\n\n"
        if kp.get("screenshot_url"):
            md += f"![Screenshot at {timestamp}]({kp['screenshot_url']})\n\n"
        md += f"{kp['summary']}\n\n"

    if doc.get("action_items"):
        md += "## Action Items\n\n"
        for item in doc["action_items"]:
            md += f"- {item}\n"

    return md


def handle_user_query(
    user_query: str,
    transcript: dict = None,
    video_path: str = None,
    conversation_history: list = None,
) -> dict:
    """
    Main function: process user query and return appropriate response.

    Args:
        user_query: Natural language query from user
        transcript: Full transcript dict (optional, for some intents)
        video_path: Path to video file (optional, for snippet creation)
        conversation_history: List of previous messages for context

    Returns:
        Dictionary with response type and content
    """
    # Detect intent
    intent_result = detect_intent(user_query)
    intent = intent_result["intent"]
    topic = intent_result["topic"]

    response = {"intent": intent, "query": user_query}

    if intent == "search":
        results = rag_engine.search(topic, n_results=5)
        response["results"] = results
        response["response"] = f"Found {len(results)} relevant segments for '{topic}'"

    elif intent == "question":
        answer = answer_question(topic, transcript, conversation_history)
        response["response"] = answer

    elif intent == "snippet":
        timestamps = rag_engine.find_timestamps_for_topic(topic, n_results=3)
        if timestamps:
            # Calculate optimal time range
            start = max(0, min(t["start"] for t in timestamps) - 2)
            end = max(t["end"] for t in timestamps) + 2

            max_duration = intent_result["parameters"].get("max_duration", 60)
            if end - start > max_duration:
                center = (timestamps[0]["start"] + timestamps[0]["end"]) / 2
                start = max(0, center - max_duration / 2)
                end = center + max_duration / 2

            response["timestamps"] = {"start": start, "end": end}
            response["context"] = timestamps[0]["text"]
            response["response"] = f"Found content about '{topic}' at {_format_time(start)}"

            # Create snippet if video_path provided
            if video_path:
                from video_transcriber import create_video_snippet
                snippet_path = create_video_snippet(video_path, start, end)
                response["snippet_path"] = snippet_path
        else:
            response["response"] = f"No content found for '{topic}'"
            response["timestamps"] = None

    elif intent == "summary":
        if transcript:
            summary = generate_summary(transcript)
            response["response"] = summary
        else:
            response["response"] = "Transcript required for summary"

    elif intent == "keypoints":
        if transcript:
            # Use a simpler key points extraction
            key_points = _extract_quick_keypoints(transcript)
            response["key_points"] = key_points
            response["response"] = f"Found {len(key_points)} key points from the video"
        else:
            response["response"] = "Transcript required for key points"

    return response


def _extract_quick_keypoints(transcript: dict) -> list[dict]:
    """Extract key points quickly without full helper document."""
    text = transcript["full_text"]
    if len(text) > 100000:
        text = text[:100000] + "..."

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {
                "role": "developer",
                "content": """Extract 5-10 key points from this transcript.
Return JSON: {"key_points": [{"title": "...", "summary": "..."}]}""",
            },
            {"role": "user", "content": text},
        ],
        reasoning_effort="low",
        response_format={"type": "json_object"},
    )

    result = json.loads(response.choices[0].message.content)
    return result.get("key_points", [])


def generate_howto_guides(transcript: dict, video_title: str) -> list[dict]:
    """
    Extract step-by-step how-to guides from a video transcript.

    Returns a list of guides, each with title, description, steps, and timestamps.
    """
    formatted = _format_transcript_with_timestamps(transcript)

    if len(formatted) > 200000:
        formatted = formatted[:200000] + "\n...[truncated]"

    system_prompt = """You are an expert at analyzing video content and extracting actionable how-to guides.
From the transcript, identify 3-10 distinct how-to guides — practical, step-by-step procedures
that a viewer could follow.

Respond with JSON:
{
    "guides": [
        {
            "title": "How to <do something>",
            "description": "Brief 1-2 sentence summary of what this guide covers",
            "steps": [
                {
                    "step_number": 1,
                    "instruction": "Short imperative instruction (e.g. 'Open the settings panel')",
                    "detail": "Optional extra detail or explanation (1-2 sentences)",
                    "timestamp": <approximate timestamp in seconds where this step is discussed>
                }
            ],
            "timestamp_start": <start time in seconds>,
            "timestamp_end": <end time in seconds>
        }
    ]
}

Rules:
- Each guide must have at least 2 steps
- Instructions should be clear and actionable
- Only extract guides that are genuinely taught or demonstrated in the video
- Order guides by their appearance in the video
- If the video has fewer than 3 extractable guides, return fewer"""

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {"role": "developer", "content": system_prompt},
            {
                "role": "user",
                "content": f"Video: {video_title}\nDuration: {transcript['duration']:.0f}s\n\nTranscript:\n{formatted}",
            },
        ],
        response_format={"type": "json_object"},
    )

    result = json.loads(response.choices[0].message.content)
    return result.get("guides", [])


def generate_custom_howto(transcript: dict, video_title: str, user_query: str) -> dict:
    """
    Generate a single how-to guide from a user's natural language query.

    Returns a guide dict with confidence level and optional note.
    """
    formatted = _format_transcript_with_timestamps(transcript)

    if len(formatted) > 200000:
        formatted = formatted[:200000] + "\n...[truncated]"

    system_prompt = """You are an expert at analyzing video content and creating how-to guides.
The user wants a specific how-to guide based on the video transcript.

Respond with JSON:
{
    "guide": {
        "title": "How to <topic>",
        "description": "Brief summary of what this guide covers",
        "steps": [
            {
                "step_number": 1,
                "instruction": "Short imperative instruction",
                "detail": "Optional extra detail (1-2 sentences)",
                "timestamp": <timestamp in seconds or null>
            }
        ],
        "timestamp_start": <start time in seconds or null>,
        "timestamp_end": <end time in seconds or null>
    },
    "confidence": "high" | "medium" | "low",
    "note": "Optional note if the topic is only partially covered or not found"
}

Rules:
- If the topic IS covered in the video, extract accurate steps with timestamps
- If partially covered, do your best and set confidence to "medium" with a note
- If NOT covered at all, return an empty steps array, confidence "low", and a helpful note
- Instructions should be clear and actionable"""

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {"role": "developer", "content": system_prompt},
            {
                "role": "user",
                "content": f"Video: {video_title}\nDuration: {transcript['duration']:.0f}s\n\nUser query: {user_query}\n\nTranscript:\n{formatted}",
            },
        ],
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)


def _format_transcript_with_timestamps(transcript: dict) -> str:
    """Format transcript with timestamps for LLM."""
    lines = []
    for seg in transcript["segments"]:
        timestamp = f"[{_format_time(seg['start'])} - {_format_time(seg['end'])}]"
        lines.append(f"{timestamp} {seg['text']}")
    return "\n".join(lines)


def _format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_duration(seconds: float) -> str:
    """Format duration for display."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def _sanitize_for_pdf(text: str) -> str:
    """Replace characters outside latin-1 range for built-in PDF fonts."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ----------------------------------------------------------------
# PDF elaboration — LLM expands each section into rich content
# ----------------------------------------------------------------

def _elaborate_overview(title: str, overview: str) -> str:
    """Ask the LLM to expand a brief overview into 4-6 detailed paragraphs."""
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {
                "role": "developer",
                "content": (
                    "You are writing a professional PDF report about a video. "
                    "Expand the overview below into a rich, detailed 4-6 paragraph summary. "
                    "Use your knowledge to add helpful context, explain terminology, "
                    "and make the section informative even for readers who haven't watched the video. "
                    "Return plain text only — no markdown formatting."
                ),
            },
            {
                "role": "user",
                "content": f"Video title: {title}\n\nOriginal overview:\n{overview}",
            },
        ],
    )
    return response.choices[0].message.content


def _elaborate_key_point(title: str, video_title: str, summary: str) -> str:
    """Ask the LLM to expand a key-point summary into a detailed section."""
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {
                "role": "developer",
                "content": (
                    "You are writing a section of a professional PDF report about a video. "
                    "Expand the key-point summary below into 3-5 detailed paragraphs. "
                    "Explain concepts in depth, provide background context from your own knowledge, "
                    "give examples where helpful, and make the section valuable as a standalone read. "
                    "Return plain text only — no markdown formatting."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Video: {video_title}\n"
                    f"Section title: {title}\n\n"
                    f"Original summary:\n{summary}"
                ),
            },
        ],
    )
    return response.choices[0].message.content


def _elaborate_action_items(title: str, action_items: list[str]) -> list[str]:
    """Ask the LLM to expand terse action items into detailed descriptions."""
    items_text = "\n".join(f"- {item}" for item in action_items)
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {
                "role": "developer",
                "content": (
                    "You are writing the action-items section of a professional PDF report. "
                    "For each action item below, expand it into 2-3 sentences that explain "
                    "what to do, why it matters, and any helpful tips. "
                    "Return a JSON object: {\"items\": [\"expanded item 1\", ...]}. "
                    "Keep the same order and count."
                ),
            },
            {
                "role": "user",
                "content": f"Video: {title}\n\nAction items:\n{items_text}",
            },
        ],
        response_format={"type": "json_object"},
    )
    result = json.loads(response.choices[0].message.content)
    return result.get("items", action_items)


def elaborate_document_for_pdf(
    doc: dict,
    selected_sections: list[str],
) -> dict:
    """
    Call the LLM to elaborate every selected section of a helper document.

    All LLM calls run concurrently to minimize total latency.
    Returns a new doc dict with elaborated text replacing the originals.
    Only selected sections are elaborated (saves tokens).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    elaborated = {
        "title": doc["title"],
        "duration": doc.get("duration"),
        "overview": doc.get("overview", ""),
        "key_points": [dict(kp) for kp in doc.get("key_points", [])],
        "action_items": list(doc.get("action_items", [])),
    }

    video_title = doc["title"]
    futures = {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        # Overview
        if "overview" in selected_sections and elaborated["overview"]:
            print("[PDF] Elaborating overview...")
            futures[pool.submit(_elaborate_overview, video_title, elaborated["overview"])] = "overview"

        # Key points
        for i, kp in enumerate(elaborated["key_points"]):
            if f"key_point_{i}" in selected_sections and kp.get("summary"):
                print(f"[PDF] Elaborating key point {i}: {kp.get('title', '')}")
                futures[pool.submit(
                    _elaborate_key_point, kp.get("title", ""), video_title, kp["summary"]
                )] = ("key_point", i)

        # Action items
        if "action_items" in selected_sections and elaborated["action_items"]:
            print("[PDF] Elaborating action items...")
            futures[pool.submit(
                _elaborate_action_items, video_title, elaborated["action_items"]
            )] = "action_items"

        # Collect results as they complete
        for future in as_completed(futures):
            tag = futures[future]
            try:
                result = future.result()
                if tag == "overview":
                    elaborated["overview"] = result
                    print("[PDF] Overview elaboration done")
                elif isinstance(tag, tuple) and tag[0] == "key_point":
                    idx = tag[1]
                    elaborated["key_points"][idx]["elaborated_summary"] = result
                    print(f"[PDF] Key point {idx} elaboration done")
                elif tag == "action_items":
                    elaborated["action_items"] = result
                    print("[PDF] Action items elaboration done")
            except Exception as e:
                print(f"[PDF] Elaboration failed for {tag}: {e}")

    print("[PDF] All elaborations complete")
    return elaborated


# ----------------------------------------------------------------
# PDF rendering
# ----------------------------------------------------------------

def _pdf_embed_image(pdf: "FPDF", img_path: str):
    """Embed a single image left-aligned with text, respecting page breaks."""
    from PIL import Image as PILImage

    with PILImage.open(img_path) as pil_img:
        iw, ih = pil_img.size
    rendered_w = pdf.epw  # full text width
    rendered_h = rendered_w * (ih / iw)

    # If the image won't fit on the current page, start a new one
    space_left = pdf.h - pdf.get_y() - pdf.b_margin
    if rendered_h > space_left:
        pdf.add_page()

    pdf.image(img_path, x=pdf.l_margin, w=rendered_w)
    pdf.ln(4)


def generate_helper_document_pdf(
    doc: dict,
    selected_sections: list[str],
    screenshot_paths: dict[int, list[str]] | None = None,
) -> bytes:
    """
    Generate a formatted PDF for selected sections of a helper document.

    Screenshots are interspersed between paragraphs of text so they
    appear at contextually relevant positions in the report.

    Args:
        doc: Elaborated helper document dict
        selected_sections: List of section IDs to include.
        screenshot_paths: Mapping of key-point index -> list of on-disk
            screenshot file paths to embed in the PDF.

    Returns:
        PDF file contents as bytes
    """
    s = _sanitize_for_pdf
    screenshot_paths = screenshot_paths or {}

    # Dark grey used for all body text
    BODY_COLOR = (75, 75, 75)
    HEADING_COLOR = (50, 50, 50)
    ACCENT_COLOR = (99, 102, 241)
    MUTED_COLOR = (140, 140, 140)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_left_margin(20)
    pdf.set_right_margin(20)
    pdf.add_page()

    page_w = pdf.epw

    # ── Title ──
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*ACCENT_COLOR)
    pdf.multi_cell(0, 10, s(doc.get("title", "Helper Document")),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    if doc.get("duration"):
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED_COLOR)
        pdf.cell(0, 5, f"Duration: {_format_duration(doc['duration'])}",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    # Divider
    y = pdf.get_y()
    pdf.set_draw_color(210, 210, 210)
    pdf.line(pdf.l_margin, y, pdf.l_margin + page_w, y)
    pdf.ln(8)

    # ── Overview ──
    if "overview" in selected_sections and doc.get("overview"):
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*HEADING_COLOR)
        pdf.cell(0, 9, "Overview", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*BODY_COLOR)
        for para in doc["overview"].split("\n\n"):
            para = para.strip()
            if para:
                pdf.multi_cell(0, 5.5, s(para))
                pdf.ln(3)
        pdf.ln(4)

    # ── Key Points ──
    key_points = doc.get("key_points", [])
    has_key_points = any(
        f"key_point_{i}" in selected_sections for i in range(len(key_points))
    )

    if has_key_points:
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*HEADING_COLOR)
        pdf.cell(0, 9, "Key Points", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)

        for i, kp in enumerate(key_points):
            if f"key_point_{i}" not in selected_sections:
                continue

            # Key point title with background bar
            y_title = pdf.get_y()
            pdf.set_fill_color(245, 245, 250)
            pdf.rect(pdf.l_margin, y_title, page_w, 9, style="F")

            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*HEADING_COLOR)
            pdf.set_xy(pdf.l_margin + 3, y_title + 1)
            pdf.cell(0, 7, s(f"{i + 1}.  {kp.get('title', '')}"),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

            # ── Interleave text paragraphs and screenshots ──
            text = kp.get("elaborated_summary", kp.get("summary", ""))
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            if len(paragraphs) <= 1 and len(text) > 600:
                paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

            img_paths = screenshot_paths.get(i, [])

            if not paragraphs:
                for img_p in img_paths:
                    try:
                        _pdf_embed_image(pdf, img_p)
                    except Exception as e:
                        print(f"[PDF] Warning: failed to embed {img_p}: {e}")
            elif not img_paths:
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(*BODY_COLOR)
                for para in paragraphs:
                    pdf.multi_cell(0, 5.5, s(para))
                    pdf.ln(3)
            else:
                n_imgs = len(img_paths)
                n_paras = len(paragraphs)
                insert_after: dict[int, list[str]] = {}
                for img_idx in range(n_imgs):
                    para_idx = int((img_idx + 1) * n_paras / (n_imgs + 1))
                    para_idx = min(para_idx, n_paras - 1)
                    insert_after.setdefault(para_idx, []).append(img_paths[img_idx])

                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(*BODY_COLOR)
                for p_idx, para in enumerate(paragraphs):
                    pdf.multi_cell(0, 5.5, s(para))
                    pdf.ln(3)

                    for img_p in insert_after.get(p_idx, []):
                        try:
                            _pdf_embed_image(pdf, img_p)
                        except Exception as e:
                            print(f"[PDF] Warning: failed to embed {img_p}: {e}")

            pdf.ln(4)

    # ── Action Items ──
    if "action_items" in selected_sections and doc.get("action_items"):
        y = pdf.get_y()
        pdf.set_draw_color(210, 210, 210)
        pdf.line(pdf.l_margin, y, pdf.l_margin + page_w, y)
        pdf.ln(6)

        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*HEADING_COLOR)
        pdf.cell(0, 9, "Action Items", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        for idx, item in enumerate(doc["action_items"]):
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*BODY_COLOR)
            pdf.cell(8, 5.5, f"{idx + 1}.")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5.5, s(item))
            pdf.ln(2)

    return pdf.output()
