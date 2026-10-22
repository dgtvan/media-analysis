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

## Installing on a shared Windows PC

Follows the [App deployment standard v1.3](https://github.com/dgtvan/local-app-deployment-strategy), adapted for a
CLI whose Python, libraries, ffmpeg and Whisper model are shared on `D:` by all accounts and builds; see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

```
publish.cmd          # agent/developer: publish a build to D:\Apps\MediaAnalysis
install.cmd          # each account, once: puts `media-analysis` on that account's PATH (updates itself on each run)
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
  `warning`, `stage_done` / `stage_skipped`, and finally `finished`, `stopped` (exit code 10) or `error`.
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
uv run media-analysis templates new sales-call      # creates %APPDATA%\MediaAnalysis\templates\sales-call.md
```

A template is a Markdown file with YAML front matter (`id`, `name`, `description`, `signals`, `evidence`, `sections`)
followed by instructions for the report writer. User templates override built-ins with the same id; `--templates-dir`
adds more folders.

When no template fits, the run stops (exit code 10) with the reason, the closest templates and a suggested new one.
Rerun with `--template <id>` or after creating a template; the transcript and scan are reused from the cache.

## Development

```
uv run pytest
uv run python tests/make_sample.py <folder>    # synthetic test video (Windows text-to-speech)
```
