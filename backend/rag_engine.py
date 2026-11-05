"""
RAG (Retrieval-Augmented Generation) engine module.
Handles: indexing transcripts, semantic search, finding relevant timestamps.
"""

import os
import hashlib
from pathlib import Path

import chromadb
from chromadb.config import Settings
from openai import OpenAI

# Initialize clients
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
EMBEDDING_MODEL = "text-embedding-3-small"

# ChromaDB setup
CHROMA_DIR = Path("output/chroma_db")
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

chroma_client = chromadb.PersistentClient(
    path=str(CHROMA_DIR),
    settings=Settings(anonymized_telemetry=False),
)

def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings using OpenAI API.

    Args:
        texts: List of text strings to embed

    Returns:
        List of embedding vectors
    """
    batch_size = 100
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=batch,
        )
        batch_embeddings = [item.embedding for item in response.data]
        all_embeddings.extend(batch_embeddings)

    return all_embeddings


def chunk_transcript(transcript: dict, min_chunk_length: int = 100) -> list[dict]:
    """
    Chunk transcript into searchable segments.

    Args:
        transcript: Transcript dict with 'segments' list
        min_chunk_length: Minimum characters per chunk

    Returns:
        List of chunks with text, start, end
    """
    chunks = []
    current_texts = []
    current_start = 0
    current_end = 0

    for i, seg in enumerate(transcript["segments"]):
        if not current_texts:
            current_start = seg["start"]

        current_texts.append(seg["text"])
        current_end = seg["end"]

        chunk_text = " ".join(current_texts)

        # Create chunk if long enough or last segment
        if len(chunk_text) >= min_chunk_length or i == len(transcript["segments"]) - 1:
            chunks.append({
                "text": chunk_text,
                "start": current_start,
                "end": current_end,
            })
            current_texts = []

    return chunks


def index_transcript(transcript: dict, video_id: str = None) -> str:
    """
    Index a transcript for semantic search.

    Args:
        transcript: Transcript dict from video_transcriber
        video_id: Optional unique ID for the video

    Returns:
        Collection name for the indexed transcript
    """
    # Generate video ID from content if not provided
    if not video_id:
        content_hash = hashlib.md5(transcript["full_text"][:1000].encode()).hexdigest()
        video_id = f"video_{content_hash[:12]}"

    collection_name = f"transcript_{video_id}"

    # Get or create collection
    collection = chroma_client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # Clear existing documents
    existing = collection.get()
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    # Chunk transcript
    # Empty text (music, silence) is rejected by the embeddings API and is useless for search.
    chunks = [c for c in chunk_transcript(transcript) if c["text"].strip()]
    if not chunks:
        return collection_name

    # Prepare data
    documents = [c["text"] for c in chunks]
    metadatas = [{"start": c["start"], "end": c["end"]} for c in chunks]
    ids = [f"chunk_{i}" for i in range(len(chunks))]

    # Generate embeddings
    embeddings = generate_embeddings(documents)

    # Add to collection
    collection.add(
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
        ids=ids,
    )

    return collection_name


def search(query: str, collection_name: str, n_results: int = 5) -> list[dict]:
    """
    Search for relevant transcript segments.

    Args:
        query: Search query
        n_results: Number of results to return
        collection_name: Collection to search (one per video; never shared state)

    Returns:
        List of results with text, start, end, relevance
    """
    if not collection_name:
        raise ValueError("collection_name is required.")

    collection = chroma_client.get_collection(collection_name)

    # Generate query embedding
    query_embedding = generate_embeddings([query])[0]

    # Search
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    # Format results
    formatted = []
    for i in range(len(results["ids"][0])):
        formatted.append({
            "text": results["documents"][0][i],
            "start": results["metadatas"][0][i]["start"],
            "end": results["metadatas"][0][i]["end"],
            "relevance": 1 - results["distances"][0][i],
        })

    return formatted


def find_timestamps_for_topic(topic: str, collection_name: str, n_results: int = 1) -> list[dict]:
    """
    Find timestamp ranges for a given topic.

    Args:
        topic: Topic to search for
        n_results: Number of timestamp ranges to return

    Returns:
        List of {start, end, text} dicts
    """
    results = search(topic, collection_name, n_results=n_results)
    return [{"start": r["start"], "end": r["end"], "text": r["text"]} for r in results]


def get_context_for_query(query: str, collection_name: str, n_results: int = 3) -> str:
    """
    Get relevant context text for a query (for LLM prompting).

    Args:
        query: The query
        n_results: Number of context chunks

    Returns:
        Combined context text with timestamps
    """
    results = search(query, collection_name, n_results=n_results)

    context_parts = []
    for r in results:
        timestamp = f"[{_format_time(r['start'])} - {_format_time(r['end'])}]"
        context_parts.append(f"{timestamp}\n{r['text']}")

    return "\n\n".join(context_parts)


def list_collections() -> list[str]:
    """List all indexed video collections."""
    collections = chroma_client.list_collections()
    return [c.name for c in collections]


def delete_collection(collection_name: str):
    """Delete an indexed video collection."""
    chroma_client.delete_collection(collection_name)


def collection_exists(collection_name: str) -> bool:
    """Check if a collection exists."""
    try:
        chroma_client.get_collection(collection_name)
        return True
    except Exception:
        return False


def ensure_collection_indexed(collection_name: str, transcript: dict) -> str | None:
    """
    Ensure a collection exists, re-indexing from transcript if needed.

    Returns:
        The collection name to search, or None if it could not be (re)built.
    """
    if collection_exists(collection_name):
        return collection_name

    # Collection missing - re-index from transcript
    if not transcript or not transcript.get("segments"):
        return None

    # Extract video_id from collection name (format: transcript_video_xxxx)
    video_id = collection_name.replace("transcript_", "")
    return index_transcript(transcript, video_id)


def _format_time(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
