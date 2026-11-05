"""Tests for the transient window, with the scope and the AFG replaced by mocks."""

import re
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from pymeasure.experiment import Procedure
from pytestqt.qtbot import QtBot

from pilgrim.gui.components import MessageWidget
from pilgrim.gui.components.message import PAUSING_TEXT, RUNNING_TEXT
from pilgrim.gui.transient import TransientWindow
from pilgrim.gui.transient.components import TransientInputsWidget
from pilgrim.procedure import TransientProcedure, procedure_transient
from pilgrim.procedure.procedure_ripple import capture_hyperlink
from pilgrim.procedure.procedure_transient import PAUSE_PROMPT

AFG_RESOURCE = "USB0::0x0699::0x0359::C012345::INSTR"
SCOPE_RESOURCE = "USB0::0x0699::0x0522::C031408::INSTR"
RUN_TIMEOUT = 10_000
#: The name of a run directory, with an index appended when a second has several.
RUN_DIRECTORY = re.compile(r"Results_\d{4}_\d{2}_\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?")
#: Two inputs: a falling edge on Vout at 1 kHz and a rising edge on Iout at 100 kHz.
TWO_INPUTS = np.array(
    [
        [1, 148, 85, 2.58, 2.38, 100, 30, 1],
        [100, 149, 84, 2.58, 2.38, 4, 50, 0],
    ],
    dtype=np.float64,
)
FIRST_PROMPT = "Input 1 of 2 set: 85 to 148 mV at 1kHz. Adjust the scope if required."
SECOND_PROMPT = "Input 2 of 2 set: 84 to 149 mV at 100kHz. Adjust the scope if required."
#: The captures of the two inputs, in run order.
CAPTURES = ["0Vin transient 85to148mV 1kHz.png", "0Vin transient 84to149mV 100kHz.png"]


def make_scope() -> MagicMock:
    """Return a mock scope with every measurement the procedure needs, always fully populated.

    The Vout measurements read a 5 V rail that rises to 5.5 V and dips to 4 V.
    """
    scope = MagicMock()
    names = [f"MEAS{number}" for number in range(1, 7)]
    kinds = {
        1: ("MEAN", "CH1"),
        2: ("MAXIMUM", "CH1"),
        3: ("MINIMUM", "CH1"),
        4: ("PK2PK", "CH1"),
        5: ("RISETIME", "CH2"),
        6: ("FALLTIME", "CH2"),
    }
    scope.measurement_names = names
    scope.measurements = {}
    scope.channels = {number: MagicMock() for number in range(1, 9)}
    scope.check_errors.return_value = []
    scope.read_file.return_value = b"capture"
    scope.read_populations.side_effect = lambda numbers: dict.fromkeys(numbers, 300)

    def add_child(cls: type, number: int, **kwargs: object) -> MagicMock:
        measurement = MagicMock()
        measurement.id = number
        measurement.type, measurement.source = kinds.get(number, ("PK2PK", "CH1"))
        measurement.mean, measurement.maximum, measurement.minimum = 5.0, 5.5, 4.0
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
def window(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TransientWindow:
    """Return a window set to run two inputs against a mock scope and a mock AFG."""
    monkeypatch.setattr(
        procedure_transient, "TektronixMSO58", lambda resource, **kwargs: make_scope()
    )
    monkeypatch.setattr(
        procedure_transient, "TektronixAFG31051", lambda resource, **kwargs: MagicMock()
    )
    monkeypatch.setattr(TransientProcedure, "INPUTS", TWO_INPUTS)
    window = TransientWindow()
    qtbot.addWidget(window)
    window.directory = str(tmp_path)
    window.inputs.afg_resource.setCurrentText(AFG_RESOURCE)
    window.inputs.scope_resource.setCurrentText(SCOPE_RESOURCE)
    return window


def run_directories(results_directory: Path) -> list[Path]:
    """Return the run directories in the results directory, oldest first, which is all it holds."""
    directories = sorted(results_directory.iterdir(), key=lambda path: path.stat().st_ctime_ns)
    assert all(path.is_dir() and RUN_DIRECTORY.fullmatch(path.name) for path in directories)
    return directories


def queue_without_running(window: TransientWindow, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Make the window's manager record the experiments queued instead of running them."""
    queued: list[Any] = []
    monkeypatch.setattr(window.manager, "queue", queued.append)
    return queued


def wait_for_prompt(qtbot: QtBot, window: TransientWindow, prompt: str) -> None:
    """Wait until the procedure shows ``prompt`` and waits for Resume."""
    qtbot.waitUntil(
        lambda: (
            window.message_widget.is_prompting and window.message_widget.message.text() == prompt
        ),
        timeout=RUN_TIMEOUT,
    )
    assert window.statusBar().currentMessage() == f"Paused: {prompt}"


def wait_for_end(qtbot: QtBot, window: TransientWindow) -> Any:
    """Wait until the running procedure has ended and return its experiment."""
    experiment = window.manager.running_experiment()
    qtbot.waitUntil(lambda: not window.manager.is_running(), timeout=RUN_TIMEOUT)
    return experiment


def test_is_built_for_the_transient_procedure(window: TransientWindow) -> None:
    assert window.procedure_class is TransientProcedure
    assert isinstance(window.inputs, TransientInputsWidget)
    assert isinstance(window.message_widget, MessageWidget)
    assert not window.message_widget.is_prompting
    assert window.message_widget.message.text() == TransientWindow.MESSAGE_IDLE_TEXT
    tabs = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert tabs == ["Results Table", "Captures", "Experiment Log"]
    assert window.table_widget.banner.path is None
    assert window.captures_widget.banner.path is None
    assert window.log_widget.banner.path is None
    assert window.filename == TransientWindow.DEFAULT_FILENAME
    assert window.windowTitle() == "Transient response"


def test_queue_builds_the_procedure_from_the_inputs(
    window: TransientWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued = queue_without_running(window, monkeypatch)
    window.inputs.vin.setValue(12)
    window.inputs.safe_mode.setChecked(False)
    window.queue()
    assert len(queued) == 1
    procedure = queued[0].procedure
    assert isinstance(procedure, TransientProcedure)
    assert procedure.pause_handler == window.message_widget.prompt
    assert (procedure.afg_resource, procedure.scope_resource) == (AFG_RESOURCE, SCOPE_RESOURCE)
    assert (procedure.vin, procedure.safe_mode) == (12, False)
    # The run gets its own directory, named from the time, where the results file goes and
    # the captures next to it.
    (run_directory,) = run_directories(tmp_path)
    assert Path(queued[0].data_filename) == run_directory / "transient_12Vin_1.csv"
    assert procedure.results_directory == run_directory
    assert procedure.capture_directory == run_directory / "captures"


def test_run_in_safe_mode_pauses_after_each_input_and_resumes_on_the_button(
    qtbot: QtBot, window: TransientWindow, tmp_path: Path
) -> None:
    window.queue()
    wait_for_prompt(qtbot, window, FIRST_PROMPT)
    assert not window.message_widget.pause_button.isEnabled()
    # The tabs name the files of the run, in its own directory, as soon as it starts.
    (run_directory,) = run_directories(tmp_path)
    assert window.table_widget.banner.path == run_directory / "transient_0Vin_1.csv"
    assert window.captures_widget.banner.path == run_directory / "captures"
    assert window.log_widget.banner.path == run_directory / "transient_0Vin_1.log"
    assert window.run_log.path == run_directory / "transient_0Vin_1.log"
    window.message_widget.resume_button.click()
    assert window.statusBar().currentMessage() == ""
    assert window.message_widget.message.text() == RUNNING_TEXT
    wait_for_prompt(qtbot, window, SECOND_PROMPT)
    assert [path.name for path in (run_directory / "captures").iterdir()] == CAPTURES[:1]
    window.message_widget.resume_button.click()
    experiment = wait_for_end(qtbot, window)
    assert experiment.procedure.status == Procedure.FINISHED
    assert not window.message_widget.is_prompting
    assert not window.message_widget.pause_button.isEnabled()
    assert list(experiment.results.data["Frequency (kHz)"]) == [1, 100]
    assert list(experiment.results.data["Load Step"]) == ["85 to 148 mV", "84 to 149 mV"]
    assert list(experiment.results.data["Overshoot (%)"]) == [10, 10]
    assert sorted(path.name for path in (run_directory / "captures").iterdir()) == sorted(CAPTURES)
    assert sorted(path.name for path in run_directory.iterdir()) == [
        "captures",
        "transient_0Vin_1.csv",
        "transient_0Vin_1.log",
    ]
    # The results file links each row to its capture, read back from the file as written.
    assert list(experiment.results.data["Capture"]) == [
        capture_hyperlink(Path("captures") / name) for name in CAPTURES
    ]
    assert window.captures_widget.directory == run_directory / "captures"
    window.captures_widget.refresh()
    assert window.captures_widget.file_list.count() == len(CAPTURES)


def test_run_without_safe_mode_needs_no_resume(qtbot: QtBot, window: TransientWindow) -> None:
    window.inputs.safe_mode.setChecked(False)
    window.queue()
    experiment = wait_for_end(qtbot, window)
    assert experiment.procedure.status == Procedure.FINISHED
    assert not window.message_widget.is_prompting
    assert list(experiment.results.data["Frequency (kHz)"]) == [1, 100]


def test_pause_button_holds_the_run_until_resume(
    qtbot: QtBot, window: TransientWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The populations stay short until the pause has been seen, so the run cannot outrun it.
    scope = make_scope()
    filled = threading.Event()
    scope.read_populations.side_effect = lambda numbers: dict.fromkeys(
        numbers, 300 if filled.is_set() else 0
    )
    monkeypatch.setattr(procedure_transient, "TektronixMSO58", lambda resource, **kwargs: scope)
    window.inputs.safe_mode.setChecked(False)
    window.queue()
    qtbot.waitUntil(window.message_widget.pause_button.isEnabled, timeout=RUN_TIMEOUT)
    window.message_widget.pause_button.click()
    assert window.message_widget.message.text() == PAUSING_TEXT
    wait_for_prompt(qtbot, window, PAUSE_PROMPT)
    assert not window.message_widget.pause_button.isEnabled()
    filled.set()
    window.message_widget.resume_button.click()
    experiment = wait_for_end(qtbot, window)
    assert experiment.procedure.status == Procedure.FINISHED
    assert list(experiment.results.data["Frequency (kHz)"]) == [1, 100]


def test_abort_while_paused_ends_the_run(qtbot: QtBot, window: TransientWindow) -> None:
    window.queue()
    wait_for_prompt(qtbot, window, FIRST_PROMPT)
    window.abort()
    assert not window.message_widget.is_prompting
    experiment = wait_for_end(qtbot, window)
    assert experiment.procedure.status == Procedure.ABORTED
    assert window.statusBar().currentMessage() == ""
    assert len(experiment.results.data) == 0


def test_run_log_records_the_set_up_and_the_pause(qtbot: QtBot, window: TransientWindow) -> None:
    window.queue()
    wait_for_prompt(qtbot, window, FIRST_PROMPT)
    window.abort()
    experiment = wait_for_end(qtbot, window)
    log_path = Path(experiment.data_filename).with_suffix(".log")
    assert window.run_log.path == log_path
    assert window.log_widget.banner.path == log_path
    text = log_path.read_text(encoding="utf-8")
    assert "Connecting to the scope." in text
    assert "Connecting to the AFG." in text
    assert "AFG set to a pulse holding its duty cycle, output off." in text
    assert f"Paused: {FIRST_PROMPT}" in text
