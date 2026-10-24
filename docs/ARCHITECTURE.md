# Architecture

The repo holds an analysis core and the Windows apps built on it. The core first works out what is in a folder of
video and audio (the speech, what is on screen); a **workflow** then turns that into a result. Today there is one
workflow, **report**, and one app, **Recording Reports**: a folder of recordings of one session becomes a report
folder, a Markdown report written to a matching template plus the frames and the text read from them that back the
report up.

- **The core** (`core/src/media_analysis`, Python): the workflows and the shared understanding stages. It runs as a
  command-line program and knows nothing about the apps.
- **Recording Reports** (`apps/recording-reports`, .NET 10 WinForms): a tray app that watches a projects folder, runs
  the core's report workflow for each project and shows status, logs and settings. It starts the core as a child
  process and reads its progress.

```
           Recording Reports (apps/recording-reports)                  Core (core/src/media_analysis)
  ┌──────────────────────────────────────────┐   starts, per project   ┌────────────────────────────────┐
  │ TrayApp ── watches the projects folder   │ ──────────────────────► │ report <in> <out> --json       │
  │ MainForm ─ projects grid, Config, ...    │                         │   └─ ReportWorkflow (9 stages) │
  │ AnalysisRunner ─ queue, one run at a time│ ◄────────────────────── │        ├─ faster-whisper       │
  │ RunHistory, logs, MediaDurations         │   JSON events (stdout)  │        ├─ ffmpeg / OpenCV      │
  └──────────────────────────────────────────┘                         │        └─ Claude (Agent SDK)   │
                                                                       └────────────────────────────────┘
```

How it is built, installed and updated is in [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. Scope

- One input folder is one session (typically one or two recordings of 10–60 minutes). Its files are analysed
  together; anything that isn't video or audio is skipped with a warning.
- Speech in English, Vietnamese or both mixed. The transcript keeps the original language; the report is in English.
- Uses an NVIDIA GPU for transcription when there is one, the CPU otherwise.
- Runs are resumable: finished work is never repeated unless asked for.
- Not covered: several folders combined into one report, audio/video clips as evidence (frames only), other input
  types (images, PDF, slides), speaker diarization beyond Meeting Recorder's microphone/system split.

## 2. Tech stack

| Concern | Choice |
|---|---|
| Core language | Python 3.12, managed with uv |
| Media decoding | ffmpeg / ffprobe |
| Transcription | faster-whisper (CTranslate2), `large-v3` by default |
| Image analysis | OpenCV (headless), NumPy, Pillow |
| Claude | Claude Agent SDK (subscription login) or the Anthropic SDK (API key) |
| CLI | Typer + Rich |
| Data | Pydantic models for every stage result, JSON in the cache |
| Templates | Markdown files with YAML front matter |
| Windows app | .NET 10 WinForms, framework-dependent |

## 3. Core

### 3.1 Code layout

```
core/
  pyproject.toml, uv.lock
  src/media_analysis/
    cli/
      app.py            commands: report (also as run), doctor, templates list/new
      sinks.py          console and JSON event output
    engine/             runs any workflow; knows nothing about reports
      workflow.py       Workflow (stage order, --force, --fresh, cache, run record), Stage, RunContext
      cache.py          content-addressed cache and file fingerprints
      perf.py           StageTimer (timings, Claude calls and tokens), TimedLLM, summary, run statistics record
      events.py         progress events and the Reporter helper stages use to emit them
      settings.py       settings (transcription, scan, evidence, Claude), data folder
      errors.py         MediaAnalysisError (expected failures, with an exit code)
      hardware.py       CPU/GPU detection
      usage.py, timecode.py
    providers/
      transcriber.py    faster-whisper wrapper, silence-aware chunking
      llm.py            Claude backends (subscription, api) behind one LLM interface
    media/              local media tools, no AI
      ffmpeg.py         probe, decode audio, iterate frames, extract stills
      segmentation.py   visual segmentation
      frames.py         perceptual hash, sharpness, edges, contact sheets
    understand/         what is in the files, the same for every workflow
      models.py         MediaFile, Ingest, Transcript, VisualScan
      ingest.py, transcribe.py, scan.py   stages 1-3
      sessions.py       Meeting Recorder files and their speakers
    workflows/
      report/
        workflow.py     ReportWorkflow: the stage list and what it adds to the run record
        models.py       Classification, FramePlan, Candidates, Evidence, ReportDraft
        prompts.py      system prompts and JSON schemas for the Claude calls
        classify.py, plan.py, extract.py, read.py, write.py, assemble.py   stages 4-9
        templates/
          registry.py   loads and validates templates
          builtin/      meeting, technical-discussion, lecture-tutorial, interview, language-practice,
                        casual-chat, generic (fallback)
  tests/                engine/, media/, understand/, workflows/report/ (same layout), fixtures/make_sample.py
```

Imports only go one way: `cli` → `workflows` → `understand` → `media` / `providers` → `engine`. The understanding
stages never depend on a workflow, so a new workflow (such as an edit plan for footage) reuses them as they are.

A stage is a small class with a `name`, a `version` (bumped when its algorithm changes, so cached results are
recomputed) and a `run` method that takes the earlier stages' results. Its cache key is a hash of what it depends on
plus that version. A workflow lists its stages and runs them in `execute`; the engine around it checks the folders,
handles `--fresh`, `--force` and `--clean-cache`, times every stage and writes the run record, also when a stage fails.

The core never prints from inside a stage. Stages emit events; `cli/sinks.py` renders them (Rich console, or one JSON
object per line with `--json`), and the Windows app reads the JSON.

### 3.2 Report workflow

```
1 ingest ─► 2 transcribe ─► 3 scan ─► 4 classify ─► 5 plan ─► 6 extract ─► 7 read ─► 8 report ─► 9 assemble
  local       local          local      Claude       Claude     local        Claude     Claude      local
```

Each stage takes the previous stages' results and returns its own typed result, which is cached (3.4). Stages that
need video (scan, plan, extract, read) are skipped for audio-only sessions.

| # | Stage | What it does | Result |
|---|---|---|---|
| 1 | ingest | Lists the folder's video/audio files in name order, probes them with ffprobe (duration, streams, size), fingerprints each file, labels Meeting Recorder audio (`audio-mic` = Me, `audio-system` = Others) | `Ingest` |
| 2 | transcribe | Decodes 16 kHz mono audio with ffmpeg and transcribes it with faster-whisper, in ~10 minute chunks cut at the quietest moment nearby, each chunk cached so an interrupted run resumes mid-file | `Transcript` |
| 3 | scan | Splits each video into visual segments (stretches whose content stays the same), with no AI | `VisualScan` |
| 4 | classify | One Claude call picks the template that fits, from the transcript, the segment list and a contact sheet of up to 16 thumbnails | `Classification` |
| 5 | plan | One Claude call decides which moments need a frame as evidence, guided by the template | `FramePlan` |
| 6 | extract | Decodes each requested range densely, groups near-duplicate frames and keeps the sharpest distinct ones | `Candidates` |
| 7 | read | Claude looks at the candidate frames in batches, drops irrelevant ones and reads their text verbatim | `Evidence` |
| 8 | report | One Claude call writes the report in Markdown from the template, transcript and evidence | `ReportDraft` |
| 9 | assemble | Writes `report.md` and copies only the frames and text the report references | output folder |

**Transcription.** faster-whisper runs with voice-activity detection. Without a language setting it re-detects the
language every 30 seconds, so mixed English/Vietnamese speech works. Previous text isn't fed back into the model,
which avoids repetition loops on long recordings. On a CUDA GPU it uses `float16`, otherwise `int8` on all CPU cores
(on a 16-thread laptop CPU, large-v3 runs at about 0.8× real time, large-v3-turbo about 1.3×). Only NVIDIA GPUs are
used; others fall back to the CPU.

**Visual scan.** Video is decoded at about 1 frame per second at 320×180. Frames are compared structurally rather than
pixel by pixel, so noise, compression and small moving things don't count as changes:

- Frames are blurred, isolated changed specks are dropped, and regions that keep changing (a clock, a webcam tile, an
  embedded video) are masked out.
- A change counts only when enough of the picture's area or edges differ for a sustained moment, compared against the
  segment's first frame so slow changes (scrolling, drawing on a whiteboard) are still caught.
- Stretches with continuous motion are marked *moving* (people on camera, playing video); inside them only a hard cut
  starts a new segment. Everything else is *static* (slides, screen share, whiteboard).
- Each segment records its edge density (a hint for text and diagrams) and a representative time for its thumbnail.

The scan only decides where to look. The frame plan reads the transcript as well, so it can still ask for moments
the scan didn't split on ("as you can see in this diagram").

**Classification.** When no template fits, or Claude's confidence is below 0.5, the run doesn't stop: it uses the
built-in `generic` template, which is never offered to Claude as a choice. The report then ends with a "Template fit"
section (why nothing fit, the closest templates, what doesn't match) and `reference/suggested-template.md`, a
ready-to-edit template for this kind of recording that the app can add to the user's templates. `--template <id>`
skips classification.

**Frame plan and extraction.** Each request is a time range with a reason, what to look for and a priority, capped at
30 per run by default (`--max-frames`). Very short ranges are widened and long ones capped at 60 seconds. Extraction
decodes the range at 2 fps, groups consecutive frames whose perceptual hashes are close, keeps the sharpest frame of
the largest groups (variance of the Laplacian), skips frames that duplicate one already chosen, and saves up to 3
candidates per request at full resolution (longest side 1568 px).

**Reading and the report.** Frames go to Claude six at a time with the reason each was requested. For each frame
Claude returns whether it is relevant, a caption, a description and the text in it. The report call gets the
template's sections and instructions, the file list, the accepted evidence (id, time, caption, description, text) and
the transcript, and refers to evidence by id. Assembly turns ids into relative links, drops references to unknown
ids with a warning, and copies only referenced frames into `reference/`.

**Meeting Recorder folders.** A folder with `video.mp4` (screen, no sound), `audio-mic.*` and `audio-system.*` is one
session recorded on one timeline. The two transcripts are merged into one conversation labelled *Me* and *Others*
before they go to Claude, and frames come from the video.

### 3.3 Claude access

Both backends implement the same small `LLM` interface (`generate_json` with a JSON Schema, `generate_text`):

- **subscription** (default): the Claude Agent SDK runs the bundled Claude Code binary, which signs in with the
  user's Claude login. The Claude Code profile (config folder) can be chosen with `--claude-profile`; it is passed as
  `CLAUDE_CONFIG_DIR`. Calls are made to behave like plain model calls: no tools, no setting sources, a custom system
  prompt, structured output through `output_format`, and `--no-session-persistence` so analyses don't add to the
  user's Claude history. Prompts and images go through stdin as a streamed message, because Windows caps a command
  line at about 32 KB. `ANTHROPIC_API_KEY` is removed from the environment so it can't silently take over the login.
  This backend is for personal use only.
- **api**: the Anthropic SDK with `ANTHROPIC_API_KEY`, billed per token.

Rate limits, overload and dropped connections are retried up to three times. The model defaults to the account's
default; `--effort` sets the thinking effort. Every call reports its tokens (input, cache reads and writes, output)
and, with the subscription, the cost at API rates as estimated by Claude Code.

### 3.4 Cache and resume

Stage results are stored in `<output>/.cache/`, each under a key that is a hash of everything the result depends
on: the input files' fingerprints (size plus a hash of the first and last 8 MB), the previous stages' results, the
relevant settings and template, the Claude model and effort, and a per-stage version number that is bumped when a
stage's algorithm changes. A run reuses every stage whose key is unchanged, so changing, say, the template reruns only
the stages after classification.

- *Analyse again* in the app, or rerunning the command, resumes from the cache, including a transcription that was
  stopped part way through a file.
- `--force <stage>` redoes that stage and every later one.
- `--fresh` (*Analyse from scratch* in the app) deletes the cache first, so everything runs again.
- `--clean-cache` deletes the cache after a successful run.

### 3.5 Events, logs and statistics

Stages emit events through a `Reporter`: `stage_started`, `progress` (completed/total), `activity` (live status of a
Claude call, such as "thinking" or "writing, 1,240 chars"), `info`, `warning`, `stage_done` / `stage_skipped`, and at
the end `summary`. The CLI adds `finished` or `error`.

`StageTimer` sits between the stages and the output. It records each stage's time and result (ran, cached, skipped,
failed), and attributes each Claude call to the stage in progress. Every call is logged as an `info` event with its
time and tokens. At the end of every run, also a failed one, it emits the performance summary: a table of stages, the
transcription speed, and Claude calls, time and tokens in total and per stage.

Every finished or failed run also appends one JSON object to `<data folder>/stats/runs.jsonl`: project and paths,
the input files with their fingerprints, template, settings, total time, Claude usage, and the per-stage records. The
fields are listed in the README.

### 3.6 Output folder

```
<output>/
  report.md                        the report, with relative links into reference/
  reference/
    frames/<file>_<hh-mm-ss>.jpg   only the frames the report uses
    text/<name>.md                 the text read from each of those frames
    suggested-template.md          only when no template fit
  .cache/                          working data, including the full timecoded transcript
```

`reference/` is rebuilt on every run so it never keeps frames an earlier report used. The transcript is working data,
not part of the deliverable.

### 3.7 Templates

A template is one Markdown file: YAML front matter (`id`, `name`, `description`, `signals` that help classification,
`evidence` that guides the frame plan, `sections` of the report) followed by instructions for the report writer.
User templates live in `<data folder>/templates` and replace built-in ones with the same id. `templates new <id>`
creates one from a skeleton.

## 4. Recording Reports (Windows app)

### 4.1 Code layout

```
apps/recording-reports/
  app.psd1, build.cmd, install.cmd, uninstall.cmd, deploy.cmd   identity and deployment (DEPLOYMENT.md)
  RecordingReports/
    Program.cs           single-instance mutex, --data-dir, starts TrayApp
    App/
      AppSettings.cs     settings.json: folders, names, ignored projects, core options, Claude profile
      AppPaths.cs, AppInitializer.cs   data folder layout, startup initialization, copying Media Analysis' data
      BuildInfo.cs, IAppHost.cs
    Analysis/
      AnalysisRunner.cs  queue, starts the core per project, follows its events, writes the log
      CoreProcess.cs     core.json (how to start the core) and the JSON event parser
    Projects/
      ProjectCatalog.cs  reads the projects folder into Project records and their state
      AutoAnalysis.cs    which new projects start on their own
      RunHistory.cs      last result per project (history.json)
      MediaDurations.cs  recording length per project, measured with ffprobe in the background, cached per file
      RunStatus.cs       plain-language names for the core's stages
    Templates/
      TemplateCatalog.cs
    UI/
      TrayApp.cs         tray icon and menu, watches the projects folder, close requests, IAppHost for the window
      MainForm.cs        window: Projects grid and its menus, Config, Templates and About tabs
      LogWindow.cs, LogFormatter.cs    live log view (readable and full)
      ReportOpener.cs    opens a report in VS Code with the chosen Claude profile
      TrayIcons.cs
  RecordingReports.Tests/  Analysis/, App/, Projects/, UI/ (same layout)
```

Namespaces follow the folders (`RecordingReports.Analysis`, ...); the project file imports them all.

### 4.2 How it works

- **Projects.** Every subfolder of the projects folder is a project; its report goes to `<output folder>\<folder
  name>`. A project is *analysed* when its `report.md` is newer than all of its media files, so adding a recording
  makes it waiting again. The folder is rescanned when it changes and every 30 seconds.
- **Running the core.** `core.json`, written by the build next to the exe, says which Python to run, where the core's
  code is and which environment variables to set (ffmpeg and model folders). For each analysis the app runs
  `python -m media_analysis report <project> <output> --json` with the options from Config, the Claude profile, the log
  path and the project's name. One analysis runs at a time; the rest wait in a queue.
- **Logs and history.** Each analysis gets a log in `<data folder>\logs`: a header (project, options, Claude profile),
  then every event line from the core, so the performance summary ends it. The window shows a readable view of it,
  live while it runs. The outcome, time and log path of the last run per project are kept in `history.json`.
- **States and icon.** Blue while an analysis runs or is queued, amber when a project is waiting, grey when all are
  analysed. Failures switch the icon to a warning until they are dismissed in the window.
- **Automatic analysis** (off by default) queues projects created after it was turned on, once their files have
  stopped changing for two minutes, and never retries a failed or stopped run.
- **Busy and closing.** The app is busy while an analysis runs or is queued. An install asks it to close through a
  named event; it refuses while busy and keeps working.
- **Resuming.** The running project (with the time spent) and the queue are saved in `pending.json`; when the app
  starts again it continues them, unless the user stopped them. The Elapsed column is the project's total processing
  time across continued runs.
- **Data folder.** Everything the app writes is under the folder given with `--data-dir` (settings, templates, logs,
  history, `pending.json`, `durations.json` with measured lengths, run statistics). The core gets the same folder through `MEDIA_ANALYSIS_HOME`.

## 5. Testing

- Core: unit tests for segmentation, hashing, audio chunking, cache keys, templates, classification, output assembly,
  the performance summary and run records (`uv run --project core pytest`). `core/tests/fixtures/make_sample.py`
  builds a short synthetic video with Windows text-to-speech for an end-to-end run.
- Recording Reports: tests for the catalog, settings, auto-analysis, durations, event parsing and migration
  (`dotnet test apps/recording-reports`). One test runs a real analysis through the app's runner when
  `MEDIA_ANALYSIS_TEST_CORE_DIR` and `MEDIA_ANALYSIS_TEST_PROJECT` are set.

## 6. Possible future work

- Speaker diarization (who said what) for recordings that aren't split per speaker.
- A helper that drafts a template from a finished run.
- Splitting the report call for transcripts too long for one Claude call (today it is a single call).
- Keeping each run's report, so older statistics records can point at the report they produced.
