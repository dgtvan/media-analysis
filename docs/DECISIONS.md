# Changes and decisions

A record of the significant changes to this repo and why they were made, newest first. Each entry says what changed,
why, and what it means for later work. Details of how things work today are in [ARCHITECTURE.md](ARCHITECTURE.md)
and [DEPLOYMENT.md](DEPLOYMENT.md); this file keeps the reasons that the code doesn't show.

---

## 2026-10-25 · Shared understanding upgrades (C1); reports read a compact transcript

**Change.**
- Reports now read the transcript as phrases (one speaker, gaps under 1 s, at most 15 s per line, with start and
  end) instead of one line per recognition segment, and Claude cites the line's start time. Long silences are marked;
  lines Whisper is known to invent over silence ("subscribe to the channel") are dropped.
- In a Meeting Recorder folder, a video that has sound is no longer transcribed: its sound is the mix of the mic and
  system files.
- A `condense` stage turns transcripts too long for one call into timecoded notes, part by part.
- New shared pieces for footage: word timestamps and verbatim fillers (opt-in, cache keys unchanged when off), shot
  detection with quality measures, pauses and loudness, Claude shot descriptions, proxies, filmstrips and the
  "look" image.

**Why.** Measured on a 30-minute stand-up re-analysed with the old and the new core (same model, same template): the
transcript Claude reads went from 49,600 characters in 1,231 lines to 20,300 in 191, the Claude stages from 142,000
to 76,000 input tokens (about a third less cost), with the same facts, decisions and actions in the report. Half of
the old text was the conversation repeated from the video's sound track, which also cost a third of the
transcription time. A first version with 30-second phrases made Claude cite time ranges, so phrases were shortened
and the citation rule tightened. Shot detection is our own (an adaptive colour-change detector, as PySceneDetect's)
because PySceneDetect pulls in the GUI build of OpenCV, which conflicts with the headless one.

**Consequences.** The report stages' versions were bumped, so analysing a project again reruns them (not
transcription). The footage pieces are ready for the edit workflow.

## 2026-10-25 · Footage Editor planned; the core stays generic

**Change.** A second app, Footage Editor, is planned: import footage, pick a profile, get a suggested multi-track
edit shown as a preview over a timeline of the full footage (cut parts dimmed), and change it only by chatting with
Claude. The work is ordered core first: shared understanding and media upgrades (C1), shared engine pieces (C2:
service mode, Claude tool kit, versioned documents, one profiles registry), then the edit workflow (C3), then the app.

**Why.**
- Reports and edits both start by understanding the media; putting the new analysis in the shared layers improves
  Recording Reports too (e.g. long meetings) instead of growing a second, separate pipeline.
- Lessons from similar projects (video-use, FireRed-OpenStoryline): give Claude a compact text view of the media
  rather than frames; let code, not the model, compute cut points; take ids, not free text, in tools; keep versions.
- The app is not an editor: the timeline explains what was cut, the chat changes it. That keeps the app small.
- The UI is a replaceable layer (HTML in WebView2 first) that only talks to a documented local API, so it can be
  swapped without touching processing.

## 2026-10-24 · Repo split into a shared core and apps; the app renamed to Recording Reports

**Change.** The Python core moved to `core/` and was split into `engine/`, `providers/`, `media/`, `understand/`
and `workflows/report/`, with each stage a small class. The Windows app moved to `apps/recording-reports/` and was
renamed from Media Analysis to Recording Reports (new install folder and data folder; the old data is copied on
first start). Deployment scripts became shared, reading each app's identity from its `app.psd1`.

**Why.** The core was about to serve a second job (editing footage). Separating what is shared (engine, media,
understanding) from what is report-specific keeps a new workflow from breaking reports, and one app per job keeps
each app simple. Cache keys were kept identical, so existing analyses were reused without redoing any work.

## 2026-10-23 · Deployment on the App deployment standard v2.3 with shared dependencies

**Change.** Builds go to `dist\`, installs to `%LOCALAPPDATA%\VanDang\<AppId>\app`; Python, the package store,
ffmpeg and Whisper models live in `%LOCALAPPDATA%\VanDang\dependencies`, shared by apps.

**Why.** The runtime is several GB; sharing it means installs and new apps cost almost nothing, and nothing has to be
copied per build.

## 2026-10-23 · Transcription speed: large-v3 with beam search stays

**Change.** None to the defaults (only fewer CPU threads were considered).

**Why.** A benchmark on a mixed English/Vietnamese meeting found every faster option (turbo model, batched decoding,
greedy decoding, an Intel GPU through OpenVINO) dropped passages or invented text. Real speed-ups need an NVIDIA GPU
or a cloud service.

## 2026-10-22 · First version: folder of recordings → report written to a template

**Change.** Local transcription (faster-whisper), a structural visual scan, Claude to pick the template, the frames
worth keeping and to write the report; a resumable, content-addressed cache; a tray app that watches a projects
folder.

**Why.** Recordings are long and personal: transcription and image work run locally, and Claude is used only where
judgement is needed, through the user's own subscription. Caching makes reruns and template changes cheap.
