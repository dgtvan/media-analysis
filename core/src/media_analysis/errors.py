"""Exceptions shared across the app."""


class MediaAnalysisError(Exception):
    """A failure the user can act on; the message is shown as-is."""

    exit_code = 1
