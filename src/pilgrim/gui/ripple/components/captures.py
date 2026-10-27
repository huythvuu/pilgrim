"""Tab that shows the captures the running ripple procedure moves to this computer."""

import logging
from pathlib import Path

from pymeasure.display.Qt import QtCore, QtGui, QtWidgets
from pymeasure.display.widgets import TabWidget

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: What the tab says while no directory is watched.
IDLE_TEXT = "The captures of the running procedure appear here."
#: The pattern of the capture files the procedure saves.
CAPTURE_PATTERN = "*.png"


class CaptureView(QtWidgets.QLabel):
    """Label that shows a capture scaled to its size, keeping the aspect ratio.

    :param parent: The parent widget.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._source: QtGui.QPixmap | None = None
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        # A label grows to its pixmap but never shrinks unless told that any size will do.
        self.setMinimumSize(1, 1)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored
        )

    @property
    def source(self) -> QtGui.QPixmap | None:
        """Get the capture shown at full size, or None while text or nothing is shown."""
        return self._source

    def show_capture(self, pixmap: QtGui.QPixmap | None) -> None:
        """Show a capture, scaled to fit, or nothing.

        :param pixmap: The capture at full size, or None to clear the view.
        """
        self._source = pixmap
        if pixmap is None:
            self.clear()
        else:
            self._fit()

    def setText(self, text: str) -> None:
        """Show text instead of a capture.

        :param text: The text.
        """
        self._source = None
        super().setText(text)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """Refit the capture to the new size."""
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if self._source is None:
            return
        self.setPixmap(
            self._source.scaled(
                self.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )


class CapturesWidget(TabWidget, QtWidgets.QWidget):
    """List the captures in the running procedure's capture directory and show the selected one.

    The directory is looked at every ``refresh_time`` seconds once :meth:`watch` names it,
    which the window does when a procedure starts. A capture that appears or is replaced is
    selected, so the latest one is shown, scaled to fit. A button opens the directory in the
    file manager.

    :param refresh_time: The seconds between two looks at the directory.
    :param parent: The parent widget.
    """

    def __init__(self, refresh_time: float = 1.0, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Captures", parent)
        self._directory: Path | None = None
        # The modification time of every capture listed, by file name, to notice replacements.
        self._listed: dict[str, float] = {}
        self._setup_ui()
        self._layout()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(round(refresh_time * 1000))
        self._timer.timeout.connect(self.refresh)

    def _setup_ui(self) -> None:
        self.directory_label = QtWidgets.QLabel(IDLE_TEXT, self)
        self.directory_label.setWordWrap(True)
        self.open_button = QtWidgets.QPushButton("Open folder", self)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_directory)
        self.file_list = QtWidgets.QListWidget(self)
        self.file_list.currentItemChanged.connect(self._show_selected)
        self.image = CaptureView(self)

    def _layout(self) -> None:
        header = QtWidgets.QHBoxLayout()
        header.addWidget(self.directory_label, 1)
        header.addWidget(self.open_button)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, self)
        splitter.addWidget(self.file_list)
        splitter.addWidget(self.image)
        # Scaled to the width available, so the capture gets three quarters of it; stretch
        # factors would leave it a sliver next to the list's size hint.
        splitter.setSizes([250, 750])
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)

    @property
    def directory(self) -> Path | None:
        """Get the directory watched, or None."""
        return self._directory

    def watch(self, directory: Path | None) -> None:
        """Show the captures in a directory and keep looking for new ones.

        :param directory: The directory, which may not exist yet; None stops watching.
        """
        self._directory = directory
        self._listed.clear()
        self.file_list.clear()
        self.image.show_capture(None)
        if directory is None:
            self.directory_label.setText(IDLE_TEXT)
            self.open_button.setEnabled(False)
            self._timer.stop()
            return
        self.directory_label.setText(str(directory))
        self.open_button.setEnabled(True)
        self.refresh()
        self._timer.start()

    def refresh(self) -> None:
        """List the captures that appeared or changed since the last look and select the newest.

        A capture that could not be read yet, as it was still being written, is read again.
        """
        if self._directory is None or not self._directory.is_dir():
            return
        changed = sorted(
            (path.stat().st_mtime, path.name)
            for path in self._directory.glob(CAPTURE_PATTERN)
            if self._listed.get(path.name) != path.stat().st_mtime
        )
        current = self.file_list.currentItem()
        if not changed:
            if self.image.source is None and current is not None:
                self._show_selected(current)
            return
        for modified, name in changed:  # From the oldest, so the newest ends up on top.
            if name not in self._listed:
                self.file_list.insertItem(0, name)
            self._listed[name] = modified
        newest = changed[-1][1]
        if current is not None and current.text() == newest:
            self._show_selected(current)
        else:
            self.file_list.setCurrentRow(self._row_of(newest))

    def open_directory(self) -> None:
        """Open the watched directory in the file manager, if it exists."""
        if self._directory is None or not self._directory.is_dir():
            log.warning(f"The capture directory {self._directory} does not exist yet.")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._directory)))

    def _row_of(self, name: str) -> int:
        for row in range(self.file_list.count()):
            if self.file_list.item(row).text() == name:
                return row
        raise ValueError(f"{name} is not listed.")

    def _show_selected(
        self,
        current: QtWidgets.QListWidgetItem | None,
        previous: QtWidgets.QListWidgetItem | None = None,
    ) -> None:
        if current is None or self._directory is None:
            return
        pixmap = QtGui.QPixmap(str(self._directory / current.text()))
        if pixmap.isNull():
            # The procedure may still be writing the file; the next refresh reads it again.
            self.image.setText(f"Loading {current.text()}...")
            return
        self.image.show_capture(pixmap)
