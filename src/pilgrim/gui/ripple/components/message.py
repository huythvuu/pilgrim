"""Panel that shows the messages of the ripple procedure and lets the user pause and resume it."""

# QtPy builds Signal from the binding's class under an alias, which pyright takes for an import
# that is not re-exported.
# pyright: reportPrivateImportUsage=false

import logging

from pymeasure.display.Qt import QtCore, QtWidgets

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: What the panel says while no procedure runs.
IDLE_TEXT = "The procedure reports here when it pauses, whenever the load must be set or on Pause."
#: What the panel says while the procedure runs and may be paused.
RUNNING_TEXT = "Running. Pause holds the procedure at its next step."
#: What the panel says from Pause until the procedure has paused.
PAUSING_TEXT = "Pausing at the next step..."


class MessageWidget(QtWidgets.QWidget):
    """Show the messages of the ripple procedure, with Pause and Resume buttons.

    Pass :meth:`prompt` to the procedure as its ``pause_handler``: it may be called from the
    worker thread and hands the prompt to the GUI thread, where :meth:`show_prompt` displays it
    in bold, enables the Resume button and beeps. Pressing Resume emits :attr:`resumed` and
    shows the procedure as running again, where Pause is enabled; pressing Pause emits
    :attr:`pause_requested` and says so until the procedure's prompt arrives. Call
    :meth:`set_running` when a procedure starts and :meth:`clear` when it ends, so the buttons
    apply only while one runs.

    :param parent: The parent widget.
    """

    #: Emitted when the user presses Pause.
    pause_requested = QtCore.Signal()
    #: Emitted when the user presses Resume.
    resumed = QtCore.Signal()
    #: Emitted with the prompt once it is shown.
    prompted = QtCore.Signal(str)
    # Carries a prompt from any thread to the GUI thread through a queued connection.
    _requested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.message = QtWidgets.QLabel(IDLE_TEXT, self)
        self.message.setWordWrap(True)
        self.pause_button = QtWidgets.QPushButton("Pause", self)
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self._pause)
        self.resume_button = QtWidgets.QPushButton("Resume", self)
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self._resume)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.message)
        # Laid out as PyMeasure lays out Queue and Abort, so the buttons line up with them.
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.resume_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self._requested.connect(self.show_prompt, QtCore.Qt.ConnectionType.QueuedConnection)

    @property
    def is_prompting(self) -> bool:
        """Get whether a prompt is shown, so the procedure is waiting for Resume."""
        return self.resume_button.isEnabled()

    def prompt(self, prompt: str) -> None:
        """Show a prompt; safe to call from any thread, as the procedure's pause handler.

        :param prompt: What the user is to do, e.g. ``"Set the load to 50."``.
        """
        self._requested.emit(prompt)

    def show_prompt(self, prompt: str) -> None:
        """Display the prompt in bold, enable Resume and beep; call from the GUI thread.

        :param prompt: What the user is to do.
        """
        self._show(prompt, bold=True)
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(True)
        self.resume_button.setFocus()
        QtWidgets.QApplication.beep()
        log.debug(f"Prompting: {prompt}")
        self.prompted.emit(prompt)

    def set_running(self, running: bool) -> None:
        """Show that a procedure runs, with Pause enabled, or that none does, with no button.

        :param running: Whether a procedure runs.
        """
        self._show(RUNNING_TEXT if running else IDLE_TEXT, bold=False)
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(False)

    def clear(self) -> None:
        """Return to the idle state, with both buttons disabled."""
        self.set_running(False)

    def _show(self, text: str, bold: bool) -> None:
        self.message.setText(text)
        font = self.message.font()
        font.setBold(bold)
        self.message.setFont(font)

    def _pause(self) -> None:
        self._show(PAUSING_TEXT, bold=False)
        self.pause_button.setEnabled(False)
        self.pause_requested.emit()

    def _resume(self) -> None:
        self.set_running(True)
        self.resumed.emit()
