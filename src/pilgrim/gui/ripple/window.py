"""Main window of the ripple GUI."""

import logging
from pathlib import Path

from pymeasure.display.manager import Experiment
from pymeasure.display.Qt import QtCore, QtGui, QtWidgets
from pymeasure.display.widgets import LogWidget, PlotWidget, TableWidget
from pymeasure.display.windows import ManagedWindowBase

from pilgrim.procedure import RippleProcedure
from pilgrim.procedure.procedure_ripple import (
    BANDWIDTH_COLUMN,
    OVERSHOOT_COLUMN,
    PK2PK_COLUMN,
    UNDERSHOOT_COLUMN,
    VMAX_COLUMN,
    VMEAN_COLUMN,
    VMIN_COLUMN,
)

from .components import INPUTS, CapturesWidget, MessageWidget, RippleInputsWidget
from .run_log import RunLogHandler

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: The parameters the experiment browser shows a column of.
DISPLAYS = ("vin", "channel", "loads", "horizontal_divs", "bandwidths")
#: The default results file name, with parameter placeholders; PyMeasure appends an index.
DEFAULT_FILENAME = "{Procedure name}_{Vin}Vin"
#: The default directory of the results files, relative to the working directory.
DEFAULT_DIRECTORY = "results"


class RippleWindow(ManagedWindowBase):
    """Run the ripple procedure: edit its parameters, queue runs and follow them.

    The left docks hold the parameter inputs, the results file settings, the Queue and Abort
    buttons, and the message panel, where the procedure asks for the load to be set whenever
    it pauses; Resume continues it, and Pause holds the running procedure at its next step. The
    parameter inputs scroll when the window is too short for them, while the file settings and
    the buttons stay in view below them. The tabs show the results as a table and a graph, the
    captures as they arrive, and the log. Aborting a paused procedure ends it.

    The log of each run is also saved next to its results file, under the same name with the
    ``.log`` extension (see :class:`RunLogHandler`). The log tab shows records from INFO up,
    while the file holds every record that reaches it, DEBUG included.

    :param parent: The parent widget.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        columns = RippleProcedure.DATA_COLUMNS
        self.table_widget = TableWidget("Results Table", columns, by_column=False)
        self.captures_widget = CapturesWidget()
        self.plot_widget = PlotWidget(
            "Results Graph", columns, x_axis=BANDWIDTH_COLUMN, y_axis=VMAX_COLUMN
        )
        self.log_widget = LogWidget("Experiment Log")
        super().__init__(
            RippleProcedure,
            widget_list=(
                self.table_widget,
                self.captures_widget,
                self.plot_widget,
                self.log_widget,
            ),
            inputs=INPUTS,
            displays=DISPLAYS,
            inputs_in_scrollarea=True,
            parent=parent,
        )
        self.setWindowTitle("Ripple capture")
        self.setStatusBar(QtWidgets.QStatusBar(self))  # Shows what the procedure waits for.
        self.filename = DEFAULT_FILENAME
        self.directory = str(Path.cwd() / DEFAULT_DIRECTORY)
        # Only results files with these columns can be opened into the window.
        self.browser_widget.browser.measured_quantities.update(
            [
                BANDWIDTH_COLUMN,
                VMEAN_COLUMN,
                VMAX_COLUMN,
                VMIN_COLUMN,
                PK2PK_COLUMN,
                OVERSHOOT_COLUMN,
                UNDERSHOOT_COLUMN,
            ]
        )
        # The DEBUG records of a run would flood the log tab; the run log file keeps them.
        self.log_widget.handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self.log_widget.handler)
        # The manager knows the run before its worker starts, so the startup is logged too.
        self.run_log = RunLogHandler(self._running_results_path)
        logging.getLogger().addHandler(self.run_log)
        self.manager.finished.connect(self.run_log.end_run)
        self.manager.failed.connect(self.run_log.end_run)
        self.manager.abort_returned.connect(self.run_log.end_run)

    def _setup_ui(self) -> None:
        super()._setup_ui()
        # ManagedWindowBase builds PyMeasure's plain inputs; the ripple ones take their place.
        plain_inputs = self.inputs
        if isinstance(plain_inputs, QtWidgets.QWidget):
            plain_inputs.deleteLater()
        self.inputs = RippleInputsWidget(
            parent=self, inputs_in_scrollarea=self.inputs_in_scrollarea
        )
        self.message_widget = MessageWidget(self)
        self.message_widget.prompted.connect(self._show_pause_status)
        self.message_widget.pause_requested.connect(self.pause_procedure)
        self.message_widget.resumed.connect(self.resume_procedure)
        self.manager.aborted.connect(self._clear_message)
        self.manager.failed.connect(self._clear_message)

    def _layout(self) -> None:
        super()._layout()
        # ManagedWindowBase ends the inputs dock with a stretch, which would share the spare
        # height with the scrolling inputs; giving the inputs the stretch instead keeps the file
        # settings and the buttons right below them, in view however short the window is.
        inputs_dock = self.inputs.parentWidget()
        inputs_layout = None if inputs_dock is None else inputs_dock.layout()
        if isinstance(inputs_layout, QtWidgets.QBoxLayout):
            inputs_layout.setStretchFactor(self.inputs, 1)
        dock = QtWidgets.QDockWidget("Message", self)
        dock.setWidget(self.message_widget)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def queue(self, procedure: RippleProcedure | None = None) -> None:
        """Queue a run of the procedure, pausing through the message panel, for the inputs.

        An input that is not valid is reported instead of queued.

        :param procedure: The procedure to run; built from the inputs when None.
        """
        if procedure is None:
            try:
                procedure = self.make_procedure()
            except ValueError as error:
                self.report_invalid_parameters(str(error))
                return
        procedure.pause_handler = self.message_widget.prompt
        super().queue(procedure)

    def report_invalid_parameters(self, reason: str) -> None:
        """Tell the user why the inputs do not make a procedure.

        :param reason: The error message.
        """
        log.error(f"The parameters are not valid: {reason}")
        QtWidgets.QMessageBox.warning(self, "Invalid parameters", reason)

    def make_procedure(self) -> RippleProcedure:
        """Return the procedure the inputs describe.

        :raises ValueError: If an input is not valid.
        """
        return self.inputs.get_procedure()

    def pause_procedure(self) -> None:
        """Ask the running procedure to pause at its next step after the user pressed Pause."""
        experiment = self._running_experiment()
        if experiment is not None:
            experiment.procedure.request_pause()

    def resume_procedure(self) -> None:
        """Let the paused procedure continue after the user pressed Resume."""
        self.statusBar().clearMessage()
        experiment = self._running_experiment()
        if experiment is not None:
            experiment.procedure.resume()

    def abort(self) -> None:
        """Abort the running procedure, which ends it even while it is paused."""
        self._clear_message()
        super().abort()

    def running(self, experiment: Experiment) -> None:
        """Show the captures of the procedure that started and enable Pause.

        :param experiment: The experiment whose procedure is running.
        """
        super().running(experiment)
        self.captures_widget.watch(experiment.procedure.local_capture_directory)
        self.message_widget.set_running(True)

    def finished(self, experiment: Experiment) -> None:
        """Clear the message panel once a procedure ends.

        :param experiment: The experiment that finished.
        """
        super().finished(experiment)
        self._clear_message()

    def abort_returned(self, experiment: Experiment) -> None:
        """Clear the message panel once an aborted procedure has ended.

        :param experiment: The experiment that was aborted.
        """
        super().abort_returned(experiment)
        self._clear_message()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Stop showing and saving the log of the window that closes."""
        logging.getLogger().removeHandler(self.log_widget.handler)
        logging.getLogger().removeHandler(self.run_log)
        self.run_log.close()
        super().closeEvent(event)

    def _running_experiment(self) -> Experiment | None:
        return self.manager.running_experiment() if self.manager.is_running() else None

    def _running_results_path(self) -> Path | None:
        experiment = self._running_experiment()
        return None if experiment is None else Path(experiment.data_filename)

    def _show_pause_status(self, prompt: str) -> None:
        self.statusBar().showMessage(f"Paused: {prompt}")

    def _clear_message(self, experiment: Experiment | None = None) -> None:
        self.message_widget.clear()
        self.statusBar().clearMessage()
