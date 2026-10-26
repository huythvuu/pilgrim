"""Tests for the ripple procedure, run against a stand-in for the scope."""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import count, product

import pytest

from pilgrim.procedure import RippleProcedure, procedure_ripple
from pilgrim.procedure.procedure_ripple import capture_file_name, whole_number_with_prefix

# The values each measurement collects in one poll interval.
POPULATION_STEP = 100
POP_MIN = 300
VIN = 12.0
LOADS = [0.0, 0.5]
HORIZONTAL_DIVS = [1e-6, 1e-3]
BANDWIDTHS = [20e6, 250e6, 1e9]
# Every combination in the order the procedure captures them.
COMBINATIONS = list(product(LOADS, HORIZONTAL_DIVS, BANDWIDTHS))
CAPTURES_PER_LOAD = len(HORIZONTAL_DIVS) * len(BANDWIDTHS)
# The answers to the load prompts of one pass, each confirming that the load is set.
LOADS_SET = [""] * len(LOADS)
# The answers of a run that starts, captures one pass and ends.
ONE_PASS = ["", *LOADS_SET, "end"]
# The prompt to continue after the first pass, by its number: after the start and load prompts.
CONTINUE_PROMPT = 1 + len(LOADS)
# The directory on the scope the captures go to, as the default names it without its slash.
SCOPE_DIRECTORY = "E:/pilgrim"
# Polls of should_stop after which a run that would otherwise hang is stopped.
MAX_POLLS = 10_000
REQUESTED_BANDWIDTH = 30e6
APPLIED_BANDWIDTH = 20e6
# The PK2PK maximum the fake scope shows before its first capture, rising 1 mV per capture.
FIRST_MAXIMUM = 0.010
MAXIMUM_STEP = 0.001


class FakeMeasurement:
    """Stand-in for a scope measurement whose population grows while the procedure waits."""

    def __init__(
        self, scope: FakeScope, number: int, measurement_type: str = "MEAN", source: str = "CH2"
    ) -> None:
        self._scope = scope
        self.id = number
        self.type = measurement_type
        self.source = source
        self.uses_global_population_limit = False
        self.population_limit_enabled = False
        self.population_limit = 1000
        self.collected = 0

    @property
    def population(self) -> int:
        """Get the population, which stops at the population limit while it is enabled."""
        if self.population_limit_enabled:
            return min(self.collected, self.population_limit)
        return self.collected

    @property
    def maximum(self) -> float:
        """Measure the maximum, which differs from one capture to the next."""
        self._scope.events.append((f"MEAS{self.id}.maximum",))
        return FIRST_MAXIMUM + MAXIMUM_STEP * self._scope.saves()


class FakeChannel:
    """Stand-in for a scope channel that records the bandwidth limits set."""

    def __init__(self, scope: FakeScope, number: int) -> None:
        self._scope = scope
        self._number = number
        self._bandwidth = 1e9

    @property
    def bandwidth(self) -> float:
        """Control the bandwidth limit, rounded as the scope's ``round_bandwidth`` does."""
        return self._bandwidth

    @bandwidth.setter
    def bandwidth(self, value: float) -> None:
        self._scope.events.append((f"ch{self._number}.bandwidth", value))
        self._bandwidth = self._scope.round_bandwidth(value)


class FakeAdapter:
    """Stand-in for the scope's adapter that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        """Close the connection."""
        self.closed = True


class FakeScope:
    """Stand-in for the MSO58 that records the settings, restarts and captures in ``events``."""

    def __init__(self) -> None:
        self.resource = ""
        self.adapter = FakeAdapter()
        self.events: list[tuple[object, ...]] = []
        #: The measurements defined on the scope, as the user set it up.
        self.on_scope: dict[int, FakeMeasurement] = {
            1: FakeMeasurement(self, 1, "PK2PK"),
            2: FakeMeasurement(self, 2),
        }
        #: The measurement interfaces the procedure attached.
        self.measurements: dict[int, FakeMeasurement] = {}
        self.files: dict[str, bytes] = {}
        #: The population of every attached measurement at each capture.
        self.populations_at_save: list[list[int]] = []
        self.round_bandwidth: Callable[[float], float] = lambda value: value
        self.directory_error: ValueError | None = None
        self._horizontal_scale = 1e-4
        self._acquiring = True
        for number in range(1, 9):
            setattr(self, f"ch{number}", FakeChannel(self, number))

    @property
    def measurement_names(self) -> list[str]:
        """Get the names of the measurements on the scope."""
        return [f"MEAS{number}" for number in self.on_scope]

    @property
    def horizontal_scale(self) -> float:
        """Control the horizontal scale."""
        return self._horizontal_scale

    @horizontal_scale.setter
    def horizontal_scale(self, value: float) -> None:
        self.events.append(("horizontal_scale", value))
        self._horizontal_scale = value

    @property
    def acquisition_enabled(self) -> bool:
        """Control whether the scope acquires, which grows the populations."""
        return self._acquiring

    @acquisition_enabled.setter
    def acquisition_enabled(self, value: bool) -> None:
        self.events.append(("acquisition_enabled", value))
        self._acquiring = value

    @property
    def complete(self) -> str:
        """Get the reply to ``*OPC?``."""
        self.events.append(("complete",))
        return "1"

    def add_child(self, cls: type, number: int, collection: str, prefix: str) -> FakeMeasurement:
        """Attach the interface of a measurement on the scope; KeyError if there is none."""
        assert (cls, collection, prefix) == (
            procedure_ripple.TektronixMSO456Measurement,
            "measurements",
            "meas_",
        )
        self.measurements[number] = self.on_scope[number]
        return self.measurements[number]

    def remove_child(self, measurement: FakeMeasurement) -> None:
        """Remove the interface of a measurement."""
        del self.measurements[measurement.id]

    def reset_persistence(self) -> None:
        """Clear the persistence."""
        self.events.append(("reset_persistence",))

    def clear_acquisitions(self) -> None:
        """Restart the statistics of every measurement."""
        self.events.append(("clear_acquisitions",))
        for measurement in self.on_scope.values():
            measurement.collected = 0

    def acquire(self) -> None:
        """Collect values on every measurement, as acquisition does over one poll interval."""
        if not self._acquiring:
            return
        for measurement in self.on_scope.values():
            measurement.collected += POPULATION_STEP

    def make_directory(self, path: str) -> None:
        """Create a directory, or raise ``directory_error`` when set."""
        self.events.append(("make_directory", path))
        if self.directory_error is not None:
            raise self.directory_error

    def save_image(self, path: str) -> None:
        """Save a capture, noting the populations it shows."""
        self.events.append(("save_image", path))
        self.populations_at_save.append([m.population for m in self.measurements.values()])
        self.files[path] = f"image {path}".encode()

    def check_errors(self) -> list[tuple[int, str]]:
        """Return no errors."""
        return []

    def read_file(self, path: str) -> bytes:
        """Return the contents of a file."""
        return self.files[path]

    def delete_file(self, path: str) -> None:
        """Delete a file."""
        self.events.append(("delete_file", path))
        del self.files[path]

    def settings(self) -> list[tuple[object, ...]]:
        """Return the bandwidth and horizontal scale events, in order."""
        return [event for event in self.events if event[0] in {"ch2.bandwidth", "horizontal_scale"}]

    def saves(self) -> int:
        """Return the number of captures saved so far."""
        return sum(event[0] == "save_image" for event in self.events)


class Console:
    """Stand-in for ``input`` that answers prompts from a script and records them."""

    def __init__(self, scope: FakeScope, *replies: str) -> None:
        self.scope = scope
        self.replies = list(replies)
        self.prompts: list[str] = []
        #: The number of captures saved before each prompt.
        self.saves_at_prompt: list[int] = []
        #: The number of scope events before each prompt.
        self.events_at_prompt: list[int] = []
        #: Called with the prompt number before each reply, e.g. to change the scope setup.
        self.on_prompt: Callable[[int], None] = lambda _: None

    def __call__(self, prompt: str = "") -> str:
        self.on_prompt(len(self.prompts))
        self.prompts.append(prompt)
        self.saves_at_prompt.append(self.scope.saves())
        self.events_at_prompt.append(len(self.scope.events))
        return self.replies.pop(0)


@dataclass
class Run:
    """What a run of the procedure emitted."""

    rows: list[dict[str, object]] = field(default_factory=list)
    progress: list[float] = field(default_factory=list)


@pytest.fixture
def scope(monkeypatch: pytest.MonkeyPatch) -> FakeScope:
    """Connect the procedure to a fake scope that acquires during each poll interval.

    :param monkeypatch: The pytest monkeypatch fixture.
    :returns: The fake scope the procedure connects to.
    """
    fake = FakeScope()

    def connect(resource: str) -> FakeScope:
        fake.resource = resource
        return fake

    monkeypatch.setattr(procedure_ripple, "TektronixMSO58", connect)
    monkeypatch.setattr(procedure_ripple, "sleep", lambda _: fake.acquire())
    return fake


def make_procedure(**parameters: object) -> RippleProcedure:
    """Build a procedure whose parameter attributes hold the given values, as during a run.

    :param parameters: Parameter values by name, in any form the parameter converts.
    :returns: The procedure after ``refresh_parameters()``.
    """
    procedure = RippleProcedure(**parameters)
    procedure.refresh_parameters()
    return procedure


def make_run_procedure(tmp_path, **parameters: object) -> RippleProcedure:
    """Build a procedure for a run on channel 2, capturing every combination into ``tmp_path``.

    :param tmp_path: The directory the captures are saved to.
    :param parameters: Parameter values that replace the defaults of the run.
    :returns: The procedure.
    """
    defaults: dict[str, object] = {
        "scope_resource": "USB0::FAKE::INSTR",
        "channel": 2,
        "vin": VIN,
        "loads": LOADS,
        "horizontal_divs": HORIZONTAL_DIVS,
        "bandwidths": BANDWIDTHS,
        "pop_min": POP_MIN,
        "local_capture_directory": str(tmp_path),
    }
    return make_procedure(**(defaults | parameters))


def run(
    procedure: RippleProcedure,
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    stop: Callable[[], bool] | None = None,
) -> Run:
    """Run startup, execute and shutdown as a worker would, answering prompts from ``console``.

    :param procedure: The procedure.
    :param console: The scripted answers to the prompts.
    :param monkeypatch: The pytest monkeypatch fixture.
    :param stop: Replaces ``should_stop``; by default the run stops only if it would hang.
    :returns: What the run emitted.
    """
    emitted = Run()

    def emit(topic: str, record: object) -> None:
        if topic == "results":
            assert isinstance(record, dict)
            emitted.rows.append(record)
        elif topic == "progress":
            assert isinstance(record, float)
            emitted.progress.append(record)

    polls = count()
    monkeypatch.setattr("builtins.input", console)
    monkeypatch.setattr(procedure, "emit", emit)
    monkeypatch.setattr(procedure, "should_stop", stop or (lambda: next(polls) > MAX_POLLS))
    procedure.startup()
    try:
        procedure.execute()
    finally:
        procedure.shutdown()
    return emitted


def combination_of(row: dict[str, object]) -> tuple[object, object, object]:
    """Return the combination a results row was captured at.

    :param row: The results row.
    :returns: The ``(load, horizontal division, bandwidth)`` combination.
    """
    return row["Load"], row["Horizontal division (s)"], row["Bandwidth (Hz)"]


def test_defaults() -> None:
    procedure = RippleProcedure()
    assert procedure.name == "ripple"
    assert procedure.channel == 1
    assert procedure.vin == 0
    assert procedure.loads == [0]
    assert procedure.horizontal_divs == []
    assert procedure.bandwidths == []
    assert procedure.pop_min == POP_MIN
    assert procedure.scope_capture_directory == "E:/pilgrim/"
    assert procedure.local_capture_directory == "./results/captures"


def test_lists_accept_values_with_units() -> None:
    procedure = make_procedure(
        vin="12 V", horizontal_divs="[1 us, 1 ms]", bandwidths="[20 MHz, 250 MHz, 1 GHz]"
    )
    assert procedure.vin == VIN
    assert procedure.horizontal_divs == pytest.approx(HORIZONTAL_DIVS)
    assert procedure.bandwidths == pytest.approx(BANDWIDTHS)


@pytest.mark.parametrize(
    "parameters",
    [
        {"channel": 0},
        {"channel": 9},
        {"bandwidths": [0]},
        {"horizontal_divs": [-1e-6]},
        {"pop_min": 0},
    ],
)
def test_invalid_parameters_are_rejected(parameters: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"minimum|maximum|outside"):
        make_procedure(**parameters)


def test_combinations_change_bandwidth_first_then_horizontal_div_then_load(tmp_path) -> None:
    assert make_run_procedure(tmp_path).combinations() == [
        (0.0, 1e-6, 20e6),
        (0.0, 1e-6, 250e6),
        (0.0, 1e-6, 1e9),
        (0.0, 1e-3, 20e6),
        (0.0, 1e-3, 250e6),
        (0.0, 1e-3, 1e9),
        (0.5, 1e-6, 20e6),
        (0.5, 1e-6, 250e6),
        (0.5, 1e-6, 1e9),
        (0.5, 1e-3, 20e6),
        (0.5, 1e-3, 250e6),
        (0.5, 1e-3, 1e9),
    ]


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        (1e-6, "s", "1us"),
        (1e-3, "s", "1ms"),
        (400e-9, "s", "400ns"),
        (4.0000000000000003e-07, "s", "400ns"),  # 400 ns as Pint converts it.
        (20e-9, "s", "20ns"),
        (10.0, "s", "10s"),
        (2.5e-6, "s", "3us"),
        (2.4e-6, "s", "2us"),
        (0.9996e-3, "s", "1ms"),
        (9.999999999999999e-07, "s", "1us"),
        (20e6, "Hz", "20MHz"),
        (250e6, "Hz", "250MHz"),
        (1e9, "Hz", "1GHz"),
        (1.5e9, "Hz", "2GHz"),
        (500e3, "Hz", "500kHz"),
    ],
)
def test_whole_number_with_prefix(value: float, unit: str, text: str) -> None:
    assert whole_number_with_prefix(value, unit) == text


@pytest.mark.parametrize(
    ("arguments", "file_name"),
    [
        ((12.0, "ripple", 0.5, 1e-6, 20e6), "12Vin ripple 0.5Load 1us 20MHz.png"),
        ((12.0, "ripple", 0.0, 1e-3, 1e9), "12Vin ripple 0Load 1ms 1GHz.png"),
        ((3.3, "dut a", 10.0, 400e-9, 250e6), "3.3Vin dut a 10Load 400ns 250MHz.png"),
    ],
)
def test_capture_file_name(
    arguments: tuple[float, str, float, float, float], file_name: str
) -> None:
    assert capture_file_name(*arguments) == file_name


@pytest.mark.parametrize("parameters", [{"loads": []}, {"horizontal_divs": []}, {"bandwidths": []}])
def test_startup_without_combinations_raises_before_connecting(
    tmp_path, scope: FakeScope, parameters: dict[str, object]
) -> None:
    procedure = make_run_procedure(tmp_path, **parameters)
    with pytest.raises(ValueError, match="at least one value"):
        procedure.startup()
    assert scope.resource == ""


@pytest.mark.parametrize(
    "parameters",
    [
        {"horizontal_divs": [1e-6, 1.2e-6]},  # Both are named 1us.
        {"bandwidths": [20e6, 20.4e6]},  # Both are named 20MHz.
        {"loads": [1, 1]},
    ],
)
def test_startup_with_shared_file_names_raises_before_connecting(
    tmp_path, scope: FakeScope, parameters: dict[str, object]
) -> None:
    procedure = make_run_procedure(tmp_path, **parameters)
    with pytest.raises(ValueError, match="share capture file names"):
        procedure.startup()
    assert scope.resource == ""


def test_startup_only_connects(tmp_path, scope: FakeScope) -> None:
    local = tmp_path / "new" / "dir"
    procedure = make_run_procedure(tmp_path, local_capture_directory=str(local))
    procedure.startup()
    assert scope.resource == "USB0::FAKE::INSTR"
    assert scope.events == []
    assert local.is_dir()


def test_execute_waits_for_confirmation_before_touching_the_scope(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = Console(scope, *ONE_PASS)
    run(make_run_procedure(tmp_path), console, monkeypatch)
    assert "press Enter to start" in console.prompts[0]
    assert console.events_at_prompt[0] == 0


def test_user_sets_each_load_before_its_captures(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = Console(scope, *ONE_PASS)
    run(make_run_procedure(tmp_path), console, monkeypatch)
    assert console.prompts[1:3] == [
        "Set the load to 0, then press Enter. ",
        "Set the load to 0.5, then press Enter. ",
    ]
    assert console.saves_at_prompt[1:3] == [0, CAPTURES_PER_LOAD]
    # The scope is not touched between the confirmation of a load and its first setting.
    assert scope.events[console.events_at_prompt[2]] == ("horizontal_scale", HORIZONTAL_DIVS[0])


def test_captures_in_the_order_of_the_combinations(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted = run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    expected: list[tuple[object, ...]] = []
    for _ in LOADS:
        for div in HORIZONTAL_DIVS:
            expected.append(("horizontal_scale", div))
            expected.extend(("ch2.bandwidth", bandwidth) for bandwidth in BANDWIDTHS)
    assert scope.settings() == expected
    assert [combination_of(row) for row in emitted.rows] == COMBINATIONS
    total = len(COMBINATIONS)
    assert emitted.progress == pytest.approx([100 * n / total for n in range(1, total + 1)])


def test_saves_every_capture_under_its_combination_name(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted = run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    names = [capture_file_name(VIN, "ripple", *combination) for combination in COMBINATIONS]
    assert names[0] == "12Vin ripple 0Load 1us 20MHz.png"
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(names)
    for name, row in zip(names, emitted.rows, strict=True):
        assert row["Capture"] == str(tmp_path / name)
        assert row["Name"] == "ripple"
        assert (tmp_path / name).read_bytes() == f"image {SCOPE_DIRECTORY}/{name}".encode()
    assert scope.files == {}  # Every capture was deleted from the scope after reading it.


@pytest.mark.parametrize("directory", ["E:/pilgrim/", "E:/pilgrim"])
def test_scope_directory_is_used_with_or_without_trailing_slash(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch, directory: str
) -> None:
    procedure = make_run_procedure(tmp_path, scope_capture_directory=directory)
    run(procedure, Console(scope, *ONE_PASS), monkeypatch)
    assert scope.events[0] == ("make_directory", SCOPE_DIRECTORY)
    saved = [str(event[1]) for event in scope.events if event[0] == "save_image"]
    assert all(path.startswith(f"{SCOPE_DIRECTORY}/") for path in saved)
    assert not any("//" in path for path in saved)


def test_data_columns_put_the_pk2pk_maximum_before_the_capture() -> None:
    assert RippleProcedure.DATA_COLUMNS == [
        "Name",
        "Load",
        "Horizontal division (s)",
        "Bandwidth (Hz)",
        "Applied horizontal division (s)",
        "Applied bandwidth (Hz)",
        "PK2PK maximum (V)",
        "Capture",
    ]


def test_each_capture_waits_for_fresh_statistics_then_stops_acquisition(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    saves = [index for index, event in enumerate(scope.events) if event[0] == "save_image"]
    assert len(saves) == len(COMBINATIONS)
    for index in saves:
        path = scope.events[index][1]
        assert scope.events[index - 6 : index + 4] == [
            ("reset_persistence",),
            ("clear_acquisitions",),
            ("complete",),
            ("acquisition_enabled", False),
            ("complete",),
            ("MEAS1.maximum",),
            ("save_image", path),
            ("complete",),
            ("delete_file", path),
            ("acquisition_enabled", True),
        ]
    assert scope.populations_at_save == [[POP_MIN, POP_MIN]] * len(COMBINATIONS)
    assert scope.acquisition_enabled


def test_each_row_holds_the_pk2pk_maximum_of_its_capture(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted = run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    assert [row["PK2PK maximum (V)"] for row in emitted.rows] == pytest.approx(
        [FIRST_MAXIMUM + MAXIMUM_STEP * n for n in range(len(COMBINATIONS))]
    )


def test_first_pk2pk_of_the_channel_is_used(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope.on_scope[1].source = "CH1"
    scope.on_scope[3] = FakeMeasurement(scope, 3, "PK2PK")
    scope.on_scope[4] = FakeMeasurement(scope, 4, "PK2PK")
    run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    read = {event[0] for event in scope.events if str(event[0]).endswith(".maximum")}
    assert read == {"MEAS3.maximum"}


@pytest.mark.parametrize(
    "setup",
    ["no measurements", "PK2PK of another channel", "other type"],
)
def test_without_pk2pk_of_the_channel_raises_before_the_first_load(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch, setup: str
) -> None:
    if setup == "no measurements":
        scope.on_scope.clear()
    elif setup == "PK2PK of another channel":
        scope.on_scope[1].source = "CH1"
    else:
        scope.on_scope[1].type = "MAXIMUM"
    console = Console(scope, *ONE_PASS)
    with pytest.raises(ValueError, match="no PK2PK measurement of CH2"):
        run(make_run_procedure(tmp_path), console, monkeypatch)
    assert len(console.prompts) == 1  # Only the start prompt.
    assert scope.settings() == []
    assert scope.adapter.closed


def test_acquisition_restarts_when_a_capture_fails(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> list[tuple[int, str]]:
        raise ValueError("Unable to complete the save operation")

    monkeypatch.setattr(scope, "check_errors", refuse)
    with pytest.raises(ValueError, match="save operation"):
        run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    assert scope.events[-1] == ("acquisition_enabled", True)


def test_population_target_is_a_lower_measurement_limit(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope.on_scope[2].population_limit_enabled = True
    scope.on_scope[2].population_limit = 100
    run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    assert scope.populations_at_save == [[POP_MIN, 100]] * len(COMBINATIONS)


def test_measurement_on_global_limit_is_warned_about(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    scope.on_scope[1].uses_global_population_limit = True
    with caplog.at_level(logging.WARNING):
        run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    assert "MEAS1 uses the global population limit" in caplog.text


def test_continue_captures_again_under_the_same_name(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = ["", *LOADS_SET, "maybe", " Continue ", *LOADS_SET, "END"]
    console = Console(scope, *replies)
    emitted = run(make_run_procedure(tmp_path), console, monkeypatch)
    # The prompt to continue is asked again after "maybe", and after the second pass.
    ask = CONTINUE_PROMPT
    assert console.prompts[ask] == console.prompts[ask + 1] == console.prompts[-1]
    assert '"continue"' in console.prompts[ask]
    assert '"end"' in console.prompts[ask]
    assert console.prompts[ask + 2].startswith("Set the load to 0,")
    total = len(COMBINATIONS)
    assert console.saves_at_prompt[ask + 1] == total
    assert [row["Name"] for row in emitted.rows] == ["ripple"] * 2 * total
    # The second pass replaces the captures of the first.
    assert len(list(tmp_path.iterdir())) == total
    settings = scope.settings()
    assert settings[: len(settings) // 2] == settings[len(settings) // 2 :]


def test_measurements_follow_the_scope_between_passes(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = Console(scope, "", *LOADS_SET, "continue", *LOADS_SET, "end")

    def change_setup(prompt: int) -> None:
        if prompt == CONTINUE_PROMPT:  # The user reconfigures the scope before continuing.
            del scope.on_scope[2]
            scope.on_scope[3] = FakeMeasurement(scope, 3)

    console.on_prompt = change_setup
    run(make_run_procedure(tmp_path), console, monkeypatch)
    assert sorted(scope.measurements) == [1, 3]
    assert scope.populations_at_save[-1] == [POP_MIN, POP_MIN]


def test_stop_while_waiting_ends_the_run_without_capturing_or_prompting(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = Console(scope, "", "")
    emitted = run(make_run_procedure(tmp_path), console, monkeypatch, stop=lambda: True)
    assert emitted.rows == []
    assert len(console.prompts) == 1 + 1  # The start prompt and the first load prompt.
    assert list(tmp_path.iterdir()) == []


def test_rounded_settings_are_recorded_and_warned_about(
    tmp_path,
    scope: FakeScope,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scope.round_bandwidth = lambda _: APPLIED_BANDWIDTH
    procedure = make_run_procedure(
        tmp_path, loads=[0], horizontal_divs=[1e-6], bandwidths=[REQUESTED_BANDWIDTH]
    )
    with caplog.at_level(logging.WARNING):
        emitted = run(procedure, Console(scope, "", "", "end"), monkeypatch)
    assert emitted.rows[0]["Bandwidth (Hz)"] == REQUESTED_BANDWIDTH
    assert emitted.rows[0]["Applied bandwidth (Hz)"] == APPLIED_BANDWIDTH
    assert "Bandwidth of 30000000.0 Hz requested, the scope applied 20000000.0" in caplog.text
    assert (tmp_path / "12Vin ripple 0Load 1us 30MHz.png").exists()


def test_existing_scope_capture_directory_is_used(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope.directory_error = ValueError("rejected: 257, File name error")
    emitted = run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    assert len(emitted.rows) == len(COMBINATIONS)


def test_other_scope_capture_directory_errors_raise(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope.directory_error = ValueError("rejected: 256, File name not found")
    with pytest.raises(ValueError, match="256"):
        run(make_run_procedure(tmp_path), Console(scope, ""), monkeypatch)
    assert scope.adapter.closed


def test_shutdown_closes_the_connection(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(make_run_procedure(tmp_path), Console(scope, *ONE_PASS), monkeypatch)
    assert scope.adapter.closed


def test_shutdown_before_connecting_does_not_raise() -> None:
    RippleProcedure().shutdown()


def test_population_is_checked_to_be_an_int(
    tmp_path, scope: FakeScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeMeasurement, "population", property(lambda _: math.nan))
    with pytest.raises(TypeError, match="Population must be an int"):
        run(make_run_procedure(tmp_path), Console(scope, "", ""), monkeypatch)
