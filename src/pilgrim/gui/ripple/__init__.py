"""GUI of the ripple procedure; run it with ``uv run pilgrim-ripple``."""

import logging
import sys

from pymeasure.display.Qt import QtWidgets

from .run_log import LOG_FORMAT, RunLogHandler
from .window import RippleWindow

__all__ = ["RippleWindow", "RunLogHandler", "main"]


def main() -> None:
    """Show the ripple window and run the application until the window is closed.

    The console shows records from INFO up. Pilgrim's own DEBUG records, such as every
    population poll with its read time, go only to the run log files.
    """
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=[console])
    logging.getLogger("pilgrim").setLevel(logging.DEBUG)
    app = QtWidgets.QApplication(sys.argv)
    window = RippleWindow()
    window.show()
    sys.exit(app.exec())
