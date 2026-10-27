"""GUI of the ripple procedure; run it with ``uv run pilgrim-ripple``."""

import logging
import sys

from pymeasure.display.Qt import QtWidgets

from .window import RippleWindow

__all__ = ["RippleWindow", "main"]


def main() -> None:
    """Show the ripple window and run the application until the window is closed."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    app = QtWidgets.QApplication(sys.argv)
    window = RippleWindow()
    window.show()
    sys.exit(app.exec())
