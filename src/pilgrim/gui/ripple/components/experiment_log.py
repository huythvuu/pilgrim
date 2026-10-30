"""Tab that shows the log and opens the log file of the running procedure."""

from pathlib import Path

from pymeasure.display.Qt import QtWidgets
from pymeasure.display.widgets import LogWidget

from .path_banner import PathBanner

#: What the banner says while no log file is named.
IDLE_TEXT = "The log file of the running procedure is named here."


class ExperimentLogWidget(LogWidget):
    """Show the log and open the log file of the running procedure.

    PyMeasure's log widget with, above the records, a banner that names the log file once
    :meth:`set_log_path` is called, which the window does when a procedure starts, and opens it
    on a button. The parameters are those of :class:`~pymeasure.display.widgets.LogWidget`.
    """

    def _setup_ui(self) -> None:
        super()._setup_ui()
        self.banner = PathBanner(IDLE_TEXT, "Open log", self)

    def _layout(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.banner)
        layout.addWidget(self.view, 1)

    def set_log_path(self, path: Path | None) -> None:
        """Name the log file the Open log button opens.

        :param path: The path of the log file; None names none.
        """
        self.banner.set_path(path)
