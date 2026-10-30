"""Main window of the ripple GUI."""

import logging
import tempfile
from pathlib import Path

from pymeasure.display.manager import Experiment
from pymeasure.display.Qt import QtCore, QtGui, QtWidgets
from pymeasure.display.windows import ManagedWindowBase
from pymeasure.experiment import Results, unique_filename

from pilgrim.procedure import RippleProcedure
from pilgrim.procedure.procedure_ripple import (
    BANDWIDTH_COLUMN,
    DEFAULT_RESULTS_DIRECTORY,
    PK2PK_COLUMN,
    RIPPLE_COLUMN,
    VMAX_COLUMN,
    VMEAN_COLUMN,
    VMIN_COLUMN,
    make_run_directory,
)

from .components import (
    INPUTS,
    CapturesWidget,
    ExperimentLogWidget,
    MessageWidget,
    ResultsTableWidget,
    RippleInputsWidget,
)
from .run_log import RunLogHandler

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: The parameters the experiment browser shows a column of.
DISPLAYS = ("vin", "channel", "loads", "horizontal_divs", "bandwidths")
#: The default results file name, with parameter placeholders; PyMeasure appends an index.
DEFAULT_FILENAME = "{Procedure name}_{Vin}Vin"
#: The width in pixels of the handle between the tabs and the experiment browser.
SPLITTER_HANDLE_WIDTH = 8


class RippleWindow(ManagedWindowBase):
    """Run the ripple procedure: edit its parameters, queue runs and follow them.

    The left docks hold the parameter inputs, the results file settings, the Queue and Abort
    buttons, and the message panel, where the procedure asks for the load to be set whenever
    it pauses; Resume continues it, and Pause holds the running procedure at its next step. The
    parameter inputs scroll when the window is too short for them, while the file settings and
    the buttons stay in view below them. The tabs show the results as a table, the captures as
    they arrive, and the log, above the experiment browser; dragging the handle between them
    splits the height of the window between the two. Each tab names the results file, the
    capture directory or the log file of the running procedure and opens it on a button.
    Aborting a paused procedure ends it.

    Each run queued gets its own directory inside the results directory, named from the time
    it is queued (see :func:`make_run_directory`). The results file goes there, with the log of
    the run under the same name and the ``.log`` extension (see :class:`RunLogHandler`), and
    the captures in ``captures`` inside it. The log tab shows records from INFO up, while the
    file holds every record that reaches it, DEBUG included.

    :param parent: The parent widget.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        columns = RippleProcedure.DATA_COLUMNS
        self.table_widget = ResultsTableWidget("Results Table", columns, by_column=False)
        self.captures_widget = CapturesWidget()
        self.log_widget = ExperimentLogWidget("Experiment Log")
        super().__init__(
            RippleProcedure,
            widget_list=(
                self.table_widget,
                self.captures_widget,
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
        self.directory = str(Path.cwd() / DEFAULT_RESULTS_DIRECTORY)
        # Only results files with these columns can be opened into the window.
        self.browser_widget.browser.measured_quantities.update(
            [
                BANDWIDTH_COLUMN,
                VMEAN_COLUMN,
                VMAX_COLUMN,
                VMIN_COLUMN,
                PK2PK_COLUMN,
                RIPPLE_COLUMN,
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
        # ManagedWindowBase stacks the tabs and the browser in a splitter whose handle is thin
        # and blends into the background; a wider, shaded one is easy to find and drag.
        splitter = self.tabs.parentWidget()
        if isinstance(splitter, QtWidgets.QSplitter):
            self.splitter = splitter
            splitter.setObjectName("results_splitter")
            splitter.setHandleWidth(SPLITTER_HANDLE_WIDTH)
            splitter.setChildrenCollapsible(False)
            # The name keeps the style from the splitter inside the captures tab.
            splitter.setStyleSheet(
                "QSplitter#results_splitter::handle { background: palette(mid); }"
                "QSplitter#results_splitter::handle:hover { background: palette(highlight); }"
            )

    def queue(self, procedure: RippleProcedure | None = None) -> None:
        """Queue a run of the procedure, pausing through the message panel, for the inputs.

        The run gets its own directory inside the results directory, created now and named
        from the time, where its results file goes and its captures, in ``captures``. An input
        or a file name that is not valid is reported instead of queued.

        :param procedure: The procedure to run; built from the inputs when None.
        """
        if procedure is None:
            try:
                procedure = self.make_procedure()
            except ValueError as error:
                self.report_invalid_parameters(str(error))
                return
        procedure.pause_handler = self.message_widget.prompt
        run_directory = make_run_directory(Path(self.directory))
        procedure.results_directory = run_directory
        if self.store_measurement:
            try:
                filename = unique_filename(
                    str(run_directory),
                    prefix=self.file_input.filename_base,
                    datetimeformat="",
                    procedure=procedure,
                    ext=self.file_input.filename_extension,
                )
            except KeyError as error:
                run_directory.rmdir()  # Still empty, so nothing of the run is left behind.
                self.report_invalid_parameters(f"The file name is not valid: {error.args[0]}")
                return
        else:
            # Results reads back a file that exists, so the file must not be created yet.
            filename = str(Path(tempfile.mkdtemp(prefix="TempFile_")) / "results.csv")
        self.manager.queue(self.new_experiment(Results(procedure, filename)))

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
        """Name the files of the procedure that started in the tabs and enable Pause.

        :param experiment: The experiment whose procedure is running.
        """
        super().running(experiment)
        results_path = Path(experiment.data_filename)
        self.table_widget.set_results_path(results_path)
        self.captures_widget.watch(experiment.procedure.capture_directory)
        self.log_widget.set_log_path(self.run_log.start_run(results_path))
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
