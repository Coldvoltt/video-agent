"""
Query handler module.
Handles: understanding user intent, generating responses, creating helper documents.
"""

import os
import json

from openai import OpenAI

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
