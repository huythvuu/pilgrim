"""Tests for the ripple procedure that need no instruments."""

from collections.abc import Iterable, Mapping
from itertools import repeat
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock

import pytest

from pilgrim.procedure import RippleProcedure, procedure_ripple

DEFAULT_DIRECTORIES = ["C:/Temp", "C:/Temp/RippleNoise"]


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

    Measurement ``n`` reads a maximum of ``n / 100``.

    :param names: The measurement names the scope lists, e.g. ``["MEAS1"]``.
    :param populations: The populations read, one per read; every measurement reads a list
        from its start, while an iterator is shared between them.
    :param events: Receives every clear, every acquisition start or stop as its value, every
        horizontal scale set, every bandwidth set with its channel number, and every directory
        made and image saved with its path.
    :param directories: The directories that exist on the scope; making one adds it.
    :param kinds: The ``(type, source)`` of measurements by number; the others are a PK2PK
        of CH1.
    """
    scope = MagicMock()
    scope.measurement_names = names
    scope.measurements = {}
    scope.channels = {}
    for number in range(1, 9):
        channel = MagicMock()
        type(channel).bandwidth = recorded_setting(events, "bandwidth", number)
        scope.channels[number] = channel
    type(scope).horizontal_scale = recorded_setting(events, "horizontal_scale")

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

    def add_child(cls: type, number: int, **kwargs: object) -> MagicMock:
        measurement = MagicMock()
        type(measurement).population = PropertyMock(side_effect=populations)
        measurement.type, measurement.source = (kinds or {}).get(number, ("PK2PK", "CH1"))
        measurement.maximum = number / 100
        scope.measurements[number] = measurement
        return measurement

    scope.add_child.side_effect = add_child
    scope.clear_acquisitions.side_effect = lambda: events.append(("clear",))
    type(scope).acquisition_enabled = PropertyMock(
        side_effect=lambda *value: events.append(("acquisition", *value))
    )
    return scope


def make_running_procedure(
    monkeypatch: pytest.MonkeyPatch, scope: MagicMock, **parameters: object
) -> RippleProcedure:
    """Return a procedure connected to ``scope``, set up as a worker would run it.

    :param monkeypatch: Replaces the polling sleep, the load prompt and the worker's hooks.
    :param scope: The mock scope.
    :param parameters: Parameter values by name.
    """
    monkeypatch.setattr(procedure_ripple, "sleep", lambda seconds: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    procedure = RippleProcedure(**parameters)
    procedure.refresh_parameters()
    procedure.scope = scope
    monkeypatch.setattr(procedure, "should_stop", lambda: False)
    monkeypatch.setattr(procedure, "emit", MagicMock())
    return procedure


def test_parameters_have_defaults() -> None:
    procedure = RippleProcedure()
    assert procedure.name == "ripple"
    assert procedure.scope_resource == ""


def test_startup_connects_to_scope_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    scope_class = MagicMock()
    monkeypatch.setattr(procedure_ripple, "TektronixMSO58", scope_class)
    procedure = RippleProcedure(scope_resource="TCPIP::scope::INSTR")
    procedure.refresh_parameters()
    procedure.startup()
    scope_class.assert_called_once_with("TCPIP::scope::INSTR")
    assert scope_class.return_value.acquisition_enabled is False
    scope_class.return_value.clear_acquisitions.assert_called_once_with()
    procedure.shutdown()
    scope_class.return_value.adapter.close.assert_called_once_with()


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
    populations = iter([0, 299, 300, 100, 300])
    scope = make_scope(["MEAS1"], populations, events)
    procedure = make_running_procedure(monkeypatch, scope, vin=12, pop_min=300, **TWO_COMBINATIONS)
    procedure.execute()
    assert events == [
        ("horizontal_scale", 1e-6),
        ("bandwidth", 1, 20e6),
        ("clear",),
        ("acquisition", True),
        ("acquisition", False),
        ("save_image", "C:/Temp/RippleNoise/12Vin ripple 0Load 1us 20MHz.png"),
        ("horizontal_scale", 1e-6),
        ("bandwidth", 1, 1e9),
        ("clear",),
        ("acquisition", True),
        ("acquisition", False),
        ("save_image", "C:/Temp/RippleNoise/12Vin ripple 0Load 1us 1GHz.png"),
    ]
    assert next(populations, None) is None
    emit: Any = procedure.emit
    row = {"Vin (V)": 12, "Load": 0, "Horizontal Div (s)": 1e-6}
    assert [call.args for call in emit.call_args_list] == [
        ("results", {**row, "Bandwidth (Hz)": 20e6, "Peak-to-Peak overall max (V)": 0.01}),
        ("progress", 50.0),
        ("results", {**row, "Bandwidth (Hz)": 1e9, "Peak-to-Peak overall max (V)": 0.01}),
        ("progress", 100.0),
    ]


def test_results_rows_hold_every_data_column(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = make_scope(["MEAS1"], repeat(300), [])
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300)
    procedure.execute()
    emit: Any = procedure.emit
    rows = [call.args[1] for call in emit.call_args_list if call.args[0] == "results"]
    assert len(rows) == len(procedure.combinations())
    assert all(list(row) == RippleProcedure.DATA_COLUMNS for row in rows)


def test_execute_records_first_peak_to_peak_of_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kinds = {1: ("MAXIMUM", "CH2"), 2: ("PK2PK", "CH1"), 3: ("PK2PK", "CH2"), 4: ("PK2PK", "CH2")}
    scope = make_scope(["MEAS1", "MEAS2", "MEAS3", "MEAS4"], [300] * 4, [], kinds=kinds)
    procedure = make_running_procedure(
        monkeypatch, scope, channel=2, pop_min=300, **TWO_COMBINATIONS
    )
    procedure.execute()
    emit: Any = procedure.emit
    maxima = [
        call.args[1]["Peak-to-Peak overall max (V)"]
        for call in emit.call_args_list
        if call.args[0] == "results"
    ]
    assert maxima == [0.03, 0.03]


def test_execute_without_peak_to_peak_of_the_channel_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(["MEAS1"], [], events, kinds={1: ("PK2PK", "CH2")})
    procedure = make_running_procedure(monkeypatch, scope, **TWO_COMBINATIONS)
    with pytest.raises(ValueError, match="no PK2PK measurement of CH1"):
        procedure.execute()
    assert events == []


def test_execute_waits_for_every_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = make_scope(["MEAS1", "MEAS3"], [300, 300, 300, 300], [])
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    procedure.execute()
    assert set(scope.measurements) == {1, 3}


def test_execute_sets_scale_and_bandwidth_of_the_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(["MEAS1"], [300] * 4, events, kinds={1: ("PK2PK", "CH3")})
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
    scope = make_scope(["MEAS1"], [], events)
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


def test_execute_prompts_before_every_load_change(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(["MEAS1"], repeat(300), events)
    procedure = make_running_procedure(
        monkeypatch,
        scope,
        pop_min=300,
        loads=[0, 50, 50, 0],
        horizontal_divs=[1e-6],
        bandwidths=[20e6, 1e9],
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": events.append(("input", prompt)))
    procedure.execute()
    loads = [event for event in events if event[0] in {"input", "save_image"}]
    assert loads == [
        ("input", "Set the load to 0, then press Enter to continue. "),
        ("save_image", "C:/Temp/RippleNoise/0Vin ripple 0Load 1us 20MHz.png"),
        ("save_image", "C:/Temp/RippleNoise/0Vin ripple 0Load 1us 1GHz.png"),
        ("input", "Set the load to 50, then press Enter to continue. "),
        ("save_image", "C:/Temp/RippleNoise/0Vin ripple 50Load 1us 20MHz.png"),
        ("save_image", "C:/Temp/RippleNoise/0Vin ripple 50Load 1us 1GHz.png"),
        ("save_image", "C:/Temp/RippleNoise/0Vin ripple 50Load 1us 20MHz.png"),
        ("save_image", "C:/Temp/RippleNoise/0Vin ripple 50Load 1us 1GHz.png"),
        ("input", "Set the load to 0, then press Enter to continue. "),
        ("save_image", "C:/Temp/RippleNoise/0Vin ripple 0Load 1us 20MHz.png"),
        ("save_image", "C:/Temp/RippleNoise/0Vin ripple 0Load 1us 1GHz.png"),
    ]


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
        (["C:/Temp"], ["C:/Temp/RippleNoise"]),
        ([], ["C:/Temp", "C:/Temp/RippleNoise"]),
    ],
)
def test_execute_makes_missing_capture_directories(
    monkeypatch: pytest.MonkeyPatch, directories: list[str], made: list[str]
) -> None:
    events: list[tuple[Any, ...]] = []
    scope = make_scope(["MEAS1"], [300, 300], events, directories)
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    procedure.execute()
    # The directories are made once, before the first combination.
    assert events[: len(made) + 1] == [
        *[("make_directory", path) for path in made],
        ("horizontal_scale", 1e-6),
    ]
    assert sum(event[0] == "make_directory" for event in events) == len(made)


@pytest.mark.parametrize("local_capture_dir", [None, "nested/captures"])
def test_execute_moves_captures_to_local_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, local_capture_dir: str | None
) -> None:
    local_directory = tmp_path / (local_capture_dir or "results/captures") / "captures"
    names = ["12Vin ripple 0Load 1us 20MHz.png", "12Vin ripple 0Load 1us 1GHz.png"]
    deleted: list[str] = []

    def delete_file(path: str) -> None:
        # The capture must already be written locally when the scope's copy goes.
        assert (local_directory / path.rsplit("/", 1)[1]).exists()
        deleted.append(path)

    scope = make_scope(["MEAS1"], [300, 300], [])
    scope.delete_file.side_effect = delete_file
    parameters: dict[str, object] = {"vin": 12, "pop_min": 300, **TWO_COMBINATIONS}
    if local_capture_dir is not None:
        parameters["local_capture_dir"] = local_capture_dir
    procedure = make_running_procedure(monkeypatch, scope, **parameters)
    procedure.execute()
    assert sorted(path.name for path in local_directory.iterdir()) == sorted(names)
    for name in names:
        scope_path = f"C:/Temp/RippleNoise/{name}"
        assert (local_directory / name).read_bytes() == f"image of {scope_path}".encode()
    assert deleted == [f"C:/Temp/RippleNoise/{name}" for name in names]


def test_capture_stays_on_scope_when_transfer_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = make_scope(["MEAS1"], [300], [])
    scope.read_file.side_effect = ValueError("Tektronix MSO58 rejected reading file: 256")
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    with pytest.raises(ValueError, match="256"):
        procedure.execute()
    scope.delete_file.assert_not_called()


def test_execute_raises_when_capture_is_not_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = make_scope(["MEAS1"], [300], [])
    scope.check_errors.return_value = [(200, "Unable to complete the save operation")]
    procedure = make_running_procedure(monkeypatch, scope, pop_min=300, **TWO_COMBINATIONS)
    with pytest.raises(ValueError, match="could not save"):
        procedure.execute()


def test_capture_directory_error_other_than_missing_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = make_scope(["MEAS1"], [], [])
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


def test_shutdown_before_startup_does_not_raise() -> None:
    RippleProcedure().shutdown()
