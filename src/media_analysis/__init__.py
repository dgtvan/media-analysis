"""Turn a folder of video/audio recordings into a template-based Markdown report."""

__version__ = "0.1.0"


def main() -> None:
    from media_analysis.cli import run_cli

    run_cli()
