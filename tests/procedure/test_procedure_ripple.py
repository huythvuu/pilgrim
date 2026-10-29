"""Tests for the ripple procedure that need no instruments."""

import logging
import math
import re
import threading
from collections.abc import Iterable, Mapping
from itertools import chain, repeat
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock

import pytest

from pilgrim.procedure import RippleProcedure, procedure_ripple

DEFAULT_DIRECTORIES = ["C:/Temp", "C:/Temp/Ripple"]
LOGGER = "pilgrim.procedure.procedure_ripple"
#: The measurements the scope lists by default: the ones of CH1 that execute records.
DEFAULT_NAMES = ["MEAS1", "MEAS2", "MEAS3", "MEAS4"]
#: The columns read from the scope measurements.
RESULT_COLUMNS = ["Mean Vmean (V)", "Max Vmax (V)", "Min Vmin (V)", "Mean Vpk2pk (V)"]


def channel_kinds(source: str) -> dict[int, tuple[str, str]]:
    """Return the ``(type, source)`` of measurements 1 to 4 as the MEAN, MAXIMUM, MINIMUM and
    PK2PK of ``source``, the ones execute records.

    :param source: The channel, e.g. ``"CH1"``.
    """
    return {
        1: ("MEAN", source),
        2: ("MAXIMUM", source),
        3: ("MINIMUM", source),
        4: ("PK2PK", source),
    }


@pytest.fixture(autouse=True)
def in_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in its own directory, where the default local capture directory lands."""
    monkeypatch.chdir(tmp_path)


def recorded_setting(events: list[tuple[Any, ...]], *label: object) -> PropertyMock:
    """Return a property that appends ``(*label, value)`` to ``events`` whenever it is set.

    :param events: Receives the settings.
    :param label: What the setting is, e.g. ``"bandwidth", 1``.
    """

    def record(*value: object) -> None:
        if value:  # Reads pass no value and are not recorded.
            events.append((*label, *value))

    return PropertyMock(side_effect=record)


def make_scope(
    names: list[str],
    populations: Iterable[int],
    events: list[tuple[Any, ...]],
    directories: Iterable[str] = DEFAULT_DIRECTORIES,
    kinds: Mapping[int, tuple[str, str]] | None = None,
) -> MagicMock:
    """Return a mock scope listing ``names``, whose measurements read ``populations`` in turn.

    Measurement ``n`` has ``n`` as its id, reads a mean of ``n / 2``, a maximum of ``n`` and a
    minimum of ``-n``, and has no population limit.

    :param names: The measurement names the scope lists, e.g. ``["MEAS1"]``.
    :param populations: The populations read: every ``read_populations`` takes one per number
        asked for, in the order asked.
    :param events: Receives every clear, every acquisition start or stop as its value, every
        horizontal scale set, every bandwidth set with its channel number, every measurement
        added with its number, every results table added with its name, every cursor pair set
        with its channel number and its levels, every cursor hiding, and every directory made and
        image saved with its path.
    :param directories: The directories that exist on the scope; making one adds it.
    :param kinds: The ``(type, source)`` of measurements by number, by default
        :func:`channel_kinds` of CH1; a number not listed is a PK2PK of CH1.
    """
    scope = MagicMock()
    scope.measurement_names = names
    scope.measurements = {}
    scope.channels = {}
    for number in range(1, 9):
        channel = MagicMock()
        channel.id = number
        type(channel).bandwidth = recorded_setting(events, "bandwidth", number)
        scope.channels[number] = channel
    type(scope).horizontal_scale = recorded_setting(events, "horizontal_scale")
    scope.cursor.set_levels.side_effect = lambda channel, low, high: events.append(
        ("set_levels", channel.id, low, high)
    )
    type(scope.cursor).enabled = recorded_setting(events, "cursor_enabled")

    existing = set(directories)

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
    scope.check_errors.return_value = []
    counts = iter(populations)
    scope.read_populations.side_effect = lambda numbers: {n: next(counts) for n in numbers}

    listed_kinds = channel_kinds("CH1") if kinds is None else kinds

    def add_child(cls: type, number: int, **kwargs: object) -> MagicMock:
        measurement = MagicMock()
        measurement.id = number
        measurement.type, measurement.source = listed_kinds.get(number, ("PK2PK", "CH1"))
        measurement.mean = number / 2
        measurement.maximum = float(number)
        measurement.minimum = float(-number)
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
    scope.add_results_table.side_effect = lambda name: events.append(("add_results_table", name))
    scope.clear_acquisitions.side_effect = lambda: events.append(("clear",))
    type(scope).acquisition_enabled = PropertyMock(
        side_effect=lambda *value: events.append(("acquisition", *value))
    )
    return scope


def make_running_procedure(
    monkeypatch: pytest.MonkeyPatch, scope: MagicMock, **parameters: object
) -> RippleProcedure:
    """Return a procedure connected to ``scope``, set up as a worker would run it.

    Every pause resumes at once.

    :param monkeypatch: Replaces the polling sleep and the worker's hooks.
    :param scope: The mock scope.
    :param parameters: Parameter values by name.
    """
    monkeypatch.setattr(procedure_ripple, "sleep", lambda seconds: None)
    procedure = RippleProcedure(**parameters)
    procedure.refresh_parameters()
    procedure.scope = scope
    procedure.pause_handler = lambda prompt: procedure.resume()
    monkeypatch.setattr(procedure, "should_stop", lambda: False)
    monkeypatch.setattr(procedure, "emit", MagicMock())
    return procedure


def test_parameters_have_defaults() -> None:
    procedure = RippleProcedure()
    assert procedure.name == "ripple"
    assert procedure.scope_resource == ""
    assert procedure.use_cursor is True


def test_startup_connects_to_scope_resource(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope([], [], events)
    scope.id = "TEKTRONIX,MSO58,C031408,CF:91.1CT FV:1.34.8"
    type(scope).acquisition_enabled = PropertyMock(
        side_effect=lambda *value: events.append(("acquisition", *value)) or True
    )
    scope_class = MagicMock(return_value=scope)
    monkeypatch.setattr(procedure_ripple, "TektronixMSO58", scope_class)
    procedure = RippleProcedure(
        scope_resource="TCPIP::scope::INSTR", channel=3, target=5, cursor_lo=4, cursor_hi=5
    )
    procedure.refresh_parameters()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        procedure.startup()
    scope_class.assert_called_once_with("TCPIP::scope::INSTR", timeout=10_000)
    connected = "Connected to TEKTRONIX,MSO58,C031408,CF:91.1CT FV:1.34.8, found acquiring."
    assert connected in caplog.messages
    # The measurements, the table and the cursors are added once acquisition has stopped and
    # before the statistics clear.
    assert events == [
        ("acquisition",),
        ("acquisition", False),
        ("add_measurement", 1),
        ("add_measurement", 2),
        ("add_measurement", 3),
        ("add_measurement", 4),
        ("add_results_table", "TABLE1"),
        ("set_levels", 3, 4.8, 5.25),
        ("clear",),
    ]
    assert "Added results table TABLE1 to the scope." in caplog.messages
    assert "Cursors shown on CH3 at 4.8 V (target 5.0 V - 4.0 %) and 5.25 V (target + 5.0 %)." in (
        caplog.messages
    )
    added = [(m.id, m.type, m.source) for m in scope.measurements.values()]
    assert added == [
        (1, "MAXIMUM", "CH3"),
        (2, "MINIMUM", "CH3"),
        (3, "MEAN", "CH3"),
        (4, "PK2PK", "CH3"),
    ]
    procedure.shutdown()
    scope.adapter.close.assert_called_once_with()


def test_startup_hides_the_cursors_when_they_are_not_used(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope([], [], events, kinds=channel_kinds("CH3"))
    procedure = make_started_procedure(
        monkeypatch, scope, channel=3, target=5, cursor_lo=4, cursor_hi=5, use_cursor=False
    )
    with caplog.at_level(logging.INFO, logger=LOGGER):
        procedure.startup()
    scope.cursor.set_levels.assert_not_called()
    assert ("cursor_enabled", False) in events
    assert "Cursors hidden." in caplog.messages


def make_started_procedure(
    monkeypatch: pytest.MonkeyPatch, scope: MagicMock, **parameters: object
) -> RippleProcedure:
    """Return a procedure whose startup connects to ``scope``.

    :param monkeypatch: Replaces the scope class.
    :param scope: The mock scope.
    :param parameters: Parameter values by name.
    """
    monkeypatch.setattr(procedure_ripple, "TektronixMSO58", MagicMock(return_value=scope))
    procedure = RippleProcedure(**parameters)
    procedure.refresh_parameters()
    return procedure


def test_startup_keeps_an_existing_results_table(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope([], [], events)
    scope.add_results_table.side_effect = ValueError(
        "Tektronix MSO58 rejected adding results table TABLE1: 100, Command error; "
        "TABLE1 already exists"
    )
    procedure = make_started_procedure(monkeypatch, scope)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        procedure.startup()
    assert events[-1] == ("clear",)
    assert "The scope shows results table TABLE1 already." in caplog.messages


@pytest.mark.parametrize(
    "details",
    [
        "221, Settings conflict; Table TABLE1",
        "2100, Command error; Table TABLE1",
    ],
)
def test_startup_raises_other_results_table_refusals(
    monkeypatch: pytest.MonkeyPatch, details: str
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope([], [], events)
    message = f"Tektronix MSO58 rejected adding results table TABLE1: {details}"
    scope.add_results_table.side_effect = ValueError(message)
    procedure = make_started_procedure(monkeypatch, scope)
    with pytest.raises(ValueError, match=re.escape(message)):
        procedure.startup()
    assert ("clear",) not in events


def test_show_cursors_puts_the_cursors_on_the_channel_about_the_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope([], [], events)
    procedure = make_running_procedure(
        monkeypatch, scope, channel=2, target=-5, cursor_lo=4, cursor_hi=5
    )
    # The low cursor lies below a negative target and the high one above it.
    assert procedure.show_cursors() == (-5.2, -4.75)
    assert events == [("set_levels", 2, -5.2, -4.75)]


def test_show_cursors_of_the_defaults_meet_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope([], [], events)
    procedure = make_running_procedure(monkeypatch, scope)
    assert procedure.show_cursors() == (0, 0)
    assert events == [("set_levels", 1, 0, 0)]


def test_startup_raises_when_a_cursor_level_is_off_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope([], [], events)
    message = "Level 5.25 V is outside the screen range of CH1, -5.0 V to 5.0 V"
    scope.cursor.set_levels.side_effect = ValueError(message)
    procedure = make_started_procedure(monkeypatch, scope, target=5, cursor_hi=5)
    with pytest.raises(ValueError, match=re.escape(message)):
        procedure.startup()
    assert ("clear",) not in events


@pytest.mark.parametrize("percent", [-1, -0.5])
def test_negative_cursor_percentages_are_rejected(percent: float) -> None:
    with pytest.raises(ValueError, match="minimum"):
        RippleProcedure(cursor_lo=percent).refresh_parameters()
    with pytest.raises(ValueError, match="minimum"):
        RippleProcedure(cursor_hi=percent).refresh_parameters()


@pytest.mark.parametrize(
    ("target", "below", "above", "low", "high"),
    [
        (5, 4, 5, 4.8, 5.25),
        (-5, 4, 5, -5.2, -4.75),
        (1.8, 5, 5, 1.71, 1.89),
        (3.3, 0, 0, 3.3, 3.3),
        (0, 10, 10, 0, 0),
    ],
)
def test_cursor_levels(target: float, below: float, above: float, low: float, high: float) -> None:
    actual_low, actual_high = procedure_ripple.cursor_levels(target, below, above)
    assert actual_low == pytest.approx(low)
    assert actual_high == pytest.approx(high)


def test_add_missing_measurements_keeps_the_existing_ones(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[tuple[Any, ...]] = []
    kinds = {1: ("PK2PK", "CH2"), 3: ("MEAN", "CH1"), 5: ("MAXIMUM", "CH1")}
    names = ["MEAS1", "MEAS3", "MEAS5"]
    scope = make_scope(names, [], events, kinds=kinds)
    procedure = make_running_procedure(monkeypatch, scope, channel=1)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        added = procedure.add_missing_measurements()
    assert events == [("add_measurement", 6), ("add_measurement", 7)]
    assert [(m.id, m.type, m.source) for m in added] == [
        (6, "MINIMUM", "CH1"),
        (7, "PK2PK", "CH1"),
    ]
    assert names == ["MEAS1", "MEAS3", "MEAS5", "MEAS6", "MEAS7"]
    assert caplog.messages == [
        "Added scope measurement MEAS6: MINIMUM of CH1.",
        "Added scope measurement MEAS7: PK2PK of CH1.",
    ]


def test_add_missing_measurements_adds_none_when_the_channel_has_them(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[tuple[Any, ...]] = []
    kinds = {1: ("MEAN", "CH2"), 2: ("PK2PK", "CH2"), 3: ("MINIMUM", "CH2"), 4: ("MAXIMUM", "CH2")}
    scope = make_scope(["MEAS1", "MEAS2", "MEAS3", "MEAS4"], [], events, kinds=kinds)
    procedure = make_running_procedure(monkeypatch, scope, channel=2)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert procedure.add_missing_measurements() == []
    assert events == []
    assert caplog.messages == [
        "The scope has the MAXIMUM, MINIMUM, MEAN, PK2PK measurements of CH2."
    ]


def test_add_missing_measurements_ignores_other_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    kinds = {1: ("MEAN", "CH2"), 2: ("PK2PK", "CH2"), 3: ("MINIMUM", "CH2"), 4: ("MAXIMUM", "CH2")}
    scope = make_scope(["MEAS1", "MEAS2", "MEAS3", "MEAS4"], [], events, kinds=kinds)
    procedure = make_running_procedure(monkeypatch, scope, channel=1)
    added = procedure.add_missing_measurements()
    assert [(m.id, m.type, m.source) for m in added] == [
        (5, "MAXIMUM", "CH1"),
        (6, "MINIMUM", "CH1"),
        (7, "MEAN", "CH1"),
        (8, "PK2PK", "CH1"),
    ]


def test_combinations_vary_bandwidth_fastest() -> None:
    procedure = RippleProcedure(loads=[0, 50], horizontal_divs=[1e-6, 1e-3], bandwidths=[20e6, 1e9])
    procedure.refresh_parameters()
    assert procedure.combinations() == [
        (0, 1e-6, 20e6),
        (0, 1e-6, 1e9),
        (0, 1e-3, 20e6),
        (0, 1e-3, 1e9),
        (50, 1e-6, 20e6),
        (50, 1e-6, 1e9),
        (50, 1e-3, 20e6),
        (50, 1e-3, 1e9),
    ]


def test_combinations_of_defaults() -> None:
    combinations = RippleProcedure().combinations()
    assert len(combinations) == 3 * 2 * 3
    assert combinations[:3] == [(0, 1e-6, 20e6), (0, 1e-6, 250e6), (0, 1e-6, 1e9)]


def test_combinations_are_empty_without_loads() -> None:
    procedure = RippleProcedure(loads=[])
    procedure.refresh_parameters()
    assert procedure.combinations() == []


TWO_COMBINATIONS = {"loads": [0], "horizontal_divs": [1e-6], "bandwidths": [20e6, 1e9]}


def test_execute_acquires_until_population_at_every_combination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    # Each combination polls until full, then reads the populations once more after the stop;
    # every read takes the four measurements.
    polls = [(0, 0, 0, 0), (299, 300, 300, 300), (300, 300, 300, 300), (310, 311, 312, 313)]
    polls += [(100, 100, 100, 100), (300, 300, 300, 300), (305, 306, 307, 308)]
    populations = iter(chain.from_iterable(polls))
    scope = make_scope(DEFAULT_NAMES, populations, events)
    procedure = make_running_procedure(monkeypatch, scope, vin=12, pop_min=300, **TWO_COMBINATIONS)
    procedure.execute()
    assert events == [
        ("horizontal_scale", 1e-6),
        ("bandwidth", 1, 20e6),
        ("clear",),
        ("acquisition", True),
        ("acquisition", False),
        ("save_image", "C:/Temp/Ripple/12Vin ripple 0Load 1us 20MHz.png"),
        ("horizontal_scale", 1e-6),
        ("bandwidth", 1, 1e9),
        ("clear",),
        ("acquisition", True),
        ("acquisition", False),
        ("save_image", "C:/Temp/Ripple/12Vin ripple 0Load 1us 1GHz.png"),
    ]
    assert next(populations, None) is None
    emit: Any = procedure.emit
    row = {"Vin (V)": 12, "Load": 0, "Horizontal Div (s)": 1e-6}
    # MEAS1 to MEAS4 give a mean of 0.5 V, a maximum of 2 V, a minimum of -3 V and a mean
    # peak-to-peak of 2 V, so the maximum lies 300 % and the minimum 700 % of the mean away.
    values = {
        "Mean Vmean (V)": 0.5,
        "Max Vmax (V)": 2.0,
        "Min Vmin (V)": -3.0,
        "Mean Vpk2pk (V)": 2.0,
        "Overshoot (%)": 300.0,
        "Undershoot (%)": 700.0,
    }
    assert [call.args for call in emit.call_args_list] == [
        ("results", {**row, "Bandwidth (Hz)": 20e6, **values}),
        ("progress", 50.0),
        ("results", {**row, "Bandwidth (Hz)": 1e9, **values}),
        ("progress", 100.0),
    ]


def test_results_are_rounded_to_five_decimal_places(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = make_scope(DEFAULT_NAMES, repeat(300), [])
    add_child = scope.add_child.side_effect
    # The measurements are attached at startup, so the readings are set as they are added.
    readings = {1: ("mean", 0.123456789), 2: ("maximum", 1.0000049)}
    readings |= {3: ("minimum", -2.9999951), 4: ("mean", 1.5e-7)}

    def add_child_with_readings(cls: type, number: int, **kwargs: object) -> MagicMock:
        measurement = add_child(cls, number, **kwargs)
        statistic, value = readings[number]
        setattr(measurement, statistic, value)
        return measurement

    scope.add_child.side_effect = add_child_with_readings
    procedure = make_running_procedure(monkeypatch, scope, vin=12, pop_min=300, **ONE_COMBINATION)
    procedure.execute()
    emit: Any = procedure.emit
    rows = [call.args[1] for call in emit.call_args_list if call.args[0] == "results"]
    # The percentages are derived from the values as read (710.00398... % and
    # 2529.99605... %), then rounded like them.
    assert rows == [
        {
            "Vin (V)": 12,
            "Load": 0,
            "Horizontal Div (s)": 1e-6,
            "Bandwidth (Hz)": 20e6,
            "Mean Vmean (V)": 0.12346,
            "Max Vmax (V)": 1.0,
            "Min Vmin (V)": -3.0,
            "Mean Vpk2pk (V)": 0.0,
            "Overshoot (%)": 710.00398,
            "Undershoot (%)": 2529.99605,
        }
    ]


def test_results_rows_hold_every_data_column(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = make_scope(DEFAULT_NAMES, repeat(300), [])
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300)
    procedure.execute()
    emit: Any = procedure.emit
    rows = [call.args[1] for call in emit.call_args_list if call.args[0] == "results"]
    assert len(rows) == len(procedure.combinations())
    assert all(list(row) == RippleProcedure.DATA_COLUMNS for row in rows)


def test_execute_records_first_measurement_of_each_type_of_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first MEAN, MAXIMUM, MINIMUM and PK2PK of CH2 are MEAS3, MEAS4, MEAS6 and MEAS8;
    # MEAS1 and MEAS2 are of other channels and MEAS5 and MEAS7 repeat a type.
    kinds = {
        1: ("MEAN", "CH1"),
        2: ("MAXIMUM", "CH3"),
        3: ("MEAN", "CH2"),
        4: ("MAXIMUM", "CH2"),
        5: ("MEAN", "CH2"),
        6: ("MINIMUM", "CH2"),
        7: ("MINIMUM", "CH2"),
        8: ("PK2PK", "CH2"),
    }
    names = [f"MEAS{number}" for number in kinds]
    scope = make_scope(names, repeat(300), [], kinds=kinds)
    procedure = make_running_procedure(
        monkeypatch, scope, channel=2, pop_min=300, **TWO_COMBINATIONS
    )
    procedure.execute()
    emit: Any = procedure.emit
    values = [
        [call.args[1][column] for column in RESULT_COLUMNS]
        for call in emit.call_args_list
        if call.args[0] == "results"
    ]
    assert values == [[1.5, 4.0, -6.0, 4.0], [1.5, 4.0, -6.0, 4.0]]


@pytest.mark.parametrize("missing", ["MEAN", "MAXIMUM", "MINIMUM", "PK2PK"])
def test_execute_without_a_measurement_of_the_channel_raises(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    events: list[tuple[Any, ...]] = []
    # The missing type is measured on another channel only.
    kinds = {
        number: (kind, "CH2" if kind == missing else "CH1")
        for number, (kind, _) in channel_kinds("CH1").items()
    }
    scope = make_scope(DEFAULT_NAMES, [], events, kinds=kinds)
    procedure = make_running_procedure(monkeypatch, scope, **TWO_COMBINATIONS)
    with pytest.raises(ValueError, match=f"no {missing} measurement of CH1"):
        procedure.execute()
    assert events == []


def test_execute_waits_for_every_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = make_scope([*DEFAULT_NAMES, "MEAS6"], repeat(300), [])
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    procedure.execute()
    assert set(scope.measurements) == {1, 2, 3, 4, 6}
    numbers = [1, 2, 3, 4, 6]
    assert all(call.args == (numbers,) for call in scope.read_populations.call_args_list)


def test_execute_sets_scale_and_bandwidth_of_the_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(DEFAULT_NAMES, repeat(300), events, kinds=channel_kinds("CH3"))
    procedure = make_running_procedure(
        monkeypatch,
        scope,
        channel=3,
        pop_min=300,
        loads=[0],
        horizontal_divs=[1e-6, 1e-3],
        bandwidths=[20e6, 1e9],
    )
    procedure.execute()
    settings = [event for event in events if event[0] in {"horizontal_scale", "bandwidth"}]
    assert settings == [
        ("horizontal_scale", 1e-6),
        ("bandwidth", 3, 20e6),
        ("horizontal_scale", 1e-6),
        ("bandwidth", 3, 1e9),
        ("horizontal_scale", 1e-3),
        ("bandwidth", 3, 20e6),
        ("horizontal_scale", 1e-3),
        ("bandwidth", 3, 1e9),
    ]


def test_execute_stops_acquisition_when_asked_to_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(DEFAULT_NAMES, [], events)
    procedure = make_running_procedure(monkeypatch, scope, **TWO_COMBINATIONS)
    monkeypatch.setattr(procedure, "should_stop", lambda: True)
    procedure.execute()
    assert events == [
        ("horizontal_scale", 1e-6),
        ("bandwidth", 1, 20e6),
        ("clear",),
        ("acquisition", True),
        ("acquisition", False),
    ]


def test_execute_pauses_before_every_load_change(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(DEFAULT_NAMES, repeat(300), events)
    procedure = make_running_procedure(
        monkeypatch,
        scope,
        pop_min=300,
        loads=[0, 50, 50, 0],
        horizontal_divs=[1e-6],
        bandwidths=[20e6, 1e9],
    )

    def record_and_resume(prompt: str) -> None:
        events.append(("pause", prompt))
        procedure.resume()

    procedure.pause_handler = record_and_resume
    procedure.execute()
    pauses = [event for event in events if event[0] in {"pause", "save_image"}]
    assert pauses == [
        ("pause", "Set the load to 0."),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 0Load 1us 20MHz.png"),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 0Load 1us 1GHz.png"),
        ("pause", "Set the load to 50."),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 50Load 1us 20MHz.png"),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 50Load 1us 1GHz.png"),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 50Load 1us 20MHz.png"),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 50Load 1us 1GHz.png"),
        ("pause", "Set the load to 0."),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 0Load 1us 20MHz.png"),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 0Load 1us 1GHz.png"),
    ]


def test_execute_stops_while_paused_when_asked_to(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(DEFAULT_NAMES, repeat(300), events)
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    monkeypatch.setattr(procedure_ripple, "POLL_INTERVAL", 0.001)
    stopped = False

    def stop_instead_of_resuming(prompt: str) -> None:
        nonlocal stopped
        stopped = True

    procedure.pause_handler = stop_instead_of_resuming
    monkeypatch.setattr(procedure, "should_stop", lambda: stopped)
    procedure.execute()
    assert events == []  # Nothing was set on the scope.
    emit: Any = procedure.emit
    emit.assert_not_called()


ONE_COMBINATION = {"loads": [0], "horizontal_divs": [1e-6], "bandwidths": [20e6]}


def request_pause_at_first_poll(procedure: RippleProcedure, scope: MagicMock) -> None:
    """Make the first read of the populations request a pause, as a GUI would meanwhile."""
    read = scope.read_populations.side_effect

    def read_and_request(numbers: list[int]) -> dict[int, int]:
        populations = read(numbers)
        if scope.read_populations.call_count == 1:
            procedure.request_pause()
        return populations

    scope.read_populations.side_effect = read_and_request


def record_pauses_and_resume(procedure: RippleProcedure, events: list[tuple[Any, ...]]) -> None:
    """Make the procedure record every pause in ``events`` and resume at once."""

    def record_and_resume(prompt: str) -> None:
        events.append(("pause", prompt))
        procedure.resume()

    procedure.pause_handler = record_and_resume


def test_request_pause_stops_acquisition_until_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    polls = [(100, 100, 100, 100), (300, 300, 300, 300), (305, 306, 307, 308)]
    scope = make_scope(DEFAULT_NAMES, chain.from_iterable(polls), events)
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **ONE_COMBINATION)
    record_pauses_and_resume(procedure, events)
    request_pause_at_first_poll(procedure, scope)
    procedure.execute()
    assert [event for event in events if event[0] in {"pause", "acquisition"}] == [
        ("pause", "Set the load to 0."),
        ("acquisition", True),
        ("acquisition", False),
        ("pause", "Paused as requested."),
        ("acquisition", True),
        ("acquisition", False),
    ]
    assert len([event for event in events if event[0] == "save_image"]) == 1


def test_request_pause_during_a_capture_pauses_before_the_next_combination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(DEFAULT_NAMES, repeat(300), events)
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    record_pauses_and_resume(procedure, events)

    def save_and_request(path: str) -> None:
        events.append(("save_image", path))
        procedure.request_pause()

    scope.save_image.side_effect = save_and_request
    procedure.execute()
    assert [event for event in events if event[0] in {"pause", "save_image", "bandwidth"}] == [
        ("pause", "Set the load to 0."),
        ("bandwidth", 1, 20e6),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 0Load 1us 20MHz.png"),
        ("pause", "Paused as requested."),
        ("bandwidth", 1, 1e9),
        ("save_image", "C:/Temp/Ripple/0Vin ripple 0Load 1us 1GHz.png"),
    ]


def test_a_load_change_satisfies_a_requested_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(DEFAULT_NAMES, repeat(300), events)
    procedure = make_running_procedure(
        monkeypatch, scope, pop_min=300, loads=[0, 50], horizontal_divs=[1e-6], bandwidths=[20e6]
    )
    record_pauses_and_resume(procedure, events)
    scope.save_image.side_effect = lambda path: procedure.request_pause()
    procedure.execute()
    assert [event for event in events if event[0] == "pause"] == [
        ("pause", "Set the load to 0."),
        ("pause", "Set the load to 50."),
    ]


def test_stop_while_paused_on_request_ends_the_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(DEFAULT_NAMES, repeat(100), events)
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **ONE_COMBINATION)
    monkeypatch.setattr(procedure_ripple, "POLL_INTERVAL", 0.001)
    stopped = False

    def resume_or_stop(prompt: str) -> None:
        nonlocal stopped
        if prompt == "Paused as requested.":
            stopped = True
        else:
            procedure.resume()

    procedure.pause_handler = resume_or_stop
    monkeypatch.setattr(procedure, "should_stop", lambda: stopped)
    request_pause_at_first_poll(procedure, scope)
    procedure.execute()
    assert events[-2:] == [("acquisition", False), ("acquisition", False)]
    assert not any(event[0] == "save_image" for event in events)


def test_pause_resumes_from_another_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    procedure = make_running_procedure(monkeypatch, make_scope([], [], []))
    prompts: list[str] = []

    def resume_soon(prompt: str) -> None:
        prompts.append(prompt)
        threading.Timer(0.01, procedure.resume).start()

    procedure.pause_handler = resume_soon
    assert procedure.pause("Set the load to 50.") is True
    assert prompts == ["Set the load to 50."]


def test_pause_returns_false_when_asked_to_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    procedure = make_running_procedure(monkeypatch, make_scope([], [], []))
    monkeypatch.setattr(procedure_ripple, "POLL_INTERVAL", 0.001)
    procedure.pause_handler = lambda prompt: None
    monkeypatch.setattr(procedure, "should_stop", lambda: True)
    assert procedure.pause("Set the load to 50.") is False


def test_resume_before_pause_is_not_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    procedure = make_running_procedure(monkeypatch, make_scope([], [], []))
    monkeypatch.setattr(procedure_ripple, "POLL_INTERVAL", 0.001)
    procedure.resume()
    procedure.pause_handler = lambda prompt: None
    monkeypatch.setattr(procedure, "should_stop", lambda: True)
    assert procedure.pause("Set the load to 50.") is False


def test_default_pause_handler_prompts_on_the_console(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt))
    procedure = RippleProcedure()
    monkeypatch.setattr(procedure, "should_stop", lambda: False)
    assert procedure.pause_handler == procedure.prompt_on_console
    assert procedure.pause("Set the load to 50.") is True
    assert prompts == ["Set the load to 50. Press Enter to continue. "]


def test_capture_directory_is_captures_in_the_results_directory() -> None:
    procedure = RippleProcedure()
    assert procedure.results_directory == Path("results")
    assert procedure.capture_directory == Path("results/captures")
    procedure.results_directory = Path("nested/results")
    assert procedure.capture_directory == Path("nested/results/captures")


@pytest.mark.parametrize("channel", [0, 9])
def test_channel_outside_the_scope_channels_is_rejected(channel: int) -> None:
    with pytest.raises(ValueError, match=r"minimum|maximum"):
        RippleProcedure(channel=channel).refresh_parameters()


def test_execute_without_measurements_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    procedure = make_running_procedure(monkeypatch, make_scope([], [], events))
    with pytest.raises(ValueError, match="no measurements"):
        procedure.execute()
    assert events == []


@pytest.mark.parametrize(
    ("directories", "made"),
    [
        (DEFAULT_DIRECTORIES, []),
        (["C:/Temp"], ["C:/Temp/Ripple"]),
        ([], ["C:/Temp", "C:/Temp/Ripple"]),
    ],
)
def test_execute_makes_missing_capture_directories(
    monkeypatch: pytest.MonkeyPatch, directories: list[str], made: list[str]
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(DEFAULT_NAMES, repeat(300), events, directories)
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    procedure.execute()
    # The directories are made once, before the first combination.
    assert events[: len(made) + 1] == [
        *[("make_directory", path) for path in made],
        ("horizontal_scale", 1e-6),
    ]
    assert sum(event[0] == "make_directory" for event in events) == len(made)


@pytest.mark.parametrize("results_directory", ["results", "nested/results"])
def test_execute_moves_captures_to_local_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, results_directory: str
) -> None:
    # The results directory does not exist yet; execute makes it with the captures directory.
    local_directory = tmp_path / results_directory / "captures"
    names = ["12Vin ripple 0Load 1us 20MHz.png", "12Vin ripple 0Load 1us 1GHz.png"]
    deleted: list[str] = []

    def delete_file(path: str) -> None:
        # The capture must already be written locally when the scope's copy goes.
        assert (local_directory / path.rsplit("/", 1)[1]).exists()
        deleted.append(path)

    scope = make_scope(DEFAULT_NAMES, repeat(300), [])
    scope.delete_file.side_effect = delete_file
    procedure = make_running_procedure(monkeypatch, scope, vin=12, pop_min=300, **TWO_COMBINATIONS)
    procedure.results_directory = tmp_path / results_directory
    procedure.execute()
    assert sorted(path.name for path in local_directory.iterdir()) == sorted(names)
    for name in names:
        scope_path = f"C:/Temp/Ripple/{name}"
        assert (local_directory / name).read_bytes() == f"image of {scope_path}".encode()
    assert deleted == [f"C:/Temp/Ripple/{name}" for name in names]


def test_capture_stays_on_scope_when_transfer_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = make_scope(DEFAULT_NAMES, repeat(300), [])
    scope.read_file.side_effect = ValueError("Tektronix MSO58 rejected reading file: 256")
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    with pytest.raises(ValueError, match="256"):
        procedure.execute()
    scope.delete_file.assert_not_called()


def test_execute_raises_when_capture_is_not_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = make_scope(DEFAULT_NAMES, repeat(300), [])
    scope.check_errors.return_value = [(200, "Unable to complete the save operation")]
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    with pytest.raises(ValueError, match="could not save"):
        procedure.execute()


def test_capture_directory_error_other_than_missing_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = make_scope(DEFAULT_NAMES, [], [])
    type(scope).working_directory = PropertyMock(side_effect=ValueError("257, Not a directory"))
    procedure = make_running_procedure(monkeypatch, scope)
    with pytest.raises(ValueError, match="257"):
        procedure.execute()
    scope.make_directory.assert_not_called()


def test_capture_file_name() -> None:
    procedure = RippleProcedure(vin=12, name="ripple")
    procedure.refresh_parameters()
    assert procedure.capture_file_name(50, 1e-3, 250e6) == "12Vin ripple 50Load 1ms 250MHz.png"


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        (1e-6, "s", "1us"),
        (2.5e-6, "s", "2.5us"),
        (1e-3, "s", "1ms"),
        (500e-9, "s", "500ns"),
        (1.0, "s", "1s"),
        (20e6, "Hz", "20MHz"),
        (250e6, "Hz", "250MHz"),
        (1e9, "Hz", "1GHz"),
        (1.5e3, "Hz", "1.5kHz"),
        (0.0, "s", "0ps"),
    ],
)
def test_format_with_si_prefix(value: float, unit: str, text: str) -> None:
    assert procedure_ripple.format_with_si_prefix(value, unit) == text


@pytest.mark.parametrize(
    ("value", "mean", "percent"),
    [
        (5.25, 5.0, 5.0),
        (4.8, 5.0, 4.0),
        (2.0, 0.5, 300.0),
        (-3.0, 0.5, 700.0),
        (5.0, 5.0, 0.0),
        # The mean keeps its sign.
        (-11.0, -12.0, -100 / 12),
    ],
)
def test_deviation_percent(value: float, mean: float, percent: float) -> None:
    assert procedure_ripple.deviation_percent(value, mean) == pytest.approx(percent)


def test_deviation_percent_of_a_zero_mean_is_nan() -> None:
    assert math.isnan(procedure_ripple.deviation_percent(1.0, 0.0))


def test_shutdown_before_startup_does_not_raise() -> None:
    RippleProcedure().shutdown()


ONE_COMBINATION = {"loads": [0], "horizontal_divs": [1e-6], "bandwidths": [20e6]}


def test_execute_logs_every_population_per_poll(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Every read takes the four measurements, in the order the scope lists them; the last
    # read follows the stop.
    polls = [(0, 100, 200, 250), (300, 300, 300, 300), (310, 320, 330, 340)]
    populations = iter(chain.from_iterable(polls))
    scope = make_scope(DEFAULT_NAMES, populations, [])
    scope.acquisition_count = 1234
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **ONE_COMBINATION)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        procedure.execute()
    assert next(populations, None) is None
    polls = [message for message in caplog.messages if message.startswith("Poll ")]
    assert [poll.split(", read in ")[0] for poll in polls] == [
        "Poll 1: MEAS1=0, MEAS2=100, MEAS3=200, MEAS4=250 (min 300)",
        "Poll 2: MEAS1=300, MEAS2=300, MEAS3=300, MEAS4=300 (min 300)",
    ]
    assert all(re.fullmatch(r".*, read in \d+\.\d\d s\.", poll) for poll in polls)
    timed = [re.sub(r"\d+\.\d s", "N s", message) for message in caplog.messages]
    filled = "Populations filled in N s over 2 polls: MEAS1=300, MEAS2=300, MEAS3=300, MEAS4=300."
    assert filled in timed
    stopped = "Acquisition stopped at MEAS1=310, MEAS2=320, MEAS3=330, MEAS4=340."
    assert stopped in caplog.messages
    assert (
        "MEAN mean 0.5 V from 310 values, MAXIMUM maximum 2.0 V from 320 values, "
        "MINIMUM minimum -3.0 V from 330 values, PK2PK mean 2.0 V from 340 values, "
        "over 1234 acquisitions."
    ) in caplog.messages
    assert "Overshoot 300.0 %, undershoot 700.0 %." in caplog.messages


def test_execute_warns_when_short_populations_stall(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The measurements but MEAS2 are filled and keep growing, while MEAS2 is stuck at 0 until
    # the last poll.
    polls = [(300, 0, 300, 300), (400, 0, 400, 400), (500, 0, 500, 500)]
    polls += [(600, 300, 600, 600), (610, 305, 610, 610)]
    populations = iter(chain.from_iterable(polls))
    scope = make_scope(DEFAULT_NAMES, populations, [])
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **ONE_COMBINATION)
    clock = 0.0

    def poll_takes_six_seconds(_seconds: float) -> None:
        nonlocal clock
        clock += 6.0

    monkeypatch.setattr(procedure_ripple, "perf_counter", lambda: clock)
    monkeypatch.setattr(procedure_ripple, "sleep", poll_takes_six_seconds)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        procedure.execute()
    assert next(populations, None) is None
    assert caplog.messages == [
        "Populations short of 300 unchanged for 12 s: MEAS2=0; acquisition continues until "
        "they fill."
    ]


def test_execute_logs_the_measurements_found(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    kinds = {
        1: ("MAXIMUM", "CH1"),
        2: ("FREQUENCY", "CH2"),
        3: ("MEAN", "CH1"),
        4: ("MINIMUM", "CH1"),
        5: ("PK2PK", "CH1"),
    }
    names = [f"MEAS{number}" for number in kinds]
    scope = make_scope(names, repeat(300), [], kinds=kinds)
    add_child = scope.add_child.side_effect

    def add_child_with_limit(cls: type, number: int, **kwargs: object) -> MagicMock:
        measurement = add_child(cls, number, **kwargs)
        if measurement.type == "FREQUENCY":
            measurement.population_limit_enabled = True
            measurement.population_limit = 100
        elif measurement.type == "MEAN":
            measurement.uses_global_population_limit = True
        elif measurement.type == "MINIMUM":
            measurement.population_limit_enabled = True
            measurement.population_limit = 300
        return measurement

    scope.add_child.side_effect = add_child_with_limit
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **ONE_COMBINATION)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        procedure.execute()
    described = [message for message in caplog.messages if message.startswith("Scope measurement")]
    assert described == [
        "Scope measurement MEAS1: MAXIMUM of CH1, no population limit.",
        "Scope measurement MEAS2: FREQUENCY of CH2, population limit 100.",
        "Scope measurement MEAS3: MEAN of CH1, global population limit.",
        "Scope measurement MEAS4: MINIMUM of CH1, population limit 300.",
        "Scope measurement MEAS5: PK2PK of CH1, no population limit.",
    ]
    warnings = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == [
        "MEAS2 stops at 100 values, below the minimum of 300, so its population can never fill."
    ]


def test_execute_logs_capture_save_and_transfer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    scope = make_scope(DEFAULT_NAMES, repeat(300), [])
    procedure = make_running_procedure(monkeypatch, scope, vin=12, pop_min=300, **ONE_COMBINATION)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        procedure.execute()
    name = "12Vin ripple 0Load 1us 20MHz.png"
    scope_path = f"C:/Temp/Ripple/{name}"
    size = len(f"image of {scope_path}".encode())
    timed = [re.sub(r"\d+\.\d s", "N s", message) for message in caplog.messages]
    assert f"Capture saved to {scope_path} on the scope in N s." in timed
    local_path = procedure.capture_directory / name
    assert f"Capture moved to {local_path}: {size} bytes in N s." in timed
