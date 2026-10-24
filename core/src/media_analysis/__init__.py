"""Analyses folders of video and audio recordings."""

__version__ = "0.1.0"


def main() -> None:
    from media_analysis.cli.app import run_cli

    run_cli()
