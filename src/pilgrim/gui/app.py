"""Start of the application of a procedure window."""

import logging
import sys

from pymeasure.display.Qt import QtWidgets

from .run_log import LOG_FORMAT


def run_window(window_class: type[QtWidgets.QMainWindow]) -> None:
    """Show a window and run the application until the window is closed.

    The console shows records from INFO up. Pilgrim's own DEBUG records, such as every
    population poll with its read time, go only to the run log files.

    :param window_class: The window to show, built without arguments.
    """
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=[console])
    logging.getLogger("pilgrim").setLevel(logging.DEBUG)
    app = QtWidgets.QApplication(sys.argv)
    window = window_class()
    window.show()
    sys.exit(app.exec())
