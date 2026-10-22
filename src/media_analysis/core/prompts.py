"""Prompts and output schemas for the Claude calls."""

from __future__ import annotations

CLASSIFY_SYSTEM = """\
You classify recordings so the right report template can be used.

You get the list of available templates, the timecoded transcript of every file in the recording set (speech may be
English, Vietnamese or mixed; it comes from automatic speech recognition and can contain errors), a table of visual
segments found by local image analysis ("static" = the picture holds still, typically slides, a screen share, a
document or whiteboard, sometimes a still camera shot; "moving" = people on camera or playing video; edge density is
high for text and diagrams), and, when there is video, one contact sheet image with thumbnails.

Pick the single template whose description and signals best match the overall purpose of the recording set.
If none of them fits reasonably well, answer "none" rather than forcing a poor match.
Write `reason` in English, 1-3 sentences, citing concrete evidence from the transcript or visuals.
`closest` lists up to 3 template ids from best to worst match (excluding the chosen one).
When the answer is "none", `suggested_template` proposes a new template: a short id, a name, and one sentence
describing it. Otherwise leave it empty."""


def classify_schema(template_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "template_id": {"type": "string", "enum": [*template_ids, "none"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
            "closest": {"type": "array", "items": {"type": "string"}},
            "suggested_template": {"type": "string"},
        },
        "required": ["template_id", "confidence", "reason", "closest"],
    }


PLAN_SYSTEM = """\
You decide which moments of the video(s) deserve a screenshot to support a report.

You get the report template (its sections and what visual evidence matters for it), the timecoded transcript, and the
visual segments found by local image analysis. Each segment is a stretch where the picture stays essentially the same.
"static" segments hold still (typically slides, screen shares, documents or whiteboards; high edge density suggests text
or diagrams); "moving" segments show people on camera or playing video.

Request a moment only when the picture is likely to carry information the report needs, for example:
- a diagram, slide, document, code, table, dashboard or whiteboard that is being discussed;
- the speaker refers to something on screen ("as you can see", "this diagram", "on this slide", "look at line 20");
- a new static segment with text or diagrams appears during an important part of the discussion.
Skip talking heads and moments where the picture adds nothing. Fewer, well-chosen requests are better than many;
an empty list is fine if no visual evidence would help.

For each request give: file_id (only files that have video), start and end in seconds (a range of 2-60 seconds,
preferably within one visual segment, where the item should be clearly visible), reason (why it matters for the
report), look_for (what the frame should show), and priority (1 = essential, 2 = useful, 3 = nice to have)."""


def plan_schema(max_requests: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "requests": {
                "type": "array",
                "maxItems": max_requests,
                "items": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string"},
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "reason": {"type": "string"},
                        "look_for": {"type": "string"},
                        "priority": {"type": "integer", "minimum": 1, "maximum": 3},
                    },
                    "required": ["file_id", "start", "end", "reason", "look_for", "priority"],
                },
            }
        },
        "required": ["requests"],
    }


READ_SYSTEM = """\
You examine candidate screenshots taken from a recording, to decide which ones are useful evidence for a report.

Each image is preceded by its id, where it comes from, why it was requested and what it should show.
For every image return:
- relevant: true only if it clearly shows something useful for the report (ideally what was looked for). Mark
  blurry, transitional, empty or duplicate frames as not relevant; if two frames show the same content, keep only
  the clearer one.
- text: all readable text in the frame, verbatim, keeping line breaks and code indentation. Empty if none.
- description: 1-3 sentences in English describing what the frame shows (diagram structure, chart content, UI state).
- caption: a short English caption, at most 15 words."""

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "relevant": {"type": "boolean"},
                    "text": {"type": "string"},
                    "description": {"type": "string"},
                    "caption": {"type": "string"},
                },
                "required": ["id", "relevant", "text", "description", "caption"],
            },
        }
    },
    "required": ["frames"],
}

REPORT_SYSTEM = """\
You write the final report for a recording set, in Markdown, in English, following the given template.

Rules:
- Start with one H1 line: `# <a specific, descriptive title>`. Then use the template's sections as H2 headings, in order.
- Use only information found in the transcript and the evidence frames. Do not invent names, numbers, decisions or
  tasks. When something is unclear or missing, say so briefly.
- The transcript comes from automatic speech recognition and may contain errors or mixed English/Vietnamese. Translate
  to English; when quoting, you may keep the original words in brackets.
- Cite moments with timecodes like [00:12:31]; when there is more than one file, prefix the file id: [f2 00:12:31].
- Embed evidence frames where they support the text, each on its own line as `![caption](evidence:ID)` using only the
  given ids, each at most once. Quote or summarise important text read from a frame next to it.
- Output only the report Markdown, with no preamble or closing remarks."""
