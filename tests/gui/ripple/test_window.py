"""Tests for the ripple window, with the scope replaced by a mock."""

import logging
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pymeasure.display.Qt import QtCore, QtWidgets
from pymeasure.experiment import Procedure
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple import RippleWindow
from pilgrim.gui.ripple.components import MessageWidget, RippleInputsWidget
from pilgrim.gui.ripple.components.message import PAUSING_TEXT, RUNNING_TEXT
from pilgrim.gui.ripple.window import DEFAULT_FILENAME, SPLITTER_HANDLE_WIDTH
from pilgrim.procedure import RippleProcedure, procedure_ripple
from pilgrim.procedure.procedure_ripple import PAUSE_PROMPT

RESOURCE = "USB0::0x0699::0x0522::C031408::INSTR"
RUN_TIMEOUT = 10_000


def make_scope() -> MagicMock:
    """Return a mock scope measuring the mean, maximum and minimum of CH1, always fully populated.

    A measurement the procedure adds joins the ones listed.
    """
    scope = MagicMock()
    names = ["MEAS1", "MEAS2", "MEAS3"]
    kinds = {1: "MEAN", 2: "MAXIMUM", 3: "MINIMUM"}
    scope.measurement_names = names
    scope.measurements = {}
    scope.channels = {number: MagicMock() for number in range(1, 9)}
    scope.check_errors.return_value = []
    scope.read_file.return_value = b"capture"
    scope.read_populations.side_effect = lambda numbers: dict.fromkeys(numbers, 300)

    def add_child(cls: type, number: int, **kwargs: object) -> MagicMock:
        measurement = MagicMock()
        measurement.id = number
        measurement.type, measurement.source = kinds.get(number, "PK2PK"), "CH1"
        measurement.mean, measurement.maximum, measurement.minimum = 0.01, 0.02, 0.0
        measurement.uses_global_population_limit = False
        measurement.population_limit_enabled = False
        measurement.population_limit = 1000
        scope.measurements[number] = measurement
        return measurement

    def add_measurement(number: int) -> MagicMock:
        names.append(f"MEAS{number}")
        return add_child(MagicMock, number)

    scope.add_child.side_effect = add_child
    scope.add_measurement.side_effect = add_measurement
    return scope


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RippleWindow:
    """Return a window set to run two loads at one scale and bandwidth against a mock scope."""
    monkeypatch.setattr(procedure_ripple, "TektronixMSO58", lambda resource, **kwargs: make_scope())
    window = RippleWindow()
    qtbot.addWidget(window)
    window.directory = str(tmp_path)
    window.inputs.scope_resource.setCurrentText(RESOURCE)
    window.inputs.loads.setText("[0, 50]")
    window.inputs.horizontal_divs.setText("[1 us]")
    window.inputs.bandwidths.setText("[20 MHz]")
    return window


def queue_without_running(window: RippleWindow, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Make the window's manager record the experiments queued instead of running them."""
    queued: list[Any] = []
    monkeypatch.setattr(window.manager, "queue", queued.append)
    return queued


def wait_for_prompt(qtbot: QtBot, window: RippleWindow, load: int | str) -> None:
    """Wait until the procedure asks for ``load`` to be set, or shows ``load`` as its prompt."""
    prompt = load if isinstance(load, str) else f"Set the load to {load}."
    qtbot.waitUntil(
        lambda: (
            window.message_widget.is_prompting and window.message_widget.message.text() == prompt
        ),
        timeout=RUN_TIMEOUT,
    )
    assert window.statusBar().currentMessage() == f"Paused: {prompt}"


def wait_for_end(qtbot: QtBot, window: RippleWindow) -> Any:
    """Wait until the running procedure has ended and return its experiment."""
    experiment = window.manager.running_experiment()
    qtbot.waitUntil(lambda: not window.manager.is_running(), timeout=RUN_TIMEOUT)
    return experiment


def test_is_built_for_the_ripple_procedure(window: RippleWindow) -> None:
    assert window.procedure_class is RippleProcedure
    assert isinstance(window.inputs, RippleInputsWidget)
    assert isinstance(window.message_widget, MessageWidget)
    assert not window.message_widget.is_prompting
    tabs = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert tabs == ["Results Table", "Captures", "Experiment Log"]
    assert window.filename == DEFAULT_FILENAME
    assert window.windowTitle() == "Ripple capture"


def test_tabs_and_browser_split_the_height_through_a_draggable_handle(
    qtbot: QtBot, window: RippleWindow
) -> None:
    splitter = window.splitter
    assert splitter.orientation() == QtCore.Qt.Orientation.Vertical
    assert splitter.widget(0) is window.tabs
    assert splitter.widget(1) is window.browser_widget
    assert splitter.handleWidth() == SPLITTER_HANDLE_WIDTH
    assert not splitter.childrenCollapsible()
    window.show()
    qtbot.waitExposed(window)
    tabs_height, browser_height = splitter.sizes()
    # Dragging the handle up by 100 pixels gives the browser that height from the tabs.
    handle = splitter.handle(1)
    start = handle.rect().center()
    qtbot.mousePress(handle, QtCore.Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(handle, pos=start - QtCore.QPoint(0, 100))
    qtbot.mouseRelease(handle, QtCore.Qt.MouseButton.LeftButton, pos=start - QtCore.QPoint(0, 100))
    assert splitter.sizes() == [tabs_height - 100, browser_height + 100]


def test_inputs_scroll_while_the_file_settings_stay_below_them(
    qtbot: QtBot, window: RippleWindow
) -> None:
    scroll_area = window.inputs.findChild(QtWidgets.QScrollArea)
    assert isinstance(scroll_area, QtWidgets.QScrollArea)
    dock = window.inputs.parentWidget()
    assert dock is not None
    layout = dock.layout()
    assert isinstance(layout, QtWidgets.QBoxLayout)
    assert layout.stretch(layout.indexOf(window.inputs)) == 1
    # Shorter than the inputs, the window scrolls them and keeps the file settings whole.
    window.show()
    qtbot.waitExposed(window)
    window.resize(window.width(), 400)
    qtbot.waitUntil(lambda: scroll_area.verticalScrollBar().maximum() > 0)
    assert dock.rect().contains(window.file_input.geometry())
    assert dock.rect().contains(window.queue_button.geometry())
    # Taller, the spare height goes to the inputs; the buttons stay at the bottom of the dock.
    window.resize(window.width(), 1000)
    qtbot.waitUntil(lambda: dock.height() > 2 * window.inputs.minimumSizeHint().height())
    gap_below_buttons = dock.height() - window.queue_button.geometry().bottom()
    assert gap_below_buttons < window.queue_button.height()


def test_pause_and_resume_line_up_with_queue_and_abort(qtbot: QtBot, window: RippleWindow) -> None:
    window.show()
    qtbot.waitExposed(window)

    def left(button: QtWidgets.QPushButton) -> int:
        return button.mapTo(window, button.rect().topLeft()).x()

    assert left(window.message_widget.pause_button) == left(window.queue_button)
    assert left(window.message_widget.resume_button) == left(window.abort_button)


def test_queue_builds_the_procedure_from_the_inputs(
    window: RippleWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued = queue_without_running(window, monkeypatch)
    window.queue()
    assert len(queued) == 1
    procedure = queued[0].procedure
    assert isinstance(procedure, RippleProcedure)
    assert procedure.pause_handler == window.message_widget.prompt
    assert procedure.scope_resource == RESOURCE
    assert procedure.combinations() == [(0, 1e-6, 20e6), (50, 1e-6, 20e6)]
    assert Path(queued[0].data_filename) == tmp_path / "ripple_0Vin_1.csv"
    # The captures go next to the results file.
    assert procedure.results_directory == tmp_path
    assert procedure.capture_directory == tmp_path / "captures"


def test_queue_reports_invalid_parameters_instead_of_raising(
    window: RippleWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued = queue_without_running(window, monkeypatch)
    reports: list[str] = []
    monkeypatch.setattr(window, "report_invalid_parameters", reports.append)
    window.inputs.loads.setText("[abc]")
    window.queue()
    assert queued == []
    assert len(reports) == 1
    assert "Loads" in reports[0]


def test_run_pauses_for_every_load_and_resumes_on_the_button(
    qtbot: QtBot, window: RippleWindow, tmp_path: Path
) -> None:
    window.queue()
    wait_for_prompt(qtbot, window, 0)
    assert not window.message_widget.pause_button.isEnabled()
    window.message_widget.resume_button.click()
    assert window.statusBar().currentMessage() == ""
    assert window.message_widget.message.text() == RUNNING_TEXT
    wait_for_prompt(qtbot, window, 50)
    assert sorted(path.name for path in (tmp_path / "captures").iterdir()) == [
        "0Vin ripple 0Load 1us 20MHz.png"
    ]
    window.message_widget.resume_button.click()
    experiment = wait_for_end(qtbot, window)
    assert experiment.procedure.status == Procedure.FINISHED
    assert not window.message_widget.is_prompting
    assert not window.message_widget.pause_button.isEnabled()
    assert list(experiment.results.data["Load"]) == [0, 50]
    captures = ["0Vin ripple 0Load 1us 20MHz.png", "0Vin ripple 50Load 1us 20MHz.png"]
    assert sorted(path.name for path in (tmp_path / "captures").iterdir()) == captures
    assert window.captures_widget.directory == tmp_path / "captures"
    window.captures_widget.refresh()
    assert window.captures_widget.file_list.count() == len(captures)


def test_pause_button_holds_the_run_until_resume(
    qtbot: QtBot, window: RippleWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The populations stay short until the pause has been seen, so the run cannot outrun it.
    scope = make_scope()
    filled = threading.Event()
    scope.read_populations.side_effect = lambda numbers: dict.fromkeys(
        numbers, 300 if filled.is_set() else 0
    )
    monkeypatch.setattr(procedure_ripple, "TektronixMSO58", lambda resource, **kwargs: scope)
    window.queue()
    wait_for_prompt(qtbot, window, 0)
    window.message_widget.resume_button.click()
    assert window.message_widget.pause_button.isEnabled()
    window.message_widget.pause_button.click()
    assert window.message_widget.message.text() == PAUSING_TEXT
    wait_for_prompt(qtbot, window, PAUSE_PROMPT)
    assert not window.message_widget.pause_button.isEnabled()
    filled.set()
    window.message_widget.resume_button.click()
    wait_for_prompt(qtbot, window, 50)
    window.message_widget.resume_button.click()
    experiment = wait_for_end(qtbot, window)
    assert experiment.procedure.status == Procedure.FINISHED
    assert list(experiment.results.data["Load"]) == [0, 50]


def test_abort_while_paused_ends_the_run(qtbot: QtBot, window: RippleWindow) -> None:
    window.queue()
    wait_for_prompt(qtbot, window, 0)
    window.abort()
    assert not window.message_widget.is_prompting
    experiment = wait_for_end(qtbot, window)
    assert experiment.procedure.status == Procedure.ABORTED
    assert window.statusBar().currentMessage() == ""
    assert len(experiment.results.data) == 0


def test_each_run_saves_its_log_next_to_its_results(
    qtbot: QtBot, window: RippleWindow, tmp_path: Path
) -> None:
    window.queue()
    window.queue()
    logs: list[Path] = []
    for run in range(2):
        if run:
            window.manager.resume()  # An abort holds back the rest of the queue.
        wait_for_prompt(qtbot, window, 0)
        window.abort()
        experiment = wait_for_end(qtbot, window)
        logs.append(Path(experiment.data_filename).with_suffix(".log"))
        assert window.run_log.path == logs[-1]
    assert [path.name for path in logs] == ["ripple_0Vin_1.log", "ripple_0Vin_2.log"]
    for path in logs:
        text = path.read_text(encoding="utf-8")
        assert "Connecting to the scope." in text
        assert "Paused: Set the load to 0." in text
        path.unlink()  # The file is closed once the run has ended.


def test_debug_records_reach_the_run_log_but_not_the_log_tab(
    qtbot: QtBot, window: RippleWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging.getLogger("pilgrim"), "level", logging.DEBUG)
    window.queue()
    wait_for_prompt(qtbot, window, 0)
    window.message_widget.resume_button.click()
    wait_for_prompt(qtbot, window, 50)
    window.abort()
    experiment = wait_for_end(qtbot, window)
    text = Path(experiment.data_filename).with_suffix(".log").read_text(encoding="utf-8")
    # Startup adds the PK2PK measurement, MEAS4, to the three the scope lists.
    poll = "Poll 1: MEAS1=300, MEAS2=300, MEAS3=300, MEAS4=300 (min 300)"
    assert f"DEBUG pilgrim.procedure.procedure_ripple: {poll}" in text
    assert "Poll 1:" not in window.log_widget.view.toPlainText()
