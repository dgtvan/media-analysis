# media-analysis

Turn a folder of video/audio recordings into one Markdown report. The app transcribes the speech, works out what kind
of recording it is (meeting, technical discussion, lecture, interview, language practice, casual chat, or your own
template), picks the moments where the picture matters, and writes a report that embeds those frames as evidence.

```
<output>/
  report.md                        # the report, built from the matching template
  reference/
    frames/<file>_<hh-mm-ss>.jpg   # only the frames the report uses
    text/<file>_<hh-mm-ss>.md      # text read from each of those frames
  .cache/                          # working data (transcript, scan, ...) so reruns skip finished work
```

## How it works

1. **Transcribe** locally with faster-whisper (English, Vietnamese or mixed). Uses an NVIDIA/CUDA GPU when present,
   otherwise the CPU.
2. **Scan** the video locally (no AI): split it into stretches where the picture stays the same, ignoring noise,
   compression artifacts and regions that keep changing (clocks, webcam tiles, people moving).
3. **Classify** with Claude: pick the template that fits, or stop with a reason when none does.
4. **Plan** with Claude: from the transcript and the visual stretches, choose which moments need a frame.
5. **Extract** locally: grab frames in those moments, drop near-duplicates, keep the sharpest.
6. **Read** with Claude: keep only useful frames and read their text.
7. **Report** with Claude, following the template; then assemble the output folder.

See [docs/PLAN.md](docs/PLAN.md) for the design.

## Requirements

- Windows/macOS/Linux with Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- ffmpeg on PATH (`winget install Gyan.FFmpeg`), or `MEDIA_ANALYSIS_FFMPEG_DIR` pointing at its `bin` folder
- Claude access:
  - **subscription** (default): your own Claude login, reused through the Claude Agent SDK. Log in once with Claude
    Code (`claude` then `/login`), or set `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`. For personal use only.
  - **api**: `ANTHROPIC_API_KEY`, billed per token. Install with `uv sync --extra api` and run with `--backend api`.

The first run downloads the Whisper model (large-v3, about 3 GB), unless `MEDIA_ANALYSIS_MODELS_DIR` points at a
shared copy.

## Windows app

A Windows app (`tray/`) runs in the background with an icon in the tray and watches a **projects folder**: each
subfolder is a project (one recording session). Each project's report goes to `<output folder>\<project name>`.

- **Tray icon** (sound-wave bars): blue = working (an analysis runs or is queued), amber = waiting (a project has
  video/audio but no up-to-date report), grey = idle. A project counts as analysed when its `report.md` is newer than
  all of its media files, so adding a file makes it waiting again. Its menu has *Status* (the window) and *Exit*;
  double-clicking it also opens the window.
- **Projects tab**: every project with its name (yours to change: F2, typing, or right-click → *Rename*; the folder
  itself is never renamed and reports stay in `<output folder>\<folder name>`; clear the name to go back to the folder
  name), its folder, a plain-language status (*Transcribing audio… 45%*, *Writing the report…*,
  *Done*, *Failed: …*, *Stopped by you*, *Not analysed*, *Ignored*), the elapsed time (live while running; the last
  run's time otherwise), when it was last analysed, and the number of media files. Right-click a project for its
  actions (*Analyse* / *Analyse again*, *Stop analysis* or *Remove from queue*, *Ignore* / *Unignore*), then what to
  open (*View logs*, *Open report*, *Open project folder*, *Open output folder*) and, after a general report, *Add the
  suggested template*. Double-click opens the report (or the project folder). One analysis runs at a time; others
  queue. Stopping is cheap: analysing again continues from the cache. An ignored project no longer counts as waiting
  and is never started automatically. Filter, search, and *Analyse all not analysed* sit above the grid.
- **Logs**: each analysis is logged from its first line to `%LOCALAPPDATA%\VanDang\MediaAnalysis\data\logs`, ending with a
  performance summary (time per step, cached or not, transcription speed, Claude calls). The log window follows a
  running analysis live and has a readable and a full view.
- **Config tab**: projects and output folders, *Analyse new projects automatically*, transcription model, spoken
  language, Claude thinking effort and the maximum number of key moments (stored in `%LOCALAPPDATA%\VanDang\MediaAnalysis\data\settings.json`).
- **Templates tab**: built-in and your own templates (`%LOCALAPPDATA%\VanDang\MediaAnalysis\data\templates`), with *New*, *Open* and
  *Copy to my templates*.
- **Automatic analysis** (off by default): projects added after you turn it on are queued once their files have
  stopped changing for 2 minutes (shown as *New: starts once its files stop changing*). Existing projects are left
  alone, and a failed or stopped analysis isn't retried automatically.
- **About tab**: when the installed build was made, where the app, the engine, settings and logs are.
- **Needs attention**: a failed analysis or a broken install turns the icon into a warning and shows a banner in the
  window until you dismiss it.

The app starts at sign-in. It counts as **busy** while an analysis runs or is queued: an install then leaves it
running and asks to be run again later. Results (outcome,
elapsed time, log) are kept in `%LOCALAPPDATA%\VanDang\MediaAnalysis\data\history.json`, so they survive restarts.

### Meeting Recorder folders

A folder with Meeting Recorder's layout (`video.mp4` without sound, `audio-mic.wav`, `audio-system.wav`) is one
session: the two transcripts are merged into one conversation on a shared timeline, labelled *Me* (microphone) and
*Others* (computer audio), and frames come from the video.

## Installing on Windows

Follows the [App deployment standard v2.3](https://github.com/dgtvan/local-app-deployment-strategy), with the core's
Python, libraries, ffmpeg and Whisper model kept outside the install so builds share them; see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

```
deploy.cmd           # build, uninstall, install: installs and starts the tray app, puts `media-analysis` on PATH
uninstall.cmd        # removes the app; settings, templates, logs and reports are kept
media-analysis run <input-folder> <output-folder>
```

## Usage

```
uv sync
uv run media-analysis doctor --check-claude
uv run media-analysis run <input-folder> <output-folder>
```

Useful options:

| Option | Meaning |
|---|---|
| `--template <id>` | Skip detection and use this template |
| `--force <stage>` | Redo a stage and everything after it (`transcribe`, `scan`, `classify`, `plan`, `extract`, `read`, `report`) |
| `--whisper-model <name>` | e.g. `large-v3-turbo` (faster on CPU, a bit less accurate) |
| `--language en\|vi` | Language hint; by default it is detected and mixed speech is allowed |
| `--max-frames <n>` | Maximum moments to capture (default 30) |
| `--claude-model <id>` | Claude model (default: your account's default model, as in Claude Code) |
| `--effort <level>` | Claude thinking effort: `low`, `medium`, `high`, `xhigh`, `max` (default: the model's default) |
| `--json` | Print progress as JSON Lines on stdout (for scripts and other programs) |
| `--clean-cache` | Delete `.cache/` after a successful run |

## Following progress

The console view shows each step with a live status line: a progress bar for transcription, scanning and frame
extraction, and what Claude is doing during the Claude steps (`thinking`, `writing, 1,240 chars`).

Programs can follow a run in two ways:

- **CLI:** `--json` prints one JSON object per line: `stage_started`, `progress` (`completed`/`total`), `activity`,
  `warning`, `stage_done` / `stage_skipped`, `summary` (time per stage; also after a failure), and finally
  `finished` (with `generic: true` when no template fit) or `error`.
- **Python:** pass a callback that receives `Event` objects:

  ```python
  from media_analysis.config import Settings
  from media_analysis.core.pipeline import Pipeline, RunOptions

  result = Pipeline(Settings(), sink=lambda e: print(e.to_dict())).run(RunOptions(input_dir=..., output_dir=...))
  ```

Rough timing on CPU: about 1.2× the audio length with large-v3, 0.8× with large-v3-turbo; the Claude steps add a few
minutes.

## Templates

```
uv run media-analysis templates list
uv run media-analysis templates new sales-call      # creates %LOCALAPPDATA%\VanDang\MediaAnalysis\data\templates\sales-call.md
```

A template is a Markdown file with YAML front matter (`id`, `name`, `description`, `signals`, `evidence`, `sections`)
followed by instructions for the report writer. User templates override built-ins with the same id; `--templates-dir`
adds more folders.

When no template fits, the report uses the built-in `generic` template and ends with a **Template fit** section: why
no template fits, the closest templates, key points on what doesn't match, and a suggested new template. A
ready-to-edit copy of it is written to `reference/suggested-template.md`; put it in your templates folder (or widen
one of the closest templates) and analyse again. The transcript and scan are reused from the cache.

## Development

```
uv run pytest
uv run python tests/make_sample.py <folder>    # synthetic test video (Windows text-to-speech)
```
