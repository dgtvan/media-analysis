# Changes and decisions

A record of the significant changes to this repo and why they were made, newest first. Each entry says what changed,
why, and what it means for later work. Details of how things work today are in [ARCHITECTURE.md](ARCHITECTURE.md)
and [DEPLOYMENT.md](DEPLOYMENT.md); this file keeps the reasons that the code doesn't show.

---

## 2026-10-26 · Optional transcript correction by Claude

**Change.** A `correct` stage after transcription: Claude returns only the transcript lines it changes to fix
clearly misheard words, using the whole meeting, the language mix and the terms list (`--correct-transcript`, a
Config checkbox, on by default in the app); the changes go to `.cache/transcript-corrections.md`.

**Why.** Claude can't take audio (all current models take text and images only), but it reads text well, and the
weak spot of Azure in Vietnamese is English terms. Compared on the 2026-10-26 stand-up (same Azure transcript,
report written with and without): the 11 corrections were right and conservative (ticket, framework, deploy, UAT,
meeting, remove, integrate, file MD, dev, email, rate limit, release) and left unclear words alone; the two reports
were of similar quality. Claude writing the report already worked out most of these words, so the gain is mostly a
more readable transcript and fewer "heard as" hedges.

**Consequences.** One more Claude call per meeting (about 20 s; $0.09 at API rates for 12 minutes). On by default in
Recording Reports at the user's request (fully automatic; the corrections are cheap and were accurate); off by
default on the command line. Not used by Footage Editor yet: corrected lines lose their word times, which cutting
between words needs.

## 2026-10-26 · Languages are always detected; the main language steers Azure and is shown

**Change.** Recording Reports has no Spoken language setting any more; the core detects. Transcript segments keep
their language, and each analysis measures the share of speech per language. With Azure, a chunk's main language
wins each 2-minute piece unless the other is more confident by over 0.1. Claude's transcript view starts with the
language mix and a warning that English terms inside Vietnamese speech are often misheard. The projects grid has a
Language column.

**Why.** The user doesn't want to choose a language per meeting, and a wrong fixed choice (English for Vietnamese
scrums) ruins a transcript. Forcing one main language on a whole meeting was rejected: an English demo inside a
Vietnamese meeting would be transcribed as Vietnamese. The bias only stops short or quiet pieces from flipping.

**Consequences.** Mixed languages within a 2-minute piece still come out in one language, and English terms inside
Vietnamese speech stay garbled; the note only helps Claude interpret them. Projects analysed before this show no
language until analysed again. The transcript view changed, so analysing again re-runs the Claude steps.

## 2026-10-26 · Every analysis records how it was made

**Change.** Each cached result has a `.made-with.json` beside it (when, core version, transcriber and its settings,
Claude models, backend and effort). Each run writes `how-made.md` into the output's `.cache/`, and the run statistics gain
the Claude models that answered, per-stage `made_with` and `reused`, and the resource use. Recording Reports passes
its resource level and has *How it was made* in the project menu.

**Why.** Comparing analyses (Whisper against Azure, one model against another) needs to know what made each one.
The settings alone weren't enough: the Claude model was recorded as "account default", and reused results were
described by the current settings, not by what made them.

**Consequences.** The details are working data in `.cache/`, not part of the deliverable (report.md and
reference/), which users share; so they don't name the Azure resource or the Claude profile. `--clean-cache`
removes them with the rest of the cache. Results cached before this change are listed as "details not kept".
The resource level is the one at the start; changes during a run are in the app's log.

## 2026-10-26 · Cloud transcription is Azure AI Speech, not Azure OpenAI Whisper

**Change.** The cloud engine is now Azure AI Speech fast transcription (`--transcriber azure-speech`) instead of
Azure OpenAI Whisper, which was removed. The apps' Config gains a technical terms list (a text file opened from
Config) and firewall help that points to the Azure portal instead of showing an IP address.

**Why.** The Azure subscription available only allows resources in Australia East. Whisper can't be deployed
there, and MAI-Transcribe-2 and LLM Speech (which detect Vietnamese and handle code-switching) were refused there
too ("Enhanced mode is currently not supported yet"). Plain fast transcription works there. On 10 minutes of a real
Vietnamese meeting with English terms it took 11–17 s; local Whisper had made up two "subscribe to the channel"
lines and lost ~45 s of speech, while Azure spelled English terms worse (subcription, layon). Choosing the language
by Azure's language detection failed on real meetings: it picked English for a mostly Vietnamese 10-minute chunk
(confidence 0.32 against 0.78 for Vietnamese) and refused another as "Multiple languages were identified". So,
without a language setting, each ~2 minute piece is transcribed in both languages and the more confident answer
kept, at twice the Azure cost. Azure
sees this network from a different IP than what's-my-IP sites report, so an IP shown by the app could be wrong.

**Consequences.** Mixed English/Vietnamese within a 2-minute piece is transcribed in one language. The terms list helps
English speech only. If MAI-Transcribe reaches Australia East, it is the same API with `enhancedMode` and could
replace plain fast transcription with little change.

## 2026-10-26 · Transcription engine can be chosen: Local Whisper or Azure OpenAI Whisper

**Change.** The transcribe stage goes through a `Transcriber` interface with two engines: faster-whisper on this
computer (default) and a Whisper deployment in Azure OpenAI (`--transcriber azure-openai --azure-endpoint <url>`, key
in `MEDIA_ANALYSIS_AZURE_KEY`). Both apps have an engine dropdown, the Azure endpoint and key, and *Test connection*.

**Why.** Local transcription on a CPU takes about as long as the recording, and longer at the Light resource level.
An Azure subscription credit is available, and Azure's Whisper is the same model family as the local one, so
transcripts, mixed English/Vietnamese and the fillers prompt behave much the same, and it returns word times, which
the edit workflow needs. Azure AI Speech and `gpt-4o-transcribe` were set aside: the first handles fillers and mixed
languages differently, the second returns no timestamps.

**Consequences.** The key is stored DPAPI-encrypted in each app's settings and reaches the core only through the
environment. The two apps keep separate settings, so the key is entered in each. Local transcripts keep their cache
keys; switching engines transcribes again. Azure detects the language once per 10-minute chunk instead of every 30
seconds, so heavily mixed speech may come out worse than locally; another engine (OpenAI, Azure AI Speech) is one more
`Transcriber` class plus an entry in the apps' dropdowns.

## 2026-10-26 · Recording Reports limits the resources an analysis uses

**Change.** Analyses run in a Windows job object with a CPU cap and a priority class. Config has a *Resource use*
level (Light 25% of the CPU at idle priority (default), Balanced 50% below normal, Fast 75% below normal, Full no
limit at normal priority). A running or queued project can be given its own level from its right-click menu.

**Why.** Transcription used every core at normal priority, which slowed the rest of the computer, and reports are
not needed right away. A job object covers the core and everything it starts (ffmpeg, Claude Code) with one limit,
and its limits can change while the analysis runs, so a project can be sped up without restarting it. Limiting the
core's threads instead would need a restart to speed up and wouldn't cover ffmpeg.

**Consequences.** The core is unchanged; Footage Editor isn't limited. At Light the local stages (mostly
transcription) can take up to four times as long as before; the Claude stages wait on the network and barely change. Raising a queued project above the Config level also moves it to the front of the queue. Only the
CPU is limited, not the GPU (transcription uses an NVIDIA GPU when there is one), memory or disk.

## 2026-10-25 · Footage Editor built on the edit workflow and a local service

**Change.** New `edit` workflow (profiles, footage view, cut rules, first edit, chat tools, versions, exports), a
local service (`media-analysis serve`) with a Project API, and the Footage Editor app: an HTML presentation in a .NET
WebView2 window. The duration limit is both a profile setting (default) and a project option next to the profile
(override).

**Why.**
- Claude plans with ids (phrases, shots, labels); code turns them into exact cuts and checks every id, so a wrong
  answer becomes a warning, not a broken edit. The first edit and every chat change use the same operations.
- Versions with parents instead of overwriting, so changes can be compared and undone.
- The service runs analyses as child processes (stoppable, isolated) and streams events over a WebSocket; the
  presentation only talks to the API, so it can be replaced without touching processing.
- The duration limit: the length a video should have depends as much on where it is going as on the kind of footage.
  As a profile setting only, every destination would need a copy of the profile; as a project option only, profiles
  like Short-form 9:16 couldn't carry their natural limit. So profiles hold a default and the project can override
  it. Claude can't add up cut lengths before the cuts are made, so a first plan that comes out too long gets one
  revision with the real length, and chat changes report when the edit goes over.

**Consequences.** Recording Reports is unchanged. Footage Editor doesn't start at sign-in and doesn't put
`media-analysis` on PATH (`app.psd1` options). CapCut export, a "look" tool for the chat, and music/sound-effect
libraries are next.

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
