"""Time formatting shared by prompts, logs and file names."""


def format_timecode(seconds: float, sep: str = ":") -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}{sep}{m:02d}{sep}{s:02d}"
