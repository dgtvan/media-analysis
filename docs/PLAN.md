# media-analysis — Plan

A non-GUI app that takes a folder of video/audio files, understands it, and produces one report folder: a Markdown
report built from a matching template, plus the frames and extracted text that back it up.

## 1. Scope

**In scope**
- One input folder → one report. Files in the folder are treated as materials of the same session
  (typically 1–2 recordings of 10–60 min).
- Input types: video and audio only. Other files in the folder are skipped with a warning.
- Spoken language: English, Vietnamese, or mixed. Transcript keeps the original language; the report is in English
  (quotes may keep the original wording with an English translation).
- Automatic GPU use when available, CPU otherwise.
- Resumable runs: finished steps are not repeated.

**Out of scope**
- Combining several input folders (multi-part meetings) into one report.
- Audio/video clips as evidence (timecoded frames only).
- Other input types (images, PDF, PPTX/DOCX) — possible future work.
- GUI (but the core is built so a UI can be added later, see §8).

**Optional / later**
- Speaker diarization (who said what), behind a flag.

## 2. Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12, managed with `uv` | Best ecosystem for speech, vision and LLM SDKs |
| Media decode | ffmpeg / ffprobe (system binary) | Checked at startup |
| Transcription | faster-whisper (CTranslate2) | `large-v3` default; per-segment language detection for mixed EN/VI |
| Image work | OpenCV (headless), Pillow | Perceptual hash, sharpness, contact sheets |
| LLM / vision | Claude via Agent SDK (`claude-agent-sdk`), subscription login | Classification, frame planning, frame reading, report writing; API-key backend as an alternative (§3a) |
| CLI | Typer + Rich | Thin layer over the core |
| Data models | Pydantic | Every stage artifact is a typed, JSON-serialisable model |
| Templates | Markdown files with YAML front matter | Built-in + user folders |
| Diarization (optional) | pyannote.audio | Needs a Hugging Face token |

Providers (transcriber, LLM) sit behind small interfaces so a cloud transcriber or a local LLM can be swapped in.

## 3. Hardware detection

`media-analysis doctor` and every run report what was detected:
- CUDA GPU via `ctranslate2.get_cuda_device_count()` (and the CUDA/cuDNN libraries it needs).
- GPU → `large-v3`, `float16`. CPU → `large-v3`, `int8`, all cores. Overridable with `--whisper-model` / `--device`.
- Only NVIDIA/CUDA GPUs are used (CTranslate2). Intel/AMD GPUs fall back to CPU; an OpenVINO backend could be added.
- Measured on a 16-thread laptop CPU: large-v3 ≈ 0.8× real time (1 h audio ≈ 75 min), large-v3-turbo ≈ 1.3×.
- Missing ffmpeg or Claude login → clear error before any work starts.

## 3a. Claude access

Two interchangeable backends behind one `LLM` interface:

- **`subscription` (default)** — Claude Agent SDK for Python. It runs the bundled Claude Code binary, which reuses the
  user's existing Claude login (Pro/Max), or a long-lived token from `claude setup-token` (`CLAUDE_CODE_OAUTH_TOKEN`).
  Usage counts against the subscription limits. Personal, single-user use only: Anthropic's terms don't allow
  products that offer claude.ai login to other users, so a shared/distributed version must use `api`.
- **`api`** — Anthropic SDK with `ANTHROPIC_API_KEY`, billed per token.

Implementation notes for the `subscription` backend:
- If `ANTHROPIC_API_KEY` is set in the environment, Claude Code uses it instead of the login, silently billing the API.
  `doctor` warns about this; the backend removes it from the child process environment.
- All built-in tools disabled, `max_turns=1`, short custom system prompt → behaves like a plain model call.
- Structured results via `output_format` (JSON Schema) → `ResultMessage.structured_output`.
- Images and long text sent as content blocks through streaming input (`ClaudeSDKClient`), not as command-line
  arguments (Windows limits the command line to ~32 KB; a 1-hour transcript is larger).

## 4. Pipeline

Each stage is idempotent, reads the previous stage's artifact and writes its own into the cache (§6).

```
1 ingest ─► 2 transcribe ─► 3 visual scan ─► 4 classify ─► 5 frame plan ─► 6 extract ─► 7 read frames ─► 8 report ─► 9 assemble
            (local)          (local)          (LLM)         (LLM)            (local)       (LLM vision)     (LLM)        (local)
```

1. **Ingest** — list video/audio files, ffprobe metadata (duration, streams), hash each file, order by
   name/creation time. Audio-only files skip the visual steps (3, 6, 7).
2. **Transcribe** — extract 16 kHz mono audio with ffmpeg; faster-whisper with VAD and per-segment language
   detection. Work is checkpointed in chunks (~10 min) so an interrupted 1-hour CPU run resumes where it stopped.
3. **Visual scan (local image processing, no AI, nothing kept in output)** — decode at low resolution (~1 fps) and
   split the video into *visual segments* (stretches whose content is the same). Comparison is structural, not
   pixel-by-pixel, so noise, compression artifacts, lighting and small moving things don't count as changes:
   - **Normalise:** grayscale, downscale, light blur — removes sensor noise and compression artifacts.
   - **Perceptual hash** (dHash/pHash): compares the overall layout; distance threshold instead of exact match.
   - **Edge-map / SSIM comparison:** compares lines and shapes (text, diagrams, slide layout), ignoring colour and
     brightness shifts. Catches meaningful small changes such as a new line of code or a new bullet.
   - **Change area:** a change counts only if it covers enough of the frame or a text-like region; a moving cursor,
     clock, or webcam thumbnail in a corner does not.
   - **Compare to the segment's anchor frame**, not only the previous frame, so slow changes (scrolling, gradual
     drawing on a whiteboard) are still detected.
   - **Minimum segment length** and hysteresis, so flicker or a brief pop-up doesn't split a segment.
   - **Segment kind:** runs of 3+ frames with motion → *moving* (people on camera, playing video); otherwise
     *static* (slides, screen share, whiteboard, still shot). Edge density is reported per segment as a hint for
     text/diagrams. Inside moving segments, only hard cuts (histogram jump) start a new segment, so people moving or
     talking never count as a change.
   Each segment gets one representative thumbnail. This replaces the fixed 3 s / 5 s interval: it runs locally at
   no usage cost, and is only used to *decide* where to look. Small changes it misses can still be requested by the
   frame plan (step 5), which also reads the transcript.
4. **Classify (processing type)** — one LLM call with: the transcript, the visual segment list, and one contact sheet
   (a grid of up to ~16 segment thumbnails). The model picks one template from the registry and returns
   `{template_id, confidence, reason}`.
   - No template fits (or confidence below threshold) → use the built-in `generic` template (`fallback: true`, never
     offered to the classifier). The report ends with a "Template fit" section (reason, closest templates, key points
     on what doesn't match) and `reference/suggested-template.md`, a ready-to-edit template for this kind of recording.
   - `--template <id>` skips this step.
5. **Frame plan** — one LLM call with the chosen template's evidence guidance, the transcript and the visual segments.
   Returns a list of evidence requests: `{start, end, reason, what_to_look_for, priority}`.
   Examples: the speaker says "as you can see in this diagram"; a slide change in a screen share; a whiteboard.
   Talking-head stretches with nothing to show get no requests. Capped (default 30 requests per run).
6. **Extract (local)** — for each request, decode full-resolution frames densely in the range (e.g. 2 fps), group
   near-duplicates by perceptual hash, keep the sharpest of each group (variance of Laplacian), cap per request.
7. **Read frames (LLM vision)** — send candidates in batches with the request's context. For each frame the model
   returns: relevant yes/no, verbatim text in the frame (code, slide text, diagram labels), a description, a caption.
   Irrelevant frames are dropped.
8. **Report** — one LLM call (map-reduce only if the transcript is too long) with the template, the transcript, and
   accepted evidence (id, timecode, caption, extracted text). The model references evidence by id; the app turns ids
   into relative image links and validates that every referenced id exists.
9. **Assemble** — write the output folder (§7). Only frames actually referenced by the report are copied.

## 5. Templates (processing types)

A template is one Markdown file with YAML front matter:

```markdown
---
id: meeting
name: Meeting
description: A work meeting with decisions and follow-ups.
signals:            # helps classification
  - several participants discussing a shared agenda
  - decisions, action items, deadlines
evidence:           # guides the frame plan
  - shared screens, slides, documents being discussed
sections:
  - Summary
  - Participants
  - Agenda & discussion
  - Questions raised
  - Decisions / conclusions
  - Action items (task, assignee, due date)
---
Instructions for the report writer: tone, detail level, what to do when information is missing...
```

Built-in: `meeting`, `technical-discussion`, `lecture-tutorial`, `interview`, `language-practice`, `casual-chat`.
- `language-practice` includes a learner assessment: grammar mistakes with corrections, vocabulary, fluency,
  pronunciation notes (from transcript evidence), and suggested exercises.

User templates live in a user folder (`%LOCALAPPDATA%\MediaAnalysis\data\templates\` on Windows, `~/.media-analysis/templates/` elsewhere) and override built-ins with the same id.

CLI:
- `media-analysis templates list`
- `media-analysis templates new <id>` — scaffold a new template file.
- Unknown type → exit code `10`, message with the reason, closest templates and:
  `media-analysis run <input> --template <id>` or `media-analysis templates new <id>`.
  Because the cache is kept, the rerun reuses the transcript and visual scan; only the LLM steps run again.

## 6. Cache and resume

- Location: `<output>/.cache/` (hidden; not part of the deliverable; `--clean-cache` removes it after a run).
  Holds the full transcript, visual scan, classification, frame plan and candidate frames.
- Each artifact is keyed by input file hash + stage settings (model, interval, template...). Changing a setting reruns
  only the affected stage and those after it.
- `--force <stage>` reruns from a stage.

## 7. Output folder

```
<output>/
  report.md                        # template-based report, relative links into reference/
  reference/
    frames/<file>_<hh-mm-ss>.jpg   # only frames used in the report
    text/<frame-id>.md             # verbatim text read from each referenced frame
  .cache/                          # working data, incl. full timecoded transcript (transcript.json)
```

Usage: `media-analysis run <input-folder> <output-folder>` — both arguments are required; there are no defaults.
The full transcript is an internal artifact in the cache, not a deliverable.

## 8. Code structure (UI-ready)

```
src/media_analysis/
  core/          # pipeline, stages, artifacts (Pydantic), cache, events — no printing, no CLI code
  providers/     # transcriber (faster-whisper), llm (Claude), diarizer (optional)
  media/         # ffmpeg wrappers, frame hashing/sharpness
  templates/     # registry + built-in template files
  cli.py         # Typer commands: run, doctor, templates
tests/
```

- The pipeline emits progress events (`stage_started`, `progress`, `stage_done`, `summary`, `error`) to a callback.
  The CLI renders them with Rich; a later UI subscribes to the same events.
- Later UI path: a small FastAPI service wraps the core (start job, stream events over SSE, fetch output), and any
  front end (web, Tauri, Electron) talks to it. No core changes needed.

## 9. Milestones

| # | Deliverable |
|---|---|
| M0 | Project skeleton (`uv`, package layout, CLI), config, `doctor` (ffmpeg, GPU, API key) |
| M1 | Ingest + transcription (GPU/CPU, mixed EN/VI, chunk checkpoints) → cached `transcript.json`; cache/resume |
| M2 | Visual scan + template registry + classification + generic fallback (was: stop-with-reason) + `--template` + `templates list/new` |
| M3 | Frame plan + dense extraction + sharpest-frame selection + vision reading (text + description) |
| M4 | Report generation + output assembly + link validation; all six built-in templates |
| M5 | Optional diarization; draft-a-template-from-a-run helper |
| T1 | Tray app (.NET): watch a projects folder, recent-projects menu with Analyse, working/waiting/idle icon, self-update; core stays analysis-only |
| T2 | Full window (projects grid with status, elapsed and actions; live logs; Config/Templates/About tabs); run history; core: generic report instead of stopping, Meeting Recorder folders as one session, performance summary |

Status: M0–M4 implemented and verified end to end on a synthetic sample (video and audio-only, detection, forced
template, user template, cached rerun). T1 and T2 implemented (docs/DEPLOYMENT.md, README "Windows app").
M5 not started.

Testing: unit tests for segmentation, hashing, audio chunking, cache keys, templates and output assembly; a short
synthetic video (`tests/make_sample.py`, Windows text-to-speech) for an end-to-end smoke test.
