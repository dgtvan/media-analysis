# Architecture

The repo holds an analysis core and the Windows apps built on it. The core first works out what is in a folder of
video and audio (the speech, what is on screen); a **workflow** then turns that into a result. There are two:

- **report**, used by **Recording Reports**: a folder of recordings of one session becomes a report folder, a Markdown
  report written to a matching template plus the frames and the text read from them that back the report up.
- **edit**, used by **Footage Editor**: imported footage and a profile become a suggested multi-track edit, shown as a
  preview over a timeline of the full footage, and changed only by chatting with Claude. Every change is a version.

- **The core** (`core/src/media_analysis`, Python): the workflows and the shared understanding stages. It runs as a
  command-line program and knows nothing about the apps.
- **Recording Reports** (`apps/recording-reports`, .NET 10 WinForms): a tray app that watches a projects folder, runs
  the core's report workflow for each project and shows status, logs and settings. It starts the core as a child
  process and reads its progress.
- **Footage Editor** (`apps/footage-editor`): a window hosting an HTML presentation in WebView2. The .NET part only
  starts the core's local service and helps with files; everything else goes through the service's Project API
  (section 5).

```
           Recording Reports (apps/recording-reports)                  Core (core/src/media_analysis)
  ┌──────────────────────────────────────────┐   starts, per project   ┌────────────────────────────────┐
  │ TrayApp ── watches the projects folder   │ ──────────────────────► │ report <in> <out> --json       │
  │ MainForm ─ projects grid, Config, ...    │                         │   └─ ReportWorkflow (11 stages)│
  │ AnalysisRunner ─ queue, one run at a time│ ◄────────────────────── │        ├─ Whisper / Azure      │
  │ RunHistory, logs, MediaDurations         │   JSON events (stdout)  │        ├─ ffmpeg / OpenCV      │
  └──────────────────────────────────────────┘                         │        └─ Claude (Agent SDK)   │
                                                                       └────────────────────────────────┘
```

How it is built, installed and updated is in [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. Scope

- One input folder is one session (typically one or two recordings of 10–60 minutes). Its files are analysed
  together; anything that isn't video or audio is skipped with a warning.
- Speech in English, Vietnamese or both mixed. The transcript keeps the original language; the report is in English.
- Transcribes on this computer (an NVIDIA GPU when there is one, the CPU otherwise) or, when chosen, with Azure AI Speech
  fast transcription.
- Runs are resumable: finished work is never repeated unless asked for.
- Not covered: several folders combined into one report, audio/video clips as evidence (frames only), other input
  types (images, PDF, slides), speaker diarization beyond Meeting Recorder's microphone/system split.

## 2. Tech stack

| Concern | Choice |
|---|---|
| Core language | Python 3.12, managed with uv |
| Media decoding | ffmpeg / ffprobe |
| Transcription | faster-whisper (CTranslate2), `large-v3` by default; or Azure AI Speech fast transcription (REST, standard library only) |
| Image analysis | OpenCV (headless), NumPy, Pillow |
| Claude | Claude Agent SDK (subscription login) or the Anthropic SDK (API key) |
| CLI | Typer + Rich |
| Data | Pydantic models for every stage result, JSON in the cache |
| Templates | Markdown files with YAML front matter |
| Windows apps | .NET 10 WinForms, framework-dependent; Footage Editor's screen is HTML in WebView2 |
| Local service | FastAPI + uvicorn on 127.0.0.1 with a token, WebSocket events |

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
      cache.py          content-addressed cache, file fingerprints, how each cached result was made
      how_made.py       how-made.md: the settings and, per step, what ran, what was reused and what made it
      perf.py           StageTimer (timings, Claude calls and tokens), TimedLLM, summary, run statistics record
      events.py         progress events and the Reporter helper stages use to emit them
      settings.py       settings (transcription, scan, evidence, Claude), data folder, technical terms file
      errors.py         MediaAnalysisError (expected failures, with an exit code)
      hardware.py       CPU/GPU detection
      usage.py, timecode.py
    providers/
      transcriber.py    Transcriber interface: faster-whisper and Azure AI Speech; silence-aware chunking
      llm.py            Claude backends (subscription, api) behind one LLM interface
    media/              local media tools, no AI
      ffmpeg.py         probe, decode audio, iterate frames, extract stills, proxies, filmstrips, loudness
      segmentation.py   visual segmentation of screen recordings
      shots.py          shot detection and quality of camera footage
      frames.py         perceptual hash, sharpness, edges, contact sheets
      audio.py          pauses and waveform envelope
      look.py           the "look" image of one time range (frames, waveform, words)
    understand/         what is in the files, the same for every workflow
      models.py         MediaFile, Ingest, Transcript, VisualScan, ShotList, Sound, Descriptions
      ingest.py, transcribe.py, correct.py, scan.py, condense.py   stages used by reports
      languages.py      share of speech per language, the note Claude gets about it
      shots.py, sound.py, describe.py                  stages for footage
      views.py          the compact text views Claude reads (transcript phrases, scan table, shot list)
      sessions.py       Meeting Recorder files and their speakers
    workflows/
      report/
        workflow.py     ReportWorkflow: the stage list and what it adds to the run record
        models.py       Classification, FramePlan, Candidates, Evidence, ReportDraft
        prompts.py      system prompts and JSON schemas for the Claude calls
        classify.py, plan.py, extract.py, read.py, write.py, assemble.py   stages 5-10
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
1 ingest ─► 2 transcribe ─► 3 correct ─► 4 scan ─► 5 condense ─► 6 classify ─► 7 plan ─► 8 extract ─► 9 read ─► 10 report ─► 11 assemble
  local       local or      Claude†      local      Claude*       Claude        Claude     local        Claude     Claude       local
              Azure
```

\* only for transcripts too long for one call; otherwise skipped. † only with `--correct-transcript` (on by default in
Recording Reports).

Each stage takes the previous stages' results and returns its own typed result, which is cached (3.4). Stages that
need video (scan, plan, extract, read) are skipped for audio-only sessions.

| # | Stage | What it does | Result |
|---|---|---|---|
| 1 | ingest | Lists the folder's video/audio files in name order, probes them with ffprobe (duration, streams, size), fingerprints each file, labels Meeting Recorder audio (`audio-mic` = Me, `audio-system` = Others) | `Ingest` |
| 2 | transcribe | Decodes 16 kHz mono audio with ffmpeg and transcribes it with faster-whisper or Azure AI Speech, in ~10 minute chunks cut at the quietest moment nearby, each chunk cached so an interrupted run resumes mid-file | `Transcript` |
| 3 | correct | Claude fixes clearly misheard words (English terms and names in Vietnamese speech) using the whole meeting and the terms list; only with `--correct-transcript` | `CorrectedTranscript` |
| 4 | scan | Splits each video into visual segments (stretches whose content stays the same), with no AI | `VisualScan` |
| 5 | condense | Builds the transcript view Claude reads (phrases, see below); if it is longer than `transcript_budget_chars` (300,000 by default), Claude condenses it part by part into timecoded notes | text |
| 6 | classify | One Claude call picks the template that fits, from the transcript, the segment list and a contact sheet of up to 16 thumbnails | `Classification` |
| 7 | plan | One Claude call decides which moments need a frame as evidence, guided by the template | `FramePlan` |
| 8 | extract | Decodes each requested range densely, groups near-duplicate frames and keeps the sharpest distinct ones | `Candidates` |
| 9 | read | Claude looks at the candidate frames in batches, drops irrelevant ones and reads their text verbatim | `Evidence` |
| 10 | report | One Claude call writes the report in Markdown from the template, transcript and evidence | `ReportDraft` |
| 11 | assemble | Writes `report.md` and copies only the frames and text the report references | output folder |

**Transcription.** faster-whisper runs with voice-activity detection. Without a language setting it re-detects the
language every 30 seconds, so mixed English/Vietnamese speech works. Previous text isn't fed back into the model,
which avoids repetition loops on long recordings. On a CUDA GPU it uses `float16`, otherwise `int8` on all CPU cores
(on a 16-thread laptop CPU, large-v3 runs at about 0.8× real time, large-v3-turbo about 1.3×). Only NVIDIA GPUs are
used; others fall back to the CPU.

**Transcription engines.** The stage only knows the `Transcriber` interface (one chunk of 16 kHz audio in, its language
and segments out), so engines can be swapped; `--transcriber` chooses one:

- `local` (default): faster-whisper as above.
- `azure-speech`: Azure AI Speech fast transcription (`/speechtotext/transcriptions:transcribe`, api-version
  2025-10-15) at `--azure-endpoint`, the resource endpoint. Each ~10 minute chunk is encoded as FLAC and sent as one
  request; phrases come back with word times and are kept as segments. The language is the `--language` setting
  (`vi` → vi-VN, `en` → en-US). Without one, each ~2 minute piece is transcribed in both English and Vietnamese
  (4 requests at a time). The language that wins most of the chunk is its main language, and a piece keeps it unless
  the other is more confident there by over 0.1, so a short or quiet piece doesn't flip to the wrong language. Azure's
  own language detection wasn't used: it picked English for a mostly Vietnamese meeting, and refuses chunks with
  much of both ("Multiple languages were identified"). A chunk without speech counts as empty. Fillers ("ờ", "ừ", "um") are kept; the profanity filter is off.
  Technical terms from `transcription-terms.txt` in the data folder (one per line, `#` comments) go as the phrase
  list, at most 500; in tests they helped English speech, while the Vietnamese model ignored them. The key comes
  only from the `MEDIA_ANALYSIS_AZURE_KEY` environment variable, so it never appears in command lines, logs or run
  records. Rate limits (429) and server errors are retried, honouring `Retry-After`; a firewall refusal says how to
  allow this computer (the portal's *Add your client IP address*, since a what's-my-IP site can see another
  address than Azure does).

**Correcting the transcript** (`understand/correct.py`, `--correct-transcript`; a Config checkbox, on by default in
Recording Reports).
Between transcription and the rest, Claude reads the whole transcript as numbered lines in time order (with
speakers), plus the language mix and the technical terms list, and returns only the lines it changed: words clearly
misheard, mostly English terms and names inside Vietnamese speech. Lines keep their language, wording and fillers;
changed lines lose their word times. The corrected transcript is what the later stages read; the original stays in
the cache, and `.cache/transcript-corrections.md` lists every change. On a 12-minute Vietnamese stand-up it corrected 11
of 37 lines in 22 s (one call, about $0.09 at API rates).

**Languages of the speech.** Every segment keeps its language when the engine gives one (Azure per phrase, local
Whisper per chunk); `understand/languages.py` adds up the speech per language. The report's transcript view starts
with a note when the speech isn't all English, e.g. "Speech: Vietnamese 85%, English 15% (detected) … English
technical terms and names inside Vietnamese speech are often misheard, so work out what was meant from the
context." The shares go into the `finished` event (the app's Language column), how-made.md and the run statistics.
The app has no language setting: it always detects.

The cache key of a local transcript is the same as before engines could be chosen; an Azure transcript's key has the
engine, language, chunk length and a hash of the terms, so changing the resource doesn't transcribe again, while
switching engines or editing the terms does. `check-transcription` transcribes two seconds of near-silence to test the settings (the
apps' *Test connection*).

**What Claude reads of the transcript.** Speech is shown as phrases rather than raw recognition segments: consecutive
segments of one speaker are joined while the gaps stay under a second and the phrase under 30 seconds, and each line
gives its start and end (`[00:12:03-00:12:41] Others: ...`). Stretches of 20 seconds or more without speech get their
own line, since they often mean a screen share or a demo. On a 30-minute stand-up this cut the lines Claude reads by
about 85% (see [DECISIONS.md](DECISIONS.md)). Word-level times and verbatim fillers ("um") can be switched on in the
transcription settings for workflows that cut between words; they are off for reports and don't change the cache key
when off.

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

**Meeting Recorder folders.** A folder with `video.mp4`, `audio-mic.*` and `audio-system.*` is one session recorded on
one timeline. The two transcripts are merged into one conversation labelled *Me* and *Others* before they go to
Claude, and frames come from the video. When the video has sound too, it is only the mix of the two audio files, so it
isn't transcribed: that would take as long again and give Claude the conversation twice, without speakers.

**Shared understanding for other workflows.** `understand/` also has stages the report workflow doesn't use yet:
`shots` (camera footage split into shots with sharpness, shake and brightness; `media/shots.py`), `sound` (pauses and
loudness per file) and `describe` (Claude describes every shot from a contact sheet of three frames per shot, cached
per clip). `media/` has proxies (small constant-frame-rate copies for playback), filmstrips (frames side by side for
timelines) and the "look" image (frames, waveform, pauses and words for one time range, for Claude to check a moment
without watching the video).

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

Next to every cached result, `<key>.made-with.json` records how it was made: when, the core version, what the stage
noted (the transcriber's description and settings) and the Claude models that answered with the backend and effort.
Stages see the cache through `RecordingCache`, which writes these files when saving and, when a result is reused,
tells the timer how it was made, so a run's records describe reused results by what really made them rather than
by the current settings.

### 3.5 Events, logs and statistics

Stages emit events through a `Reporter`: `stage_started`, `progress` (completed/total), `activity` (live status of a
Claude call, such as "thinking" or "writing, 1,240 chars"), `info`, `warning`, `stage_done` / `stage_skipped`, and at
the end `summary`. The CLI adds `finished` or `error`.

`StageTimer` sits between the stages and the output. It records each stage's time and result (ran, cached, skipped,
failed), and attributes each Claude call to the stage in progress. Every call is logged as an `info` event with its
time and tokens. At the end of every run, also a failed one, it emits the performance summary: a table of stages, the
transcription speed, and Claude calls, time and tokens in total and per stage.

Every finished or failed run also appends one JSON object to `<data folder>/stats/runs.jsonl`: project and paths,
the input files with their fingerprints, template, settings, total time, Claude usage and models, and the per-stage
records including what made each stage's results. The fields are listed in the README.

Each finished run also writes `how-made.md` into the output's `.cache/` (`engine/how_made.py`): the run's date and length,
the resource use the app reported (`--resource-use`), the Claude login, model and effort, workflow lines (template
and key moments, or profile and length limit), and a table of steps with what this run did (ran, reused, skipped)
and what made each result (transcriber, Claude model, or this computer; for reused results, when). It is working
data like the rest of `.cache/`: the deliverable is only the report and its references.

### 3.6 Output folder

```
<output>/
  report.md                        the report, with relative links into reference/
  reference/
    frames/<file>_<hh-mm-ss>.jpg   only the frames the report uses
    text/<name>.md                 the text read from each of those frames
    suggested-template.md          only when no template fit
  .cache/                          working data, including the full timecoded transcript, how-made.md
                                   (section 3.5) and, with --correct-transcript, transcript-corrections.md
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
      ResourceLimit.cs   resource levels and the job object that applies them to the core and its processes
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
  `python -m media_analysis report <project> <output> --json` with the options from Config (transcription engine and
  endpoint, Whisper model, Claude profile and effort, key moments, transcript correction), the log path, the
  project's name and the resource level at the start (`--resource-use`, for how-made.md). The Azure key goes in the
  environment, never on the command line. The `finished` event's language shares are kept in `history.json` for the
  grid's Language column. One analysis runs at a time; the rest wait in a queue.
- **Resource use.** The core runs in a Windows job object, so its CPU cap and priority also cover the processes it
  starts (ffmpeg, Claude Code). Config sets the level for all analyses (Light 25% of the CPU at idle priority (default),
  Balanced 50% below normal, Fast 75% below normal, Full no limit at normal priority); a running or queued
  project can get its own level from its right-click menu. Changes apply to a running analysis at once and are
  written to its log. The limit matters for local work such as Local Whisper; with Azure transcription the core
  mostly waits on the network; a queued project raised above the Config level moves to the front. A project's own level is
  kept in `pending.json`, so it survives a restart, and is dropped when the analysis ends. Only the CPU is limited.
- **Logs and history.** Each analysis gets a log in `<data folder>\logs`: a header (project, options, Claude profile),
  then every event line from the core, so the performance summary ends it. The window shows a readable view of it,
  live while it runs. The outcome, time and log path of the last run per project are kept in `history.json`.
- **States and icon.** Blue while an analysis runs or is queued, amber when a project is waiting, grey otherwise. A new
  project that automatic analysis will start once its files stop changing isn't waiting: it doesn't turn the icon
  amber, count as not analysed or join *Analyse all not analysed*. Failures switch the icon to a warning until they are dismissed in the window.
- **Automatic analysis** (off by default) queues projects created after it was turned on, once their files have
  stopped changing for two minutes, and never retries a failed or stopped run.
- **Busy and closing.** The app is busy while an analysis runs or is queued. An install asks it to close through a
  named event; it refuses while busy and keeps working.
- **Resuming.** The running project (with the time spent) and the queue are saved in `pending.json`; when the app
  starts again it continues them, unless the user stopped them. The Elapsed column is the project's total processing
  time across continued runs.
- **Data folder.** Everything the app writes is under the folder given with `--data-dir` (settings, templates, logs,
  history, `pending.json`, `durations.json` with measured lengths, run statistics). The core gets the same folder through `MEDIA_ANALYSIS_HOME`.

## 5. Footage Editor and the edit workflow

### 5.1 Layers

```
Presentation   apps/footage-editor/ui (HTML, CSS, JS)        ← replaceable; talks only to the Project API
     │  HTTP + WebSocket on 127.0.0.1, token
Application    workflows/edit/api.py (projects, footage, analysis jobs, edit versions, chat)
Domain         workflows/edit (profiles, cut rules, first edit, chat tools) + understand/ (shared)
Engine         engine/service.py (server, events, jobs), engine/documents.py (versions), providers/agent.py
Host           apps/footage-editor/FootageEditor (.NET): window, WebView2, starts/stops the service, file dialogs
```

The service (`media-analysis serve --ui <folder>`) listens on a free port and prints its address as one JSON line;
the host reads it and opens it in WebView2. Analyses run as child processes of the CLI (`edit-project`), so they can
be stopped and can't take the service down; their JSON events go to the project's WebSocket channel. Chat turns run
in the service and stream Claude's text and each tool call to the same channel.

### 5.2 Edit workflow

```
ingest ─► transcribe ─► shots ─► sound ─► describe ─► prepare ─► sequence
local      local (words,   local    local    Claude      local      Claude
           fillers kept)
```

- **Profiles** (`workflows/edit/profiles/builtin/*.md`, user copies in `<data folder>/edit-profiles`): structured
  settings (what cuts follow, target length, optional maximum, aspect, pacing, clip lengths, structure, captions,
  b-roll, music, sound effects) plus guidance for Claude. A project can set its own maximum length next to the
  profile; it overrides the profile's.
- **What Claude reads** (`workflows/edit/view.py`): per clip its shots (id, times, description, problems) and its
  speech as numbered phrases (`f1.p12`).
- **Claude chooses, code cuts** (`cuts.py`): a main-track clip is a range of phrases, optionally starting or ending at
  given words, or a shot (or part of it). Code cuts between words with 80 ms of air before and 120 ms after, never
  reaching into the neighbouring word, and keeps shots within their bounds. Times are whole milliseconds.
- **The plan** (`models.py`): tracks T1 captions, V2 b-roll, V1 main, A1 dialogue (the sound of V1), A2 music, A3
  sound effects. Main clips play back to back; everything else is anchored to a main clip with an offset, so it moves
  with it. Music and sound effects are placeholders until there is a library.
- **First edit** (`sequence.py`): one Claude call returns the plan; code builds it with the same operations the chat
  uses (`draft.py`), so bad ids become warnings instead of broken edits. If it comes out longer than the limit,
  Claude gets one more call with the real length.
- **Chat** (`chat.py`): each message is one Claude turn with tools (find footage, add/replace/trim/move/remove clips,
  captions, sound effects, music, merge/split/rename tracks, remember a preference). Tools change a working copy; if
  anything changed it is saved as a new version with its parent, so any version can be compared or made current
  again (`engine/documents.py`). Each change reports the new length when it goes over the limit.
- **Exports** (`export.py`): `export/edit.json` (seconds, original file paths, every track and item) and
  `export/edit.md`, rewritten with every version.

### 5.3 Project folder

```
<data folder>/projects/<id>/
  project.json            name, profile, maximum length, imported footage paths (footage is never copied)
  .cache/                 stage results (shared understanding + edit stages), how-made.md
  media/<fingerprint>.proxy.mp4, .strip.jpg   proxies (720p, constant frame rate, keyframe every 0.5 s), filmstrips
  edit/index.json, v0001.json, ...            edit versions
  understanding.json      what the last finished analysis found (footage, transcript, shots, descriptions, the
                          view Claude reads), so the project opens and chats without re-running any stage
  chat/history.jsonl, memory.md, activity.jsonl   the conversation, lasting preferences, and analysis steps
  export/edit.json, edit.md
```

## 6. Testing

- Core: unit tests for segmentation, hashing, audio chunking, cache keys, templates, classification, output assembly,
  the performance summary and run records (`uv run --project core pytest`). `core/tests/fixtures/make_sample.py`
  builds a short synthetic video with Windows text-to-speech for an end-to-end run.
- Recording Reports: tests for the catalog, settings, auto-analysis, durations, event parsing and migration
  (`dotnet test apps/recording-reports`). One test runs a real analysis through the app's runner when
  `MEDIA_ANALYSIS_TEST_CORE_DIR` and `MEDIA_ANALYSIS_TEST_PROJECT` are set.
- Edit workflow and service: cut rules, plan operations, first-edit building, exports, versions, the Project API
  (in `core/tests`). Footage Editor host: `dotnet test apps/footage-editor`.

## 7. Possible future work

- CapCut export (its draft format), and editor exports such as OTIO/FCPXML.
- A "look" tool in the chat, so Claude can check a moment's frames, waveform and words before answering.
- Music and sound-effect libraries chosen by Claude; rendering the final video.
- Speaker diarization (who said what) for recordings that aren't split per speaker.
- A helper that drafts a template from a finished run.
- Keeping each run's report, so older statistics records can point at the report they produced.
