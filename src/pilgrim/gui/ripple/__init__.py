"""GUI of the ripple procedure; run it with ``uv run ripple``."""

from pilgrim.gui.app import run_window

from .window import RippleWindow

__all__ = ["RippleWindow", "main"]


def main() -> None:
    """Show the ripple window and run the application until the window is closed."""
    run_window(RippleWindow)
