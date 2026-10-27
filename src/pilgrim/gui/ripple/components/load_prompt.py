"""Panel that shows the prompt of a paused ripple procedure and lets the user continue it."""

# QtPy builds Signal from the binding's class under an alias, which pyright takes for an import
# that is not re-exported.
# pyright: reportPrivateImportUsage=false

import logging

from pymeasure.display.Qt import QtCore, QtWidgets

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: What the panel says while the procedure is not paused.
IDLE_TEXT = "The procedure pauses here whenever the load must be set."


class LoadPromptWidget(QtWidgets.QWidget):
    """Show what the user must do before the paused ripple procedure continues, with Continue.

    Pass :meth:`prompt` to the procedure as its ``pause_handler``: it may be called from the
    worker thread and hands the prompt to the GUI thread, where :meth:`show_prompt` displays it
    in bold, enables the Continue button and beeps. Pressing the button emits :attr:`continued`
    and clears the panel; :meth:`clear` does the latter when a paused procedure is aborted.

    :param parent: The parent widget.
    """

    #: Emitted when the user presses Continue.
    continued = QtCore.Signal()
    #: Emitted with the prompt once it is shown.
    prompted = QtCore.Signal(str)
    # Carries a prompt from any thread to the GUI thread through a queued connection.
    _requested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.message = QtWidgets.QLabel(IDLE_TEXT, self)
        self.message.setWordWrap(True)
        self.continue_button = QtWidgets.QPushButton("Continue", self)
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self._continue)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.message)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.continue_button)
        layout.addLayout(buttons)

        self._requested.connect(self.show_prompt, QtCore.Qt.ConnectionType.QueuedConnection)

    @property
    def is_prompting(self) -> bool:
        """Get whether a prompt is shown, so the procedure is waiting for Continue."""
        return self.continue_button.isEnabled()

    def prompt(self, prompt: str) -> None:
        """Show a prompt; safe to call from any thread, as the procedure's pause handler.

        :param prompt: What the user is to do, e.g. ``"Set the load to 50."``.
        """
        self._requested.emit(prompt)

    def show_prompt(self, prompt: str) -> None:
        """Display the prompt in bold, enable Continue and beep; call from the GUI thread.

        :param prompt: What the user is to do.
        """
        self.message.setText(prompt)
        self._set_bold(True)
        self.continue_button.setEnabled(True)
        self.continue_button.setFocus()
        QtWidgets.QApplication.beep()
        log.debug(f"Prompting: {prompt}")
        self.prompted.emit(prompt)

    def clear(self) -> None:
        """Return to the idle state, with Continue disabled."""
        self.message.setText(IDLE_TEXT)
        self._set_bold(False)
        self.continue_button.setEnabled(False)

    def _set_bold(self, bold: bool) -> None:
        font = self.message.font()
        font.setBold(bold)
        self.message.setFont(font)

    def _continue(self) -> None:
        self.clear()
        self.continued.emit()
