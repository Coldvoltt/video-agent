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
- **B-roll**: the editor reads your transcript, works out what each moment is about, searches Pixabay with its own keywords and places only clips whose thumbnails clearly match; also your own cutaway clips (auto split into described shots) and AI-generated stills with camera motion.
- **Multicam**: sync 2-3 camera angles of the same take (by their transcripts and sound), trimmed to the same length, with the clearest audio (or your separate recorder); the editor cuts between cameras at natural pauses, or puts a chosen camera on a moment.
- **Plans and vid-coins**: Free (2 coins a month, 1 camera angle, 1 B-roll cutaway per project, Karaoke and Minimal captions), Basic ($3 or ₦4,500: 10 coins for a month, 2 angles, unlimited B-roll, every caption style and font) and Pro ($10 or ₦15,000: 25 coins for 3 months, 3 angles, AI background removal, AI B-roll images). One coin per started minute of a full-quality export; previews and edits are free. Payments are a demo for now (no money moves).
- **Accounts and dashboard**: sign in or create an account when you start editing (what you picked is kept, and anything you'd started moves into the account); a dashboard at `/dashboard` with your projects and settings, including Telegram (coming soon).
- **Caption fonts**: 10 bundled fonts (clean, condensed, comic, marker, script, serif) for captions and text, and important words drawn in their own font, colour and size; ask in chat ("make the key words pop in a comic font") or set it in the Captions panel.
- **Effects**: video noise removal, film grain, and blur (whole frame, or a region to hide a face, screen or plate).
- **Emphasis**: keyword pops, punch-in zooms on key moments, 7 built-in sound effects placed automatically, and one-click audio polish (social loudness, noise reduction, clearer voice).
- **Stickers**: 7 animated callouts (circle, underline, box, arrow, check, cross, sparkle) in any colour, animated emoji, and your own Lottie `.json` animations; the editor can place them on cue ("an arrow when I mention the elephants").
- **Backgrounds**: blur, recolour or replace what's behind you (with B-roll, a stock clip or an AI image), using an AI cut-out (MODNet, with clean edge colours) or a soft green/blue-screen key that keeps hair detail. The first preview after a cut-out takes a while; later ones reuse it.
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

- Accounts are email and password only (no "Sign in with Google" or password reset email yet). Set `EDITOR_REQUIRE_ACCOUNT=1` to refuse anonymous use of the API; without it, people who haven't signed in can still open projects they started before, but must sign in to start a new one.
- SQLite, local disk and a single worker: fine for one machine. Postgres, object storage and a Redis queue are planned for hosting.
- The live preview is approximate (no transitions, LUTs, video overlays or ducking); the rendered preview is exact.
- Only add YouTube videos you own or have rights to edit.
- Background removal runs on the CPU, once per clip range: about 9 minutes per minute of footage for the AI cut-out, 2 for a green screen. It downloads a 26 MB model on first use.
- Animated emoji are Noto Animated Emoji by Google, CC BY 4.0; credit them where you publish. Sticker rendering uses rlottie (LGPL-2.1) through `rlottie-python`.
