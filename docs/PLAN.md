# Agentic Video Editor: Re-architecture Plan

_Last updated: 2026-11-05_

## 1. Goal and scope

Turn video-agent from a video Q&A tool into an agentic video editor. Creators upload footage or paste a YouTube link, describe what they want, optionally attach reference videos, and get back an edited video.

In scope:

- Clipping driven by requests ("best 3 moments", "cut the ums and silences", "60-second short")
- Transitions, color grading, animated captions and text
- User-provided illustrations, overlays and sound effects
- Style taken from reference videos ("in the style of", not a copy)
- Iterative follow-ups ("make the captions bigger")

Out of scope for now: generative video (AI-made footage), multi-camera sync, real-time collaborative editing.

## 2. Current state (before this work)

The backend is a video Q&A / RAG app (FastAPI, SQLite, Chroma, OpenAI). About 30% carries over.

| Module | Does today | Fate |
| --- | --- | --- |
| `video_transcriber.py` | YouTube captions, Whisper, frame grabs, MoviePy clipping | Removed; `editor/media.py` uses ffmpeg directly |
| `rag_engine.py` | Embeds transcript chunks in Chroma | Keep for "find the part where..." (agent tool) |
| `query_handler.py` | Intent classifier, helper docs, how-to guides, PDFs | Removed; replaced by the editing agent |
| `api.py` | Synchronous endpoints, uploads, sessions | Replaced by a thin shell around `editor/router.py` (`/v2`) |
| `database.py` | SQLite sessions, transcripts, chats | Removed; editor tables live in `editor/store.py` |

Blockers found:

1. Heavy work runs inside HTTP requests; renders take minutes.
2. YouTube ingest downloads audio only.
3. `rag_engine._current_collection_name` is a module global, so concurrent users can cross-query.
4. Uploads are read fully into memory.
5. Whisper is called with segment timestamps only; captions need word timestamps.
6. `backend/cookies.txt` holds live YouTube session cookies and is committed to git.

## 3. Target architecture

The LLM never edits video directly. It produces a **timeline JSON** (edit decision list); deterministic code validates and renders it.

```
 User request + uploaded assets + reference video(s)
         |
         v
 [Analysis]  word-level transcript, RAG index, scene cuts, silences,
         |   beats, color stats, vision model on sampled frames
         v
 [Agent]  LLM + tools (find_segments, add_cut, apply_transition,
         |  set_grade, style_captions, place_overlay, add_sfx ...)
         v
 [Timeline JSON]  versioned per project  <-- user edits / follow-up prompts
         |
         v
 [Validator]  schema + asset existence + time bounds
         |
         v
 [Job queue] --> [Render worker: ffmpeg filter graph]
                       |-- preview (480p, fast)
                       '-- final export
```

Every follow-up request is a new timeline version, so edits are cheap, undoable and debuggable.

## 4. Tech stack decisions

| Concern | Decision | Why |
| --- | --- | --- |
| Language | Python for API, agent, analysis, render orchestration | Best ecosystem for media analysis + LLMs; team already uses it |
| Rendering | ffmpeg filter graphs built from the timeline, run as a subprocess | MoviePy renders frame-by-frame in Python: too slow for layered effects |
| Captions | ASS subtitles rendered by libass (ffmpeg `ass` filter) | Word highlights, pops, outlines, positioning without a browser |
| Motion text (later) | Remotion (React) render service + `<Player>` preview | Rich kinetic text; reuses the React frontend; live in-browser preview |
| Job queue | DB-backed queue (`jobs` table, atomic claim) + `python -m editor.worker` | No new infrastructure; works on Windows dev and a single server; swap to Redis/Arq when scaling out |
| Database | SQLite (WAL mode) now, Postgres when multiple worker hosts exist | Same SQL; WAL lets API + worker share the file |
| Storage | `editor/storage.py` abstraction, local disk now, S3 later | Keeps paths out of business logic |
| Transcription | OpenAI `whisper-1` with `word` + `segment` timestamps | Word timing drives captions and filler/silence cuts |
| Agent model | Provider interface with two backends planned; **OpenAI first** (`EDITOR_AGENT_MODEL`, default `gpt-5-mini`) | Tools for timeline edits now, vision for reference analysis in Phase 4; second provider plugs into the same interface |

## 5. Timeline JSON (the contract)

Schema version 1, implemented in `backend/editor/timeline.py`. Unknown fields are rejected so new features fail loudly until the renderer supports them.

```json
{
  "version": 1,
  "output": { "width": 1080, "height": 1920, "fps": 30, "fit": "cover", "background": "black" },
  "clips": [
    { "id": "c1", "asset_id": "a1b2c3", "in": 12.5, "out": 31.0 },
    { "id": "c2", "asset_id": "a1b2c3", "in": 95.0, "out": 104.2, "volume": 0.9 }
  ]
}
```

- `clips` play back-to-back on the main track. `in`/`out` are seconds in the source asset.
- `fit`: `cover` crops to fill the frame (16:9 to 9:16 shorts), `contain` letterboxes.
- Image assets can be clips (held for `out - in` seconds).

Phase 3 fields (implemented; full definitions in `backend/editor/timeline.py`):

| Field | Time base | Shape |
| --- | --- | --- |
| `clips[].zoom` | per clip | punch-in factor 1.0-2.0, centred (Phase 4) |
| `clips[].transition_in` | between clips | `{ "type": "fade" \| other xfade name, "duration": 0.1-2 }` |
| `grade` | whole video | `{ "preset", "brightness", "contrast", "saturation", "temperature", "lut_asset_id" \| "lut_preset", "lut_strength" }` |
| `captions` | from transcript | `{ "style": "karaoke" \| "pop" \| "boxed" \| "minimal", "position", "size", "color", "highlight_color", "uppercase", "max_words" }` |
| `text[]` | output | `{ "id", "text", "start", "end", "style", "position", "size", "color", "animation" }` |
| `overlays[]` | output | `{ "id", "asset_id", "start", "end", "position", "scale", "opacity", "fade" }` |
| `audio[]` | output | `{ "id", "asset_id", "start", "in", "out", "volume", "fade_in", "fade_out", "loop", "duck" }` |

Still planned: `clips[].speed` (Phase 4/5).

## 6. Roadmap

Status key: [x] done, [~] in progress, [ ] not started.

### Phase 1: Foundation

- [x] Plan document (this file)
- [x] `cookies.txt` path configurable via `YTDLP_COOKIES_FILE`; file git-ignored (see the security note in section 7)
- [x] RAG: collection passed explicitly everywhere; module global removed
- [x] Editor data model: projects, assets, timeline versions, jobs (`editor/store.py`)
- [x] Storage abstraction (`editor/storage.py`)
- [x] DB-backed job queue + worker process (`editor/jobs.py`, `editor/worker.py`)
- [x] Ingest: streamed uploads, full-video YouTube download, ffprobe metadata
- [x] Word-level transcription + per-asset RAG index (needs `OPENAI_API_KEY`)
- [x] Timeline schema v1 + validator
- [x] ffmpeg renderer: cuts, concat, aspect fit, preview/final quality, progress reporting
- [x] `/v2` API: projects, assets, timeline versions, renders, jobs, search
- [x] Tests: schema, validator, queue, renderer on generated clips (26 passing)
- [x] Verified live: YouTube link -> worker download -> 9:16 preview + 1080x1920 final render
- [x] yt-dlp floor raised to 2026.10.26 (2026.03.03 got HTTP 403 from YouTube)
- [ ] Verify transcription + search with a real `OPENAI_API_KEY` (code done, untested without a key)
- [x] Removed EC2 deployment (GitHub Actions workflow) on request; hosting to be decided later

### Phase 2: Agentic clipping

- [x] Provider-neutral LLM interface (`editor/llm/`); OpenAI adapter (Chat Completions + function calling)
- [x] Agent loop (`editor/agent.py`) editing an in-memory draft; only a valid draft is saved as a new version
- [x] Tools: `list_assets`, `get_timeline`, `get_transcript` (paged, optional word timings), `search_transcripts`, `find_silences`, `set_timeline` (snaps cuts to word edges), `set_output_format`, `tighten_pacing`, `cut_ranges`
- [x] Silence (ffmpeg `silencedetect`) and filler-word removal done deterministically in `editor/edits.py`; Whisper prompted to keep "um"/"uh"
- [x] "Best N moments" / N-second short: model picks ranges from the transcript, tools enforce validity and report duration
- [x] Chat as a queued `agent` job + message history; auto preview render after each change
- [x] Tests with a scripted model and a stubbed OpenAI client (38 passing in total)
- [ ] Live evaluation with a real `OPENAI_API_KEY` on real creator footage; tune the prompt and `EDITOR_AGENT_MODEL` / `EDITOR_AGENT_REASONING`
- [x] First live run with OpenAI: real transcription, agent edits and preview renders on a real clip
- [x] Fixes from that run: pauses also detected from word gaps (background noise hid them from `silencedetect`); fillers longer than 0.8 s are kept (Whisper stretches them over real words); clip labels built from word timings; plainer, shorter replies
- [ ] Second model provider (adapter in `editor/llm/`)

### Frontend (replaced the Q&A UI, 2026-11-05)

- [x] Old Q&A screens and backend routes retired (uncommitted work saved in `git stash` entry "legacy WIP before UI replacement")
- [x] Workspace: media bin (upload, drag-and-drop, YouTube link, per-item status), preview stage (preview/final renders, export, download), timeline strip (clips sized by length and labelled with their words, click to seek), chat with the editing agent, version history with restore
- [x] Responsive down to phone width; keyboard focus visible; reduced motion respected
- [ ] Manual timeline edits (trim, reorder, delete clips by hand)
- [ ] Real accounts instead of a random id in local storage

### Phase 3: Styling (done 2026-11-05)

- [x] Transitions: 23 `xfade` types + `acrossfade`; overlaps handled in output timing (`Timeline.clip_spans()`); auto-shortened for short clips
- [x] Colour grading: 8 presets (natural, warm, cool, punchy, film, moody, bw, vintage) plus brightness/contrast/saturation/temperature; `.cube` 3D LUT upload (validated) via `lut3d`
- [x] Animated captions from word timings (`editor/captions.py`, ASS + libass): karaoke, pop, boxed, minimal; colour, highlight, size, position, uppercase, words per line; fillers left out; lines break at transcript segments; bundled OFL fonts (Montserrat ExtraBold, Anton) so output doesn't depend on server fonts
- [x] Text layers: title, label, lower third; 9 positions; fade, pop, slide-up, typewriter animations
- [x] Overlays: images or video (picture-in-picture), position, scale, opacity, fades
- [x] Music and sound effects: placement, trimming, looping, fades, ducking under speech (`sidechaincompress`, verified >6 dB dip), limiter on ffmpeg 5+
- [x] Agent tools: `set_captions`, `set_transitions`, `set_grade`, `add_text`, `add_overlay`, `add_audio`, `remove_layer`; timeline summary reports each clip's output start/end
- [x] Fixed: re-cutting clips used to drop captions/grade/layers; jump cuts inside a split clip don't inherit its transition
- [x] Agent told to do only what was asked (live run showed it adding music and a logo unprompted)
- [x] UI: transition markers, lanes for captions/text/overlays/audio on the same time axis as clips, colour summary, LUT uploads
- [x] Verified live with OpenAI: two chat turns produced captions, grade, title, logo, ducked music and a whoosh; frames and audio checked
- [x] Fixed (reported in testing): portrait footage came out landscape. Phone videos store portrait as rotated landscape pixels and the probe ignored the rotation flag; new timelines also defaulted to 1920x1080. Now: rotation-aware probe, new timelines match the source shape, agent keeps orientation unless asked, stored dimensions of earlier uploads corrected when the worker starts
- [x] Manual caption editing: caption editor under the timeline (edit or hide any line, style, position, colours, all caps; saved as one new version with a preview); corrections stored in source time (`captions.corrections`) so they survive re-cuts and restyling; agent tools `get_captions` / `edit_captions`; API `GET/POST /v2/projects/{pid}/captions`
- [ ] Smart reframing for 9:16 (face/subject tracking instead of center crop)
- [ ] Per-clip colour; keyframed motion (zoom/punch-in) on clips
- [ ] Caption timing edits (currently text only; Whisper sometimes gives a word a very short duration)

### Phase 4: Reference videos (done 2026-11-05)

- [x] References as their own media role (`role: reference`): upload or YouTube link; analysed, never usable as footage; excluded from transcript search
- [x] Style profile per reference (`editor/style.py`), first 10 minutes analysed:
  - pacing: own cut detector (`analysis.detect_cuts`) that also finds jump cuts and punch-ins; ffmpeg's scene score missed 5 of 6 punch-in cuts in testing
  - colour: brightness, contrast, saturation, warmth, Lab statistics from 24 sampled frames
  - audio: loudness, tempo (onset autocorrelation; none reported for speech), speaking pace from the transcript
  - vision (OpenAI, 6 frames): caption look, on-screen text, framing, punch-ins, transitions, energy, one-line summary
  - suggestions mapped onto existing settings (aspect, shot length, caption style, transition, colour match)
- [x] Colour matching: Reinhard transfer in Lab baked into a 33-point `.cube` LUT (`editor/colormatch.py`), strength 0.1-1, saved as a generated asset per match so earlier versions stay renderable
- [x] Punch-in zoom on clips (`clips[].zoom`) and pacing match (`apply_pacing`): long takes split at phrase boundaries with alternating punch-ins; tail split evenly
- [x] Agent tools: `get_reference_style`, `apply_reference_style` (all parts in one deterministic step, reports applied/skipped), `match_reference_color`, `apply_pacing`
- [x] Fixed after a live run: the model claimed it had set 9:16 without calling the tool; matching is now one tool call and replies may only describe confirmed changes
- [x] UI: footage and style-reference sections, reference card (summary + facts) with "Edit in this style", punch-in tags on clips
- [x] Verified live: a reference built with known style (9:16, ~2 s jump cuts with punch-ins, mid-screen uppercase karaoke captions with cyan highlight, vintage grade) was recovered by analysis and applied to plain 4:3 footage
- [ ] Music-matched cutting (cut on the beat of the user's music)
- [ ] Reference text/title styles (vision describes them; not yet mapped to text layers)
- [ ] Analyse more than the first 10 minutes; cache frames between runs

### Phase 5: Polish (done 2026-11-05, with deferrals)

- [x] Undo/redo for every kind of edit (agent, captions, hand edits, restores): each step is saved as a new version, so nothing is lost; Ctrl+Z / Ctrl+Shift+Z; history stacks per project
- [x] "What changed" for every version in plain words (`editor/diff.py`): format, clips added/removed/split/trimmed/reordered, punch-ins, transitions, length, captions, colour, text/overlays/audio
- [x] Hand editing of the timeline: select a clip to trim (numbers or "start/end here"), split at the playhead, move, delete, punch-in, transition, volume; edits stay a local draft with its own undo until saved as one version (with a preview render); chat and caption editing pause while a draft is open
- [x] Instant live preview in the browser (`LivePreview.jsx`): plays the timeline from 720p H.264 proxies made at ingest (backfilled for earlier uploads), with captions (estimated word highlight), text, image overlays, punch-ins, approximate colour, and music/SFX; lists what only the rendered preview shows
- [x] Fixed during this phase: music files were sent to Whisper and the empty result crashed search indexing; audio-only files are no longer transcribed and empty text is never indexed
- [x] Retire legacy Q&A endpoints (done early, with the UI replacement)
- [ ] Remotion render service / player: deferred. Remotion's licence is free only for individuals and companies of up to 3 people; larger companies need a paid company licence. Decision needed before adopting it. The live preview covers instant playback; motion text is covered by the ASS renderer.
- [ ] Postgres + object storage (S3 or similar) + Redis queue: deferred until hosting is chosen; `storage.py`, `store.py` and `jobs.py` are the seams
- [ ] Real accounts (email login, Google sign-in, or a hosted provider such as Clerk/Auth0): decision needed
- [ ] Drag-and-drop on the timeline; trimming by dragging clip edges

### Built-in colour looks (added 2026-11-05, on request)

- [x] 12 preset looks generated in code as 33-point `.cube` LUTs (`editor/luts.py`): Teal & Orange, Warm Film, Cool Cinema, Golden Hour, Bleach Bypass, Moody Green, Vintage Fade, Soft Pastel, Noir, Clean & Bright, Neon Night, Rosy. No third-party files or licence terms; tones stay in order (tested)
- [x] Strength 0-100% for presets and uploaded LUTs (`grade.lut_strength`, baked by mixing with an identity LUT)
- [x] Colour panel: the user's own footage through every look, strength slider, apply (new version + preview), download any look as `.cube`
- [x] Agent: `set_grade(look=..., look_strength=...)`; "What changed" names the look; live preview shows a CSS approximation

### Phase 6: Spice it up (in progress, started 2026-11-05)

Order agreed: 1) B-roll, 2) emphasis pack (keyword pops, emphasis zooms, auto sound effects, audio polish),
3) Lottie stickers and callouts, 4) designed motion graphics via HyperFrames, 5) style packs and agent playbooks.

**Step 1: B-roll (done)**
- [x] B-roll as its own media role: uploads (video or photos) are split into shots and each shot is described by the vision model; never transcribed
- [x] Stock B-roll: Pixabay (free key, required 24-hour search cache, shape filtering) and Pexels (new keys paused by Pexels as of 2026-11; code kept). The server resolves download links itself; the browser only sends a clip id
- [x] AI-generated B-roll stills (OpenAI `EDITOR_IMAGE_MODEL`, default `gpt-image-2.5-flare`, verified live), animated with camera motion
- [x] Timeline `broll[]` layers (output time) rendered over the main video, under graphics and captions: push-in, pull-out, pans, fades, optional own sound
- [x] Agent tools: `find_phrase`, `list_broll`, `search_stock_broll`, `add_stock_broll`, `generate_broll_image`, `add_broll` (trims cutaways to one shot and reports what it shows)
- [x] UI: B-roll section (uploads, stock search with credits, image generation), B-roll timeline row, cutaway inspector (timing, shot picker, motion, sound, remove), cutaways in the live preview
- [x] Scene detection: compared PySceneDetect with our detector on ground-truth clips; ours matched or beat it (6/6 punch-ins vs 5/6, no false cuts on captioned video), so no new dependency. Our detector now compares colour, not grayscale (a red-to-green cut of equal brightness was missed)
- [x] Live-run fixes: the agent placed the wrong shot (never looked at shot descriptions) and let a cutaway run into the next shot; now it must check `list_broll` and `add_broll` keeps cutaways inside one shot
- [x] Agent replies with bullet lists: prompt tightened and replies are flattened to plain sentences (`agent.tidy_reply`)

**Step 2: Emphasis pack (done)**
- [x] Keyword pops: new `keyword` text style (big display type, pop-in), placed below the face; captions give way while a keyword is on screen
- [x] Emphasis zooms: `zooms[]` layer (ease in 0.1 s, out 0.18 s, framed high for faces) on the speaker only, never on B-roll; B-roll placed later removes zooms it would hide
- [x] Built-in sound effects synthesised by ffmpeg (`editor/sfx.py`): whoosh, swipe, riser, pop, ding, click, impact, all level-matched to -3 dB peak; `audio[].sfx`; API `GET /v2/sfx`, `/v2/sfx/{id}.wav`
- [x] Audio polish (`audio_polish`): loudness to -14 LUFS (measured -14.5 on a -23.6 LUFS source), noise reduction (light/strong), clearer voice (EQ + compression)
- [x] Agent tools: `emphasize` (pop + zoom + sound per phrase, capped at a 1.5 s beat), `add_zoom`, `add_sound_effect`, `auto_sound_effects` (no two effects within 0.35 s), `set_audio_polish`
- [x] UI: Sound panel (polish switches, sound library with preview and "add at playhead"), Zoom row, sound effects in the Audio row, keyword pops/zooms/effects in the live preview
- [x] Live-run fixes: 5-second "emphasis" zoom from a stretched word timing (now capped); zooms hidden under B-roll; near-stacked sounds; replies with markdown and "3:00" for three seconds; live preview text was 1.6-1.7x too big (font metric mismatch with libass, now corrected from the font files) and hidden behind cutaways
- [x] Emoji with keyword pops (`emphasize` takes an optional emoji, added in step 3)
- [ ] The agent sometimes picks a B-roll shot that doesn't match and says so; stricter rule added, to watch in testing

**Step 3: Lottie stickers and callouts (done)**
- [x] Timeline `stickers[]` layer: callout, animated emoji or uploaded Lottie `.json`, with position, size, rotation, colour and loop; rendered to transparent frames with rlottie and overlaid after B-roll, under captions
- [x] 7 built-in callouts generated as Lottie with draw-on animation (circle, underline, box, arrow, check, cross, sparkle), any colour
- [x] Animated emoji from Noto Animated Emoji (CC BY 4.0, credit shown in the UI), downloaded on first use and cached; words and ASCII rejected
- [x] Agent tools: `list_stickers`, `add_sticker`; `emphasize` can add an emoji
- [x] UI: Stickers panel (animated callout thumbnails, popular emoji, any emoji, your animations), Stickers timeline row, sticker inspector (position, size, rotation, timing, colour, remove), stickers in the live preview via lottie-web (MIT, loaded only when needed)
- [x] Live-run fixes: the agent silently skipped one of three requested stickers; the prompt now makes it do every part of a request or say what it didn't do. The live preview crashed when a sticker left the screen (lottie-web listener removed after destroy)
- Licences: OpenCV (cut-out maths) is Apache-2.0. MODNet weights and code are Apache-2.0. rlottie is LGPL-2.1 and used unmodified through `rlottie-python`; keep it a separate, replaceable library

**Step 3b: Background removal (done)**
- [x] Person mattes with MODNet (Apache-2.0) on the CPU via ONNX Runtime. ONNX export from Hugging Face `Xenova/modnet` (Apache-2.0), pinned revision, SHA-256 checked, downloaded on first use to `<storage>/models/modnet.onnx` (or `EDITOR_MODNET_PATH`)
- [x] Only the source ranges clips use are matted (15 fps, 384 px short side, light temporal smoothing) and cached as grey FFV1 files per range; trims inside a cached range reuse it. About 0.45 s a frame on this machine, so 1 minute of footage takes about 7 minutes the first time
- [x] Per-clip `background`: blur (the real background), solid colour, or an image/video asset (B-roll, stock, AI image; videos loop); letterbox bars count as background
- [x] Green/blue screen: colour key with spill removal, screen colour detected from the frame edges (default tolerance 0.12; 0.3 ate into skin and hair)
- [x] Agent tool `set_background` (auto picks colour key or AI cut-out, warns that the first preview is slower); API `GET /assets/{id}/screen-color`; clip inspector controls; "what changed" lines
- [x] Live-run fixes: half-sure matte areas ghosted the old background through (hay above the head); matte levels are now firmed up before compositing
- [ ] Live preview doesn't show background changes (listed under "not shown live"); the rendered preview does

**Feedback round, 2026-11-12 (user test notes)**
1. Multicam (done): `editor/multicam.py`. 2-3 angles of one take (+ optional separate audio) are synced: coarse offset
   from matching three-word runs in the transcripts, then onset-envelope cross-correlation (+-1.5 s of that, or
   global without transcripts), then waveform cross-correlation for sample accuracy (test offsets exact to 3 ms;
   the live 3-angle test matched the constructed 1.2/0.0/1.7 s exactly). All are trimmed to the shared stretch.
   Audio: the separate recording, else the angle with the best speech-band SNR, minus clipping. Stored as a
   `multicam` asset (its file = the chosen audio, its transcript = that audio's, moved to group time), so every
   existing tool works on it. Clips have `angle`. Tools `create_multicam`, `switch_angles` (cuts at sentence
   pauses every 2.5-6 s, at jump cuts once a shot has lasted 1 s, never the same angle twice, opens on a wide),
   `set_angle`. API `POST /projects/{id}/multicam` (job). UI: "Sync camera angles", camera picker per clip,
   clips labelled by camera; live preview plays the camera files (with their own sound).
2. Cut-out quality (done): cut-outs are now a cached foreground + alpha pair (`v2-*` files). AI: model at 448 px
   (same cost as 384), keyframes at 15 fps with interpolated mattes in between (no lag from smoothing), edge
   colours decontaminated (blur-fusion foreground estimate). Green screen: own soft colour-difference keyer
   (keeps hair strands as partial alpha) with brightness-preserving spill removal (the old ffmpeg chromakey +
   despill cut strands off and left a dark outline). Tried a guided filter: it smeared dark hair on dark
   backgrounds into a halo, so it is not used. Cost: AI ~9 s, key ~2 s per second of footage, once.
3. B-roll from the transcript (done): `editor/brollplan.py`, tool `plan_broll`. The model reads the whole edit,
   picks moments and writes 2-3 concrete Pixabay queries each; moments are located by their exact words, kept
   out of the hook, off other B-roll and under a third of the runtime (shortened to fit); candidates' thumbnails
   are scored by vision and only clear matches (>= 6/10) are placed. Live: elephant herd on "elephants", trunk
   close-up on "really long trunks". The agent now calls it for any B-roll request instead of reusing images.
5. Caption fonts (done, 2026-11-12): `editor/typefaces.py` registers 10 fonts bundled in `editor/fonts` (Google
   Fonts, OFL/Apache, licences alongside). `captions.font`, `captions.emphasis` (words/phrases, numbers, font,
   colour, size, uppercase) and `text[].font`. Sizes are scaled by each font's unitsPerEm/(winAscent+winDescent)
   so any font draws as big as the default (libass sizes by line height); the browser preview needs no scaling.
   Agent: `set_captions` font/emphasis options (it picks the important words itself), `add_text` and `emphasize`
   fonts. API `GET /v2/fonts`; caption lines carry `emphasis` word positions for the live preview.
6. Landing page (done, 2026-11-12): `frontend/src/Landing.jsx` at `/`, the editor at `/edit` (hosting needs an SPA
   fallback to index.html). Charcoal and off-white with the timeline lane colours; Bricolage Grotesque headings;
   the headline plays as a karaoke caption with emphasised words in caption fonts.
7. Accounts and dashboard (done, 2026-11-13): `editor/auth.py`. Email + password (scrypt), sessions as random
   tokens in an HttpOnly SameSite=Lax cookie (only a SHA-256 is stored), 30 days. Every endpoint takes the user from
   the session, else an anonymous browser id, and an account's id is never accepted without its session. Signing
   up or in moves that browser's anonymous projects (rows and files) into the account. `EDITOR_REQUIRE_ACCOUNT=1`
   refuses anonymous use; `EDITOR_SECURE_COOKIES=1` for HTTPS. Simple brute-force guard (10 failures / 15 min).
   API: `/v2/auth/{signup,login,logout,me}`, `GET/PATCH /v2/me` (name, password, settings), `GET /v2/dashboard`.
   UI: sign-in dialog when you start editing (the start carries on after), user chip in the editor, `/dashboard`
   with project cards and Settings (account, Telegram integration as "coming soon" with a notify-me switch).
   Not yet: password reset by email, OAuth, email verification, deleting an account.
8. Plans and vid-coins (done, 2026-11-13; demo payments): `editor/billing.py`. 1 coin = one full-quality export
   per started minute (previews, chat and hand edits free), charged when the export is queued and refunded once if
   it fails. Free 2 coins topped up each calendar month (no stacking); Basic $3 / NGN 4,500 = 10 coins for one
   month; Pro $10 / NGN 15,000 = 25 coins for three months; buying adds coins and starts or extends the period;
   paid coins end with the plan. Limits are enforced on the server where timelines are saved or exported and
   where gated features start (multicam angles, B-roll count, caption styles/fonts/emphasis, AI background,
   AI images), and the chat editor reports them ("Plan limit"). Ledger of every coin movement; payments table.
   API: `GET /v2/plans`, `GET /v2/billing`, `POST /v2/billing/checkout`, `POST /v2/billing/demo/confirm`.
   UI: pricing on the landing page (USD/NGN), Plan and coins tab with demo checkout, coin chip, export cost,
   upgrade links on plan-limit errors, locked caption styles and fonts, multicam hint.
   Next: a real provider (Paystack: initialize, redirect, verify by webhook signature) in place of the demo
   confirm endpoint; receipts; reminders before a plan ends.
4. Effects (done): `effects[]` layer: video noise removal (hqdn3d; nlmeans for strong settings in exports, +10 dB
   on heavy noise), film grain (luma noise), blur of the whole frame or a region. Tool `add_effect`, Effects panel,
   timeline row and inspector, live preview (backdrop blur, animated grain). Sound noise reduction stays in
   audio polish.


## 7. Risks, legal and open questions

- **Security:** `backend/cookies.txt` contains live YouTube session cookies and is in git history. Sign that Google account out of all sessions to rotate them. Before pushing the commit that untracks it, copy the file to the server outside the repo and set `YTDLP_COOKIES_FILE` there, or `git pull` will delete it on the server.
- **Auth:** endpoints trust a `user_id` query parameter. Real auth is needed before public launch.
- **Legal:** downloading and re-editing YouTube videos raises YouTube terms-of-service and copyright issues. Restrict to the user's own content or require a rights attestation.
- **Cost:** Whisper about $0.006/min; renders are CPU-heavy, so the render worker wants a compute-optimised machine.
- **Hosting:** EC2 deployment and its GitHub Actions workflow were removed (2026-11-05). Pick hosting before the first public build; the API and the worker are separate processes.
- **Decided: agent model.** Two providers will be offered; OpenAI ships first. The agent talks to a provider-neutral interface (`editor/llm/`), so the second provider is an adapter, not a rewrite.
- **Open: credentials needed** — `OPENAI_API_KEY` for live transcription and agent tests; later storage (S3 or similar) and Postgres when hosting is chosen.

## 8. Running it

```bash
cd backend
pip install -r requirements.txt          # ffmpeg + ffprobe must be on PATH
uvicorn api:app --reload                 # API (/v2 editor routes)
python -m editor.worker                  # render/ingest worker (separate process)
python -m pytest tests                   # tests (need ffmpeg)

cd ../frontend
npm install
npm run dev                              # http://localhost:3000, proxies /api to :8000
```

Environment variables: `OPENAI_API_KEY`, `EDITOR_LLM_PROVIDER` (default `openai`), `EDITOR_AGENT_MODEL` (default `gpt-5-mini`), `EDITOR_AGENT_REASONING` (default `low`), `YTDLP_COOKIES_FILE` (optional), `EDITOR_STORAGE_ROOT` (default `output/editor`), `FFMPEG_BINARY` / `FFPROBE_BINARY` (optional).

Deployment: none for now. Wherever it lands, run the API and `python -m editor.worker` as two long-lived processes, and upgrade yt-dlp on every deploy (YouTube breaks old releases regularly).

## 9. API reference (`/v2`, all take `?user_id=`)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v2/projects` | Create project `{name}` |
| GET | `/v2/projects` | List projects |
| GET / DELETE | `/v2/projects/{pid}` | Project + assets + latest timeline / delete everything |
| POST | `/v2/projects/{pid}/assets/upload` | Multipart upload (`transcribe`, `language`); queues ingest |
| POST | `/v2/projects/{pid}/assets/url` | `{url}` YouTube link; queues download + ingest |
| GET | `/v2/projects/{pid}/assets/{aid}` | Asset metadata (`include_transcript=true` for words) |
| GET | `/v2/projects/{pid}/assets/{aid}/file` | Original media |
| GET / PUT | `/v2/projects/{pid}/timeline` | Read (optionally `version=`) / save a new version |
| POST | `/v2/projects/{pid}/renders` | `{preview, timeline_version?}`; queues render |
| GET | `/v2/projects/{pid}/jobs` | Recent jobs |
| GET | `/v2/jobs/{jid}` | Status + progress |
| GET | `/v2/jobs/{jid}/output` | Download finished render |
| GET | `/v2/projects/{pid}/search?q=` | Semantic search across transcribed assets |
| POST | `/v2/projects/{pid}/chat` | `{message, render_preview}`; queues an agent job (409 if one is already running) |
| GET | `/v2/projects/{pid}/messages` | Chat history with timeline versions and preview job ids |
| GET / POST | `/v2/projects/{pid}/captions` | Caption lines as rendered / change style, edit or hide lines (`{settings, edits, enabled}`) |
| POST | `/v2/projects/{pid}/timeline/restore` | `{version}`; saves a copy of an earlier version as the newest |
