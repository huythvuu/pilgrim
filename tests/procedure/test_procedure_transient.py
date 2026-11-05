"""Tests for the transient procedure that need no instruments."""

import logging
import math
import threading
from collections.abc import Iterable, Mapping
from itertools import chain, repeat
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock

import numpy as np
import pytest
from numpy.typing import NDArray

from pilgrim.procedure import TransientProcedure, procedure_transient
from pilgrim.procedure.procedure_ripple import capture_hyperlink
from pilgrim.procedure.procedure_transient import Input

LOGGER = "pilgrim.procedure.procedure_transient"
SCOPE_ID = "TEKTRONIX,MSO58,C031408,CF:91.1CT FV:1.34.8"
AFG_ID = "TEKTRONIX,AFG31051,C012345,SCPI:99.0 FV:1.6.2"
#: The directories that exist on the mock scope: the default scope capture directory.
DEFAULT_DIRECTORIES = ["C:/Temp", "C:/Temp/Transient"]
#: The measurements the scope lists by default: the six the procedure requires.
DEFAULT_NAMES = [f"MEAS{number}" for number in range(1, 7)]
#: The ``(type, source)`` of the default measurements by number: Vout on CH1, Iout on CH2.
DEFAULT_KINDS = {
    1: ("MEAN", "CH1"),
    2: ("MAXIMUM", "CH1"),
    3: ("MINIMUM", "CH1"),
    4: ("PK2PK", "CH1"),
    5: ("RISETIME", "CH2"),
    6: ("FALLTIME", "CH2"),
}
#: The ``(mean, maximum, minimum)`` the default measurements read: a 5 V rail that rises to
#: 5.5 V and dips to 4 V with 0.25 V peak-to-peak, and a load current with a 1.5 us rise time
#: and a 2.5 us fall time.
DEFAULT_READINGS = {
    1: (5.0, 5.2, 4.8),
    2: (5.4, 5.5, 5.3),
    3: (4.1, 4.2, 4.0),
    4: (0.25, 0.3, 0.2),
    5: (1.5e-6, 1.6e-6, 1.4e-6),
    6: (2.5e-6, 2.6e-6, 2.4e-6),
}
#: The results the default readings give, in the units of the columns.
DEFAULT_VALUES = {
    "Mean Vmean (mV)": 5000.0,
    "Max Vmax (mV)": 5500.0,
    "Min Vmin (mV)": 4000.0,
    "Mean Vpk2pk (mV)": 250.0,
    "Overshoot (%)": 10.0,
    "Undershoot (%)": -20.0,
    "Mean Rise Time (us)": 1.5,
    "Mean Fall Time (us)": 2.5,
}
#: Two inputs: one triggering on the falling edge of Vout at 1 kHz, one on the rising edge of
#: Iout at 100 kHz.
TWO_INPUTS: NDArray[np.float64] = np.array(
    [
        [1, 148, 85, 2.58, 2.38, 100, 30, 1],
        [100, 149, 84, 2.58, 2.38, 4, 50, 0],
    ],
    dtype=np.float64,
)
FIRST_CAPTURE = "C:/Temp/Transient/12Vin transient 85to148mV 1kHz.png"
SECOND_CAPTURE = "C:/Temp/Transient/12Vin transient 84to149mV 100kHz.png"


@pytest.fixture(autouse=True)
def in_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in its own directory, where the default local capture directory lands."""
    monkeypatch.chdir(tmp_path)


def recorded_setting(events: list[tuple[Any, ...]], *label: object) -> PropertyMock:
    """Return a property that appends ``(*label, value)`` to ``events`` whenever it is set.

    :param events: Receives the settings.
    :param label: What the setting is, e.g. ``"trigger_level", 1``.
    """

    def record(*value: object) -> None:
        if value:  # Reads pass no value and are not recorded.
            events.append((*label, *value))

    return PropertyMock(side_effect=record)


def make_scope(
    names: list[str],
    populations: Iterable[int],
    events: list[tuple[Any, ...]],
    kinds: Mapping[int, tuple[str, str]] = DEFAULT_KINDS,
    readings: Mapping[int, tuple[float, float, float]] = DEFAULT_READINGS,
) -> MagicMock:
    """Return a mock scope listing ``names``, whose measurements read ``populations`` in turn.

    :param names: The measurement names the scope lists, e.g. ``["MEAS1"]``.
    :param populations: The populations read: every ``read_populations`` takes one per number
        asked for, in the order asked.
    :param events: Receives every clear, every acquisition start or stop as its value, every
        horizontal and trigger setting with its value, every trigger level set with its channel
        number, every measurement added with its number, every cursor pair set with its channel
        number and its levels, every cursor hiding, and every directory made, image saved and
        file deleted with its path.
    :param kinds: The ``(type, source)`` of the measurements by number; a number not listed is
        a PK2PK of CH1.
    :param readings: The ``(mean, maximum, minimum)`` the measurements read by number; a number
        ``n`` not listed reads ``n / 2``, ``n`` and ``-n``.
    """
    scope = MagicMock()
    scope.id = SCOPE_ID
    scope.measurement_names = names
    scope.measurements = {}
    scope.channels = {}
    for number in range(1, 9):
        channel = MagicMock()
        channel.id = number
        type(channel).trigger_level = recorded_setting(events, "trigger_level", number)
        scope.channels[number] = channel
    for setting in (
        "horizontal_scale",
        "horizontal_position",
        "trigger_type",
        "trigger_source",
        "trigger_slope",
    ):
        setattr(type(scope), setting, recorded_setting(events, setting))
    scope.cursor.set_levels.side_effect = lambda channel, low, high: events.append(
        ("set_levels", channel.id, low, high)
    )
    type(scope.cursor).enabled = recorded_setting(events, "cursor_enabled")

    existing = set(DEFAULT_DIRECTORIES)

    def change_directory(*path: str) -> None:
        if path and path[0] not in existing:
            raise ValueError("Tektronix MSO58 rejected the setting: 256, File name not found")

    def make_directory(path: str) -> None:
        events.append(("make_directory", path))
        existing.add(path)

    type(scope).working_directory = PropertyMock(side_effect=change_directory)
    scope.make_directory.side_effect = make_directory
    scope.save_image.side_effect = lambda path: events.append(("save_image", path))
    scope.read_file.side_effect = lambda path: f"image of {path}".encode()
    scope.delete_file.side_effect = lambda path: events.append(("delete_file", path))
    scope.check_errors.return_value = []
    counts = iter(populations)
    scope.read_populations.side_effect = lambda numbers: {n: next(counts) for n in numbers}

    def add_child(cls: type, number: int, **kwargs: object) -> MagicMock:
        measurement = MagicMock()
        measurement.id = number
        measurement.type, measurement.source = kinds.get(number, ("PK2PK", "CH1"))
        mean, maximum, minimum = readings.get(number, (number / 2, number, -number))
        measurement.mean, measurement.maximum, measurement.minimum = mean, maximum, minimum
        measurement.uses_global_population_limit = False
        measurement.population_limit_enabled = False
        measurement.population_limit = 1000
        scope.measurements[number] = measurement
        return measurement

    def add_measurement(number: int) -> MagicMock:
        events.append(("add_measurement", number))
        names.append(f"MEAS{number}")
        measurement = MagicMock()
        measurement.id = number
        scope.measurements[number] = measurement
        return measurement

    scope.add_child.side_effect = add_child
    scope.add_measurement.side_effect = add_measurement
    scope.clear_acquisitions.side_effect = lambda: events.append(("clear",))
    type(scope).acquisition_enabled = PropertyMock(
        side_effect=lambda *value: events.append(("acquisition", *value))
    )
    return scope


def make_afg(events: list[tuple[Any, ...]]) -> MagicMock:
    """Return a mock AFG that records every setting of its channel in ``events``.

    :param events: Receives ``("afg", <setting>, <value>)`` for every setting.
    """
    afg = MagicMock()
    afg.id = AFG_ID
    for setting in (
        "output_enabled",
        "shape",
        "pulse_hold",
        "frequency",
        "pulse_dutycycle",
        "amplitude_high",
        "amplitude_low",
        "pulse_leading",
        "pulse_trailing",
    ):
        setattr(type(afg.ch1), setting, recorded_setting(events, "afg", setting))
    return afg


def connect(
    monkeypatch: pytest.MonkeyPatch, scope: MagicMock, afg: MagicMock
) -> tuple[MagicMock, MagicMock]:
    """Make the instrument classes return the mocks.

    :param monkeypatch: Replaces the classes in the procedure module.
    :param scope: The mock scope.
    :param afg: The mock AFG.
    :returns: The mock scope class and the mock AFG class.
    """
    scope_class = MagicMock(return_value=scope)
    afg_class = MagicMock(return_value=afg)
    monkeypatch.setattr(procedure_transient, "TektronixMSO58", scope_class)
    monkeypatch.setattr(procedure_transient, "TektronixAFG31051", afg_class)
    return scope_class, afg_class


def make_running_procedure(
    monkeypatch: pytest.MonkeyPatch,
    scope: MagicMock,
    afg: MagicMock,
    inputs: NDArray[np.float64] = TWO_INPUTS,
    **parameters: object,
) -> TransientProcedure:
    """Return a procedure connected to ``scope`` and ``afg``, set up as a worker would run it.

    Every pause resumes at once, and safe mode is off unless ``parameters`` turn it on.

    :param monkeypatch: Replaces the polling sleep, the inputs and the worker's hooks.
    :param scope: The mock scope.
    :param afg: The mock AFG.
    :param inputs: The rows of ``INPUTS`` to go through.
    :param parameters: Parameter values by name.
    """
    monkeypatch.setattr(procedure_transient, "sleep", lambda seconds: None)
    monkeypatch.setattr(TransientProcedure, "INPUTS", inputs)
    procedure = TransientProcedure(**{"safe_mode": False, **parameters})
    procedure.refresh_parameters()
    procedure.scope = scope
    procedure.afg = afg
    procedure.pause_handler = lambda prompt: procedure.resume()
    monkeypatch.setattr(procedure, "should_stop", lambda: False)
    monkeypatch.setattr(procedure, "emit", MagicMock())
    return procedure


def record_pauses_and_resume(procedure: TransientProcedure, events: list[tuple[Any, ...]]) -> None:
    """Make every pause append ``("pause", prompt)`` to ``events`` and resume at once.

    :param procedure: The procedure.
    :param events: Receives the pauses.
    """

    def handler(prompt: str) -> None:
        events.append(("pause", prompt))
        procedure.resume()

    procedure.pause_handler = handler


def test_parameters_have_defaults() -> None:
    procedure = TransientProcedure()
    assert procedure.name == "transient"
    assert (procedure.afg_resource, procedure.scope_resource) == ("", "")
    assert (procedure.vin, procedure.duty_cycle) == (0, 50)
    assert (procedure.vout_channel, procedure.iout_channel) == (1, 2)
    assert (procedure.safe_mode, procedure.use_cursor) == (True, True)
    assert (procedure.cursor_hi_abs, procedure.cursor_lo_abs) == (0, 0)
    assert (procedure.iout_trigger_level, procedure.pop_min) == (144, 300)
    assert procedure.scope_capture_dir == "C:/Temp/Transient/"


def test_data_columns_are_in_order() -> None:
    assert TransientProcedure.DATA_COLUMNS == [
        "Vin (V)",
        "Load Step",
        "Frequency (kHz)",
        "Mean Vmean (mV)",
        "Max Vmax (mV)",
        "Min Vmin (mV)",
        "Mean Vpk2pk (mV)",
        "Overshoot (%)",
        "Undershoot (%)",
        "Mean Rise Time (us)",
        "Mean Fall Time (us)",
        "Capture",
    ]


def test_data_column_units_are_pint_units() -> None:
    units = TransientProcedure.parse_columns(TransientProcedure.DATA_COLUMNS)
    # PyMeasure's column parser does not read "%" as a unit, so the percent columns carry none,
    # like the columns without units.
    assert {column: str(unit) for column, unit in units.items()} == {
        "Vin (V)": "volt",
        "Frequency (kHz)": "kilohertz",
        "Mean Vmean (mV)": "millivolt",
        "Max Vmax (mV)": "millivolt",
        "Min Vmin (mV)": "millivolt",
        "Mean Vpk2pk (mV)": "millivolt",
        "Mean Rise Time (us)": "microsecond",
        "Mean Fall Time (us)": "microsecond",
    }


def test_inputs_are_read_row_by_row() -> None:
    inputs = TransientProcedure().inputs()
    assert len(inputs) == len(TransientProcedure.INPUTS)
    assert inputs[0] == Input(1, 148, 85, 2.58, 2.38, 100, 30, True)
    assert inputs[1] == Input(100, 149, 84, 2.58, 2.38, 4, 50, False)
    # The 1 kHz inputs trigger on the falling edge of Vout, the others on the rising edge of
    # Iout.
    assert [current.falling_edge for current in inputs] == [True, False, False, False] * 4
    assert inputs[0].describe() == "85 to 148 mV at 1kHz"
    assert inputs[3].describe() == "69 to 159 mV at 1MHz"
    assert inputs[10].load_step == "-17 to 97 mV"


def test_input_row_must_hold_one_value_per_field() -> None:
    with pytest.raises(ValueError, match="needs 8 values, not 3"):
        Input.from_row(np.array([1.0, 2.0, 3.0]))


def test_startup_connects_and_sets_up_both_instruments(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope([], [], events)
    afg = make_afg(events)
    type(scope).acquisition_enabled = PropertyMock(
        side_effect=lambda *value: events.append(("acquisition", *value)) or True
    )
    scope_class, afg_class = connect(monkeypatch, scope, afg)
    procedure = TransientProcedure(
        scope_resource="TCPIP::scope::INSTR",
        afg_resource="TCPIP::afg::INSTR",
        vout_channel=3,
        iout_channel=4,
        cursor_lo_abs=4.8,
        cursor_hi_abs=5.25,
    )
    procedure.refresh_parameters()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        procedure.startup()
    scope_class.assert_called_once_with("TCPIP::scope::INSTR", timeout=10_000)
    afg_class.assert_called_once_with("TCPIP::afg::INSTR")
    assert f"Connected to {SCOPE_ID}, found acquiring." in caplog.messages
    assert f"Connected to {AFG_ID}." in caplog.messages
    # The measurements and the cursors are set once acquisition has stopped; the AFG follows,
    # with its output kept off.
    assert events == [
        ("acquisition",),
        ("acquisition", False),
        ("add_measurement", 1),
        ("add_measurement", 2),
        ("add_measurement", 3),
        ("add_measurement", 4),
        ("add_measurement", 5),
        ("add_measurement", 6),
        ("set_levels", 3, 4.8, 5.25),
        ("afg", "output_enabled", False),
        ("afg", "shape", "PULS"),
        ("afg", "pulse_hold", "DUTY"),
    ]
    added = [(m.id, m.type, m.source) for m in scope.measurements.values()]
    assert added == [
        (1, "MEAN", "CH3"),
        (2, "MAXIMUM", "CH3"),
        (3, "MINIMUM", "CH3"),
        (4, "PK2PK", "CH3"),
        (5, "RISETIME", "CH4"),
        (6, "FALLTIME", "CH4"),
    ]
    assert "Cursors shown on CH3 at 4.8 V and 5.25 V." in caplog.messages


def test_startup_hides_the_cursors_when_they_are_not_used(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), [], events)
    connect(monkeypatch, scope, make_afg(events))
    procedure = TransientProcedure(use_cursor=False)
    procedure.refresh_parameters()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        procedure.startup()
    assert ("cursor_enabled", False) in events
    assert not [event for event in events if event[0] == "set_levels"]
    assert "The scope has every measurement required." in caplog.messages
    assert "Cursors hidden." in caplog.messages


def test_startup_raises_when_a_cursor_level_is_off_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), [], events)
    scope.cursor.set_levels.side_effect = ValueError("Level 9 V is outside the screen range")
    connect(monkeypatch, scope, make_afg(events))
    procedure = TransientProcedure(cursor_hi_abs=9)
    procedure.refresh_parameters()
    with pytest.raises(ValueError, match="outside the screen range"):
        procedure.startup()


def test_add_missing_measurements_keeps_the_existing_ones(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[tuple[Any, ...]] = []
    # A PK2PK of another channel does not stand in for the one of Vout.
    kinds = {1: ("MEAN", "CH1"), 2: ("RISETIME", "CH2"), 3: ("PK2PK", "CH5")}
    scope = make_scope(["MEAS1", "MEAS2", "MEAS3"], [], events, kinds=kinds)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        added = procedure.add_missing_measurements()
    assert [(m.id, m.type, m.source) for m in added] == [
        (4, "MAXIMUM", "CH1"),
        (5, "MINIMUM", "CH1"),
        (6, "PK2PK", "CH1"),
        (7, "FALLTIME", "CH2"),
    ]
    assert scope.measurement_names == [f"MEAS{number}" for number in range(1, 8)]
    assert "Added scope measurement MEAS4: MAXIMUM of CH1 (Vout)." in caplog.messages
    assert "Added scope measurement MEAS7: FALLTIME of CH2 (Iout)." in caplog.messages


def test_execute_sets_the_instruments_and_records_a_row_for_every_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), repeat(300), events)
    procedure = make_running_procedure(
        monkeypatch, scope, make_afg(events), vin=12, cursor_lo_abs=4.8
    )
    procedure.execute()
    # The AFG takes the frequency first and the edges last; the scope follows, triggering on the
    # falling edge of Vout at the low cursor level for the first input and on the rising edge
    # of Iout at the Iout trigger level for the second; each input then acquires once and ends
    # with its capture.
    assert events == [
        ("afg", "frequency", 1000.0),
        ("afg", "pulse_dutycycle", 50),
        ("afg", "amplitude_high", 0.148),
        ("afg", "amplitude_low", 0.085),
        ("afg", "pulse_leading", 2.58),
        ("afg", "pulse_trailing", 2.38),
        ("afg", "output_enabled", True),
        ("horizontal_scale", 1e-4),
        ("horizontal_position", 30.0),
        ("trigger_type", "EDGE"),
        ("trigger_source", "CH1"),
        ("trigger_slope", "FALL"),
        ("trigger_level", 1, 4.8),
        ("clear",),
        ("acquisition", True),
        ("acquisition", False),
        ("save_image", FIRST_CAPTURE),
        ("delete_file", FIRST_CAPTURE),
        ("afg", "frequency", 100000.0),
        ("afg", "pulse_dutycycle", 50),
        ("afg", "amplitude_high", 0.149),
        ("afg", "amplitude_low", 0.084),
        ("afg", "pulse_leading", 2.58),
        ("afg", "pulse_trailing", 2.38),
        ("afg", "output_enabled", True),
        ("horizontal_scale", 4e-6),
        ("horizontal_position", 50.0),
        ("trigger_type", "EDGE"),
        ("trigger_source", "CH2"),
        ("trigger_slope", "RISE"),
        ("trigger_level", 2, 0.144),
        ("clear",),
        ("acquisition", True),
        ("acquisition", False),
        ("save_image", SECOND_CAPTURE),
        ("delete_file", SECOND_CAPTURE),
    ]
    emit: Any = procedure.emit
    captures = Path("captures")
    first_row = {"Vin (V)": 12.0, "Load Step": "85 to 148 mV", "Frequency (kHz)": 1.0}
    second_row = {"Vin (V)": 12.0, "Load Step": "84 to 149 mV", "Frequency (kHz)": 100.0}
    assert [call.args for call in emit.call_args_list] == [
        (
            "results",
            {
                **first_row,
                **DEFAULT_VALUES,
                "Capture": capture_hyperlink(captures / "12Vin transient 85to148mV 1kHz.png"),
            },
        ),
        ("progress", 50.0),
        (
            "results",
            {
                **second_row,
                **DEFAULT_VALUES,
                "Capture": capture_hyperlink(captures / "12Vin transient 84to149mV 100kHz.png"),
            },
        ),
        ("progress", 100.0),
    ]
    local = Path("results") / captures / "12Vin transient 85to148mV 1kHz.png"
    assert local.read_bytes() == f"image of {FIRST_CAPTURE}".encode()


def test_execute_acquires_until_every_population_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    # Three polls until every measurement is full, then one more read after the stop.
    polls = [(0,) * 6, (299, 300, 300, 300, 300, 300), (300,) * 6, (310,) * 6]
    populations = iter(chain.from_iterable(polls))
    scope = make_scope(list(DEFAULT_NAMES), populations, events)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events), inputs=TWO_INPUTS[:1])
    procedure.execute()
    assert scope.read_populations.call_count == len(polls)
    assert next(populations, None) is None
    assert [event for event in events if event[0] == "acquisition"] == [
        ("acquisition", True),
        ("acquisition", False),
    ]


def test_execute_in_safe_mode_pauses_with_the_acquisition_running_after_each_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), repeat(300), events)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events), safe_mode=True)
    record_pauses_and_resume(procedure, events)
    procedure.execute()
    kinds = ("trigger_level", "acquisition", "pause", "clear", "save_image")
    # The trace is live while the user adjusts the scope, and the statistics are cleared again
    # on resume, so nothing taken meanwhile counts.
    assert [event for event in events if event[0] in kinds] == [
        ("trigger_level", 1, 0.0),
        ("clear",),
        ("acquisition", True),
        ("pause", "Input 1 of 2 set: 85 to 148 mV at 1kHz. Adjust the scope if required."),
        ("clear",),
        ("acquisition", False),
        ("save_image", "C:/Temp/Transient/0Vin transient 85to148mV 1kHz.png"),
        ("trigger_level", 2, 0.144),
        ("clear",),
        ("acquisition", True),
        ("pause", "Input 2 of 2 set: 84 to 149 mV at 100kHz. Adjust the scope if required."),
        ("clear",),
        ("acquisition", False),
        ("save_image", "C:/Temp/Transient/0Vin transient 84to149mV 100kHz.png"),
    ]


def test_execute_does_not_pause_without_safe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), repeat(300), events)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events), safe_mode=False)
    record_pauses_and_resume(procedure, events)
    procedure.execute()
    assert not [event for event in events if event[0] == "pause"]


def test_execute_stops_while_paused_in_safe_mode_when_asked_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), repeat(300), events)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events), safe_mode=True)
    monkeypatch.setattr(procedure_transient, "POLL_INTERVAL", 0.001)
    stop = threading.Event()
    procedure.pause_handler = lambda prompt: stop.set()
    monkeypatch.setattr(procedure, "should_stop", stop.is_set)
    procedure.execute()
    # The acquisition started for the live trace is stopped, and never cleared for a resume.
    assert events[-3:] == [("clear",), ("acquisition", True), ("acquisition", False)]
    assert events.count(("clear",)) == 1
    emit: Any = procedure.emit
    emit.assert_not_called()


def test_execute_stops_acquisition_when_asked_to_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), repeat(0), events)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events))
    monkeypatch.setattr(procedure, "should_stop", lambda: True)
    procedure.execute()
    assert events[-3:] == [("clear",), ("acquisition", True), ("acquisition", False)]
    emit: Any = procedure.emit
    emit.assert_not_called()


def test_request_pause_stops_acquisition_until_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), chain([0] * 6, repeat(300)), events)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events), inputs=TWO_INPUTS[:1])
    record_pauses_and_resume(procedure, events)
    read = scope.read_populations.side_effect

    def request_at_first_poll(numbers: list[int]) -> dict[int, int]:
        procedure.request_pause()
        scope.read_populations.side_effect = read
        return read(numbers)

    scope.read_populations.side_effect = request_at_first_poll
    procedure.execute()
    assert [event for event in events if event[0] in ("acquisition", "pause", "clear")] == [
        ("clear",),
        ("acquisition", True),
        ("acquisition", False),
        ("pause", "Paused as requested."),
        ("acquisition", True),
        ("acquisition", False),
    ]


def test_request_pause_during_a_capture_pauses_before_the_next_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), repeat(300), events)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events))
    record_pauses_and_resume(procedure, events)

    def save_and_request_pause(path: str) -> None:
        events.append(("save_image", path))
        procedure.request_pause()

    scope.save_image.side_effect = save_and_request_pause
    procedure.execute()
    marks = [
        event
        for event in events
        if event[0] in ("save_image", "pause") or event[:2] == ("afg", "frequency")
    ]
    # The request made during the last capture is not honoured, since no input follows.
    assert marks == [
        ("afg", "frequency", 1000.0),
        ("save_image", "C:/Temp/Transient/0Vin transient 85to148mV 1kHz.png"),
        ("pause", "Paused as requested."),
        ("afg", "frequency", 100000.0),
        ("save_image", "C:/Temp/Transient/0Vin transient 84to149mV 100kHz.png"),
    ]


def test_execute_without_a_required_measurement_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(["MEAS1", "MEAS2", "MEAS3", "MEAS4"], [], events)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events))
    with pytest.raises(ValueError, match=r"no RISETIME measurement of CH2 \(Iout\)"):
        procedure.execute()


def test_execute_without_measurements_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    procedure = make_running_procedure(monkeypatch, make_scope([], [], []), make_afg([]))
    with pytest.raises(ValueError, match="no measurements"):
        procedure.execute()


def test_execute_makes_missing_capture_directories(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), repeat(300), events)
    procedure = make_running_procedure(
        monkeypatch,
        scope,
        make_afg(events),
        inputs=TWO_INPUTS[:1],
        scope_capture_dir="C:/Temp/Transient/dut1/",
    )
    procedure.execute()
    assert ("make_directory", "C:/Temp/Transient/dut1") in events
    assert ("save_image", "C:/Temp/Transient/dut1/0Vin transient 85to148mV 1kHz.png") in events


def test_execute_raises_when_capture_is_not_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), repeat(300), events)
    scope.check_errors.return_value = [(200, "Save failed")]
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events))
    with pytest.raises(ValueError, match="could not save the capture"):
        procedure.execute()


def test_execute_logs_every_input(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), repeat(300), events)
    procedure = make_running_procedure(monkeypatch, scope, make_afg(events))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        procedure.execute()
    assert "Starting transient measurement over 2 inputs." in caplog.messages
    assert "Input 1 of 2: 85 to 148 mV at 1kHz." in caplog.messages
    assert (
        "AFG pulse set to 84 to 149 mV at 100kHz, 50 % duty cycle, edges 2.58 us and "
        "2.38 us, output on." in caplog.messages
    )


@pytest.mark.parametrize(
    ("maximum", "mean", "percent"),
    [(5.5, 5.0, 10.0), (-4.5, -5.0, -10.0), (5.0, 5.0, 0.0), (4.9, 5.0, -2.0)],
)
def test_overshoot_percent(maximum: float, mean: float, percent: float) -> None:
    assert procedure_transient.overshoot_percent(maximum, mean) == pytest.approx(percent)


@pytest.mark.parametrize(
    ("minimum", "mean", "percent"),
    [(4.0, 5.0, -20.0), (-6.0, -5.0, 20.0), (5.0, 5.0, 0.0), (5.1, 5.0, 2.0)],
)
def test_undershoot_percent(minimum: float, mean: float, percent: float) -> None:
    assert procedure_transient.undershoot_percent(minimum, mean) == pytest.approx(percent)


def test_overshoot_and_undershoot_of_a_zero_mean_are_nan() -> None:
    assert math.isnan(procedure_transient.overshoot_percent(1.0, 0.0))
    assert math.isnan(procedure_transient.undershoot_percent(-1.0, 0.0))


@pytest.mark.parametrize(("vin", "text"), [(12, "12Vin"), (3.3, "3.3Vin"), (0, "0Vin")])
def test_capture_file_name(vin: float, text: str) -> None:
    procedure = TransientProcedure(vin=vin, name="transient")
    procedure.refresh_parameters()
    current = Input(500, 97, -17, 0.4, 0.4, 1, 50, False)
    assert procedure.capture_file_name(current) == f"{text} transient -17to97mV 500kHz.png"


def test_capture_directory_is_captures_in_the_results_directory() -> None:
    procedure = TransientProcedure()
    assert procedure.results_directory == Path("results")
    procedure.results_directory = Path("C:/runs/Results_2026_11_05_10-00-00")
    assert procedure.capture_directory == Path("C:/runs/Results_2026_11_05_10-00-00/captures")


def test_default_pause_handler_prompts_on_the_console(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt))
    procedure = TransientProcedure()
    monkeypatch.setattr(procedure, "should_stop", lambda: False)
    assert procedure.pause("Adjust the scope.") is True
    assert prompts == ["Adjust the scope. Press Enter to continue. "]


def test_shutdown_turns_the_afg_output_off_and_closes_both_connections() -> None:
    events: list[tuple[Any, ...]] = []
    procedure = TransientProcedure()
    scope = MagicMock()
    afg = make_afg(events)
    procedure.scope = scope
    procedure.afg = afg
    procedure.shutdown()
    assert events == [("afg", "output_enabled", False)]
    afg.adapter.close.assert_called_once_with()
    scope.adapter.close.assert_called_once_with()


def test_shutdown_before_startup_does_not_raise() -> None:
    TransientProcedure().shutdown()


def test_shutdown_closes_the_scope_when_the_afg_did_not_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(list(DEFAULT_NAMES), [], events)
    monkeypatch.setattr(procedure_transient, "TektronixMSO58", MagicMock(return_value=scope))
    monkeypatch.setattr(
        procedure_transient,
        "TektronixAFG31051",
        MagicMock(side_effect=ConnectionError("AFG not found")),
    )
    procedure = TransientProcedure()
    procedure.refresh_parameters()
    with pytest.raises(ConnectionError):
        procedure.startup()
    procedure.shutdown()
    scope.adapter.close.assert_called_once_with()
