"""Exceptions shared across the app."""


class MediaAnalysisError(Exception):
    """A failure the user can act on; the message is shown as-is."""

    exit_code = 1


class ProcessingStopped(MediaAnalysisError):
    """The pipeline stopped on purpose (e.g. no template fits the input)."""

    exit_code = 10
