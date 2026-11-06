# VidAgent

An agentic video editor for content creators. Upload footage or paste a YouTube link, describe the edit in chat (or point at a reference video whose style you like), and get a rendered video back. You can also edit the timeline and captions by hand.

The roadmap and design decisions are in [`docs/PLAN.md`](docs/PLAN.md).

## Requirements

- Python 3.12, Node.js 18+ (yt-dlp also uses Node for YouTube)
- ffmpeg and ffprobe on `PATH` (with libass, which standard builds include)
- An OpenAI API key for transcription, the editing agent and reference analysis

## Setup

```bash
cd backend
pip install -r requirements.txt          # add -r requirements-dev.txt to run the tests
cp .env.example .env                     # then put your OPENAI_API_KEY in it

cd ../frontend
npm install
```

## Running it

Three processes, each in its own terminal:

```bash
# 1. API (from backend/)
uvicorn api:app --reload --port 8000

# 2. Worker: downloads, transcription, the editing agent, analysis, renders (from backend/)
python -m editor.worker

# 3. Frontend (from frontend/)
npm run dev                              # http://localhost:3000
```

The worker is required: without it uploads stay "waiting" and chat never answers. `--reload` only restarts the API; restart the worker yourself after backend changes. Check `http://localhost:8000/health` (ffmpeg and openai_key should be `true`). API docs: `http://localhost:8000/docs`.

## What it does

- **Footage**: uploads (video, audio, images, `.cube` LUTs) and YouTube links; word-level transcripts; portrait phone video handled correctly; small preview copies for the browser.
- **Chat editing**: cut pauses and filler words, short-form clips and "best moments", remove sections, change format (9:16, 16:9, 1:1, 4:5), with follow-ups.
- **B-roll**: your own cutaway clips (auto split into described shots), stock video from Pixabay (or Pexels), and AI-generated stills with camera motion; the editor places them on the words they illustrate.
- **Emphasis**: keyword pops, punch-in zooms on key moments, 7 built-in sound effects placed automatically, and one-click audio polish (social loudness, noise reduction, clearer voice).
- **Stickers**: 7 animated callouts (circle, underline, box, arrow, check, cross, sparkle) in any colour, animated emoji, and your own Lottie `.json` animations; the editor can place them on cue ("an arrow when I mention the elephants").
- **Backgrounds**: blur, recolour or replace what's behind you (with B-roll, a stock clip or an AI image), using an AI cut-out (MODNet) or a green/blue-screen key. The first preview after an AI cut-out takes a few minutes; later ones reuse it.
- **Colour looks**: 12 built-in looks (Teal & Orange, Warm Film, Noir, ...) previewed on your own footage, with a strength slider; each downloadable as a `.cube`. Your own `.cube` LUTs work too.
- **Styling**: animated captions (karaoke, pop, boxed, minimal), 23 transitions, colour presets and LUTs, titles and lower thirds, image/video overlays, music and sound effects with ducking under speech, punch-in zooms.
- **Reference videos**: add a video whose style you like; it's analysed (pacing, colour, captions, audio, framing) and the editor can apply that style to your footage.
- **Hand editing**: select a clip to trim, split at the playhead, reorder, delete, punch in or change its transition; edit or hide any caption line; restyle captions.
- **Preview and history**: instant live preview in the browser, exact rendered previews, full-quality export, undo/redo (Ctrl+Z / Ctrl+Shift+Z) and a plain-language "what changed" for every version.

## Tests

```bash
cd backend
python -m pytest                         # ffmpeg needed; no API key needed (the model is scripted)
```

## Layout

```
backend/
  api.py              FastAPI app (mounts editor/router.py under /v2)
  rag_engine.py       transcript search (Chroma)
  editor/
    router.py         HTTP API
    store.py, jobs.py SQLite data + job queue;  worker.py runs jobs
    media.py          probe, YouTube download, transcription, proxies
    timeline.py       timeline JSON schema (the agent/renderer contract)
    renderer.py       timeline -> ffmpeg;  captions.py: ASS captions/text
    agent.py, llm/    editing agent and model providers (OpenAI)
    analysis.py, style.py, colormatch.py   reference analysis and colour matching
    edits.py, diff.py pure edit helpers; plain-language version changes
  tests/
frontend/src/
  App.jsx, components/  workspace UI;  lib/  API client and edit helpers
```

## Known limits

- One user per browser (a random id in local storage); no real accounts yet.
- SQLite, local disk and a single worker: fine for one machine. Postgres, object storage and a Redis queue are planned for hosting.
- The live preview is approximate (no transitions, LUTs, video overlays or ducking); the rendered preview is exact.
- Only add YouTube videos you own or have rights to edit.
- Background removal runs on the CPU (about 7 minutes per minute of footage, once); it downloads a 26 MB model on first use.
- Animated emoji are Noto Animated Emoji by Google, CC BY 4.0; credit them where you publish. Sticker rendering uses rlottie (LGPL-2.1) through `rlottie-python`.
