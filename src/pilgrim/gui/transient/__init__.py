"""GUI of the transient procedure; run it with ``uv run transient``."""

from pilgrim.gui.app import run_window

from .window import TransientWindow

__all__ = ["TransientWindow", "main"]


def main() -> None:
    """Show the transient window and run the application until the window is closed."""
    run_window(TransientWindow)
