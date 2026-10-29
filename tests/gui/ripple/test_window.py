"""Tests for the ripple window, with the scope replaced by a mock."""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pymeasure.experiment import Procedure
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple import RippleWindow
from pilgrim.gui.ripple.components import LoadPromptWidget, RippleInputsWidget
from pilgrim.gui.ripple.window import DEFAULT_FILENAME
from pilgrim.procedure import RippleProcedure, procedure_ripple

RESOURCE = "USB0::0x0699::0x0522::C031408::INSTR"
RUN_TIMEOUT = 10_000


def make_scope() -> MagicMock:
    """Return a mock scope with one PK2PK measurement of CH1 whose population is always full."""
    scope = MagicMock()
    scope.measurement_names = ["MEAS1"]
    scope.measurements = {}
    scope.channels = {number: MagicMock() for number in range(1, 9)}
    scope.check_errors.return_value = []
    scope.read_file.return_value = b"capture"
    scope.read_populations.side_effect = lambda numbers: dict.fromkeys(numbers, 300)

    def add_child(cls: type, number: int, **kwargs: object) -> MagicMock:
        measurement = MagicMock()
        measurement.id = number
        measurement.type, measurement.source = "PK2PK", "CH1"
        measurement.maximum = 0.01
        measurement.uses_global_population_limit = False
        measurement.population_limit_enabled = False
        measurement.population_limit = 1000
        scope.measurements[number] = measurement
        return measurement

    scope.add_child.side_effect = add_child
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
    window.inputs.local_capture_dir.setText(str(tmp_path))
    return window


def queue_without_running(window: RippleWindow, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Make the window's manager record the experiments queued instead of running them."""
    queued: list[Any] = []
    monkeypatch.setattr(window.manager, "queue", queued.append)
    return queued


def wait_for_prompt(qtbot: QtBot, window: RippleWindow, load: int) -> None:
    """Wait until the procedure asks for ``load`` to be set."""
    prompt = f"Set the load to {load}."
    qtbot.waitUntil(
        lambda: window.load_prompt.is_prompting and window.load_prompt.message.text() == prompt,
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
    assert isinstance(window.load_prompt, LoadPromptWidget)
    assert not window.load_prompt.is_prompting
    tabs = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert tabs == ["Results Table", "Captures", "Results Graph", "Experiment Log"]
    assert window.filename == DEFAULT_FILENAME
    assert window.windowTitle() == "Ripple capture"


def test_queue_builds_the_procedure_from_the_inputs(
    window: RippleWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued = queue_without_running(window, monkeypatch)
    window.queue()
    assert len(queued) == 1
    procedure = queued[0].procedure
    assert isinstance(procedure, RippleProcedure)
    assert procedure.pause_handler == window.load_prompt.prompt
    assert procedure.scope_resource == RESOURCE
    assert procedure.combinations() == [(0, 1e-6, 20e6), (50, 1e-6, 20e6)]
    assert Path(queued[0].data_filename) == tmp_path / "ripple_0Vin_1.csv"


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


def test_run_pauses_for_every_load_and_continues_on_the_button(
    qtbot: QtBot, window: RippleWindow, tmp_path: Path
) -> None:
    window.queue()
    wait_for_prompt(qtbot, window, 0)
    window.load_prompt.continue_button.click()
    assert window.statusBar().currentMessage() == ""
    wait_for_prompt(qtbot, window, 50)
    assert sorted(path.name for path in (tmp_path / "captures").iterdir()) == [
        "0Vin ripple 0Load 1us 20MHz.png"
    ]
    window.load_prompt.continue_button.click()
    experiment = wait_for_end(qtbot, window)
    assert experiment.procedure.status == Procedure.FINISHED
    assert not window.load_prompt.is_prompting
    assert list(experiment.results.data["Load"]) == [0, 50]
    captures = ["0Vin ripple 0Load 1us 20MHz.png", "0Vin ripple 50Load 1us 20MHz.png"]
    assert sorted(path.name for path in (tmp_path / "captures").iterdir()) == captures
    assert window.captures_widget.directory == tmp_path / "captures"
    window.captures_widget.refresh()
    assert window.captures_widget.file_list.count() == len(captures)


def test_abort_while_paused_ends_the_run(qtbot: QtBot, window: RippleWindow) -> None:
    window.queue()
    wait_for_prompt(qtbot, window, 0)
    window.abort()
    assert not window.load_prompt.is_prompting
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
    window.load_prompt.continue_button.click()
    wait_for_prompt(qtbot, window, 50)
    window.abort()
    experiment = wait_for_end(qtbot, window)
    text = Path(experiment.data_filename).with_suffix(".log").read_text(encoding="utf-8")
    assert "DEBUG pilgrim.procedure.procedure_ripple: Poll 1: MEAS1=300 (min 300)" in text
    assert "Poll 1:" not in window.log_widget.view.toPlainText()
