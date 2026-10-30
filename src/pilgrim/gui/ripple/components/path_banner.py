"""Row that names a file or directory and opens it on a button."""

import logging
from pathlib import Path

from pymeasure.display.Qt import QtCore, QtGui, QtWidgets

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class PathBanner(QtWidgets.QWidget):
    """Name a file or directory and open it on a button.

    The label says ``idle_text`` until :meth:`set_path` names a path, which enables the button.
    A file opens in the application the system associates with it, and a directory in the file
    manager.

    :param idle_text: What the label says while no path is named.
    :param button_text: The text of the button.
    :param parent: The parent widget.
    """

    def __init__(
        self, idle_text: str, button_text: str, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._idle_text = idle_text
        self._path: Path | None = None
        self.label = QtWidgets.QLabel(idle_text, self)
        self.label.setWordWrap(True)
        self.open_button = QtWidgets.QPushButton(button_text, self)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_path)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.open_button)

    @property
    def path(self) -> Path | None:
        """Get the path named, or None."""
        return self._path

    def set_path(self, path: Path | None) -> None:
        """Name a path, which may not exist yet, and enable the button.

        :param path: The path; None names none and disables the button.
        """
        self._path = path
        self.label.setText(self._idle_text if path is None else str(path))
        self.open_button.setEnabled(path is not None)

    def open_path(self) -> None:
        """Open the path named, if it exists."""
        if self._path is None or not self._path.exists():
            log.warning(f"{self._path} does not exist yet.")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._path)))
