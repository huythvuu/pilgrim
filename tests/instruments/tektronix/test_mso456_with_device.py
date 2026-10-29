"""Device tests for the Tektronix MSO4/5/6 series driver.

Run with ``--device-address=<VISA resource name>`` of any 4, 5 or 6 Series MSO.
An MSO58 is driven through :class:`TektronixMSO58`, every other model through the base class.
The tests use values valid on every model and drive CH1 only, which should carry a repetitive
signal with edges, such as the 1 kHz pulse on the bench; the tests that need a trigger skip when
the edge trigger at the midpoint of CH1 does not fire. The vertical settings are never changed,
except the bandwidth limit of CH1 and, in the cursor tests, its scale, position, offset and
display state; they and the horizontal settings are set back after each test.
The setup is saved to a file in ``C:/pilgrim_test`` first and recalled when the module finishes,
which restores the measurements and the results table the tests delete and add; the file and the
directory are deleted again. The response header and verbosity states every connection sets are
not part of the setup and stay.
"""

import math
import re
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager

import pytest
from pymeasure.adapters import VISAAdapter
from pyvisa.resources import MessageBasedResource

from pilgrim.instruments import (
    TektronixMSO58,
    TektronixMSO456,
    TektronixMSO456Cursor,
    TektronixMSO456Measurement,
)

MODEL_PATTERN = re.compile(r"TEKTRONIX,(MSO[456]\d\w*|LPD64)")
# The models with eight analog channels, which the driver represents with their own class.
MODEL_CLASSES: dict[str, type[TektronixMSO456]] = {
    "MSO58": TektronixMSO58,
    "MSO58B": TektronixMSO58,
    "MSO58LP": TektronixMSO58,
}
COMMAND_ERRORS = range(100, 200)
TRIGGER_STATES = ("ARMED", "AUTO", "READY", "SAVE", "TRIGGER")
SCRATCH_DIRECTORY = "C:/pilgrim_test"
SETUP_FILE_NAME = "pilgrim_setup.set"
SETUP_FILE = f"{SCRATCH_DIRECTORY}/{SETUP_FILE_NAME}"
MISSING_DIRECTORY = "C:/pilgrim_missing_directory"
TABLE = "TABLE1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_TRAILER = b"IEND\xaeB`\x82"
# Saving and recalling the setup take about 0.6 s and 1.6 s on the bench, and a single sequence
# takes as long as the signal needs to trigger, so waiting for them with ``complete`` gets a
# longer timeout than the adapter's default of 2 s.
LONG_TIMEOUT = 20_000
# A trigger level far outside the screen, which the instrument clamps to the edge of the screen.
FAR_LEVEL = 1000.0
SEQUENCE_COUNT = 3
POPULATION_LIMIT = 5
# Enough values for the statistics to be checked.
FEW_VALUES = 3
# Enough acquisitions to tell a reset count from one that carried on.
MANY_ACQUISITIONS = 50
# The screen is 10 divisions wide, so a scale of ``cycles / (DIVISIONS * frequency)`` fits that
# many cycles on it.
DIVISIONS = 10
CYCLES_ON_SCREEN = 5
# A horizontal scale between two of the instrument's steps, which it rounds silently to one
# of them.
OFF_STEP_SCALE = 3.3e-6
STEP_BELOW, STEP_ABOVE = 2e-6, 5e-6
# Horizontal scales far outside the range of every model, which the instrument clamps.
HUGE_SCALE = 1e6
TINY_SCALE = 1e-15
# The lowest bandwidth limit, which every model offers (the manual's example uses it).
LOW_BANDWIDTH = 20e6
# A bandwidth between two limits the instrument offers, which it rounds silently to one of them.
OFF_STEP_BANDWIDTH = 30e6
# A bandwidth far above the maximum of every model, which the instrument clamps.
HUGE_BANDWIDTH = 1e12
# Vertical settings of CH1 for the cursor band tests, which show -2.5 V to 2.5 V on screen.
BAND_SCALE, BAND_POSITION, BAND_OFFSET = 0.5, 0.0, 0.0
# The target and tolerance of the cursor band tests, and the levels they put the cursors at.
BAND_TARGET, BAND_PERCENT = 1.8, 5.0
BAND_LEVELS = (1.71, 1.89)
# A tolerance whose band does not fit the screen at BAND_SCALE.
WIDE_PERCENT = 50.0
# The instrument reads levels back with a few significant digits, e.g. 1.710 or 249.000E-3.
LEVEL_TOLERANCE = 1e-3
# The measurement types the transient procedure needs.
TRANSIENT_TYPES = ["MEAN", "MINIMUM", "MAXIMUM", "TOP", "BASE", "PK2PK", "RISETIME", "FALLTIME"]
PERCENT_LEVELS = [
    f"reference_level_percent_{edge}_{level}"
    for edge in ("rise", "fall")
    for level in ("high", "mid", "low")
]
# Properties whose values the instrument reads back unchanged, with the values to try in order.
ROUND_TRIPS: list[tuple[str, list[str | float | bool]]] = [
    (
        "trigger_type",
        ["WIDTH", "TIMEOUT", "RUNT", "WINDOW", "LOGIC", "SETHOLD", "TRANSITION", "EDGE"],
    ),
    ("trigger_slope", ["FALL", "EITHER", "RISE"]),
    ("trigger_coupling", ["HFREJ", "LFREJ", "NOISEREJ", "DC"]),
    ("trigger_mode", ["NORMAL", "AUTO"]),
    ("trigger_holdoff_type", ["TIME", "RANDOM"]),
    ("trigger_holdoff_time", [0.0, 5e-9, 1.23456e-3, 10.0]),
    ("horizontal_mode", ["MANUAL", "AUTO"]),
    # Decade steps, which every model supports (the manual's examples use 20 ns and 20 µs).
    ("horizontal_scale", [20e-9, 2e-6, 1e-3, 1.0]),
    ("horizontal_position", [0.0, 10.0, 100.0, 50.0]),
    ("persistence", ["AUTO", "INFPERSIST", "VARPERSIST", "OFF"]),
    ("persistence_decay_time", [0.05, 2.5, 100.0]),
    ("reference_level_method", ["ABSOLUTE", "PERCENT"]),
    ("reference_level_mode", ["LATCH", "CONTINUOUS"]),
    ("reference_level_scope", ["PERSOURCE", "GLOBAL"]),
    ("reference_level_percent_preset", ["TWENTYEIGHTY", "CUSTOM", "TENNINETY"]),
    ("reference_level_absolute_rise_high", [0.4, 1.0]),
    ("reference_level_absolute_rise_mid", [0.25, 0.0]),
    ("reference_level_absolute_rise_low", [0.1, -1.5]),
    ("reference_level_absolute_fall_high", [0.45, 2.0]),
    ("reference_level_absolute_fall_mid", [-0.2, 0.0]),
    ("reference_level_absolute_fall_low", [-1.5, -1.25e-3]),
    ("cycle_statistics_enabled", [True, False]),
    ("image_color_mode", ["INVERTED", "NORMAL"]),
]


@contextmanager
def visa_timeout(mso: TektronixMSO456, milliseconds: int) -> Generator[None]:
    """Raise the adapter's timeout for the block, e.g. to wait longer with ``complete``.

    :param mso: The instrument, connected through a VISA adapter.
    :param milliseconds: The timeout to use in the block.
    """
    adapter = mso.adapter
    assert isinstance(adapter, VISAAdapter)
    connection = adapter.connection
    assert isinstance(connection, MessageBasedResource)
    original = connection.timeout
    connection.timeout = milliseconds
    try:
        yield
    finally:
        connection.timeout = original


def wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> bool:
    """Poll ``condition`` until it holds or ``timeout`` seconds have passed.

    :param condition: The condition to poll.
    :param timeout: The longest time to wait in seconds.
    :returns: Whether the condition held before the timeout.
    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def wait_for_setup_command(mso: TektronixMSO456, command: str) -> None:
    """Write a setup command, wait for it to finish and fail if the instrument reports an event.

    :param mso: The connected instrument.
    :param command: The command to write, e.g. ``SAVe:SETUp "C:/x.set"``.
    """
    mso.write(command)
    with visa_timeout(mso, LONG_TIMEOUT):
        assert mso.complete == "1"
    assert mso.check_errors() == [], f"Instrument reported an event after {command}"


@pytest.fixture(scope="module")
def mso(connected_device_address):
    """Connect to the instrument once per module, through the class of its model.

    Skips if the instrument is not a 4, 5 or 6 Series MSO.

    :param connected_device_address: VISA resource name from ``--device-address``.
    :returns: The connected instrument, closed when the module finishes.
    """
    instrument = TektronixMSO456(connected_device_address)
    model = MODEL_PATTERN.match(instrument.id)
    if model is None:
        instrument.adapter.close()
        pytest.skip(f"Not a 4, 5 or 6 Series MSO: {instrument.id}")
    instrument_class = MODEL_CLASSES.get(model[1], TektronixMSO456)
    if instrument_class is not TektronixMSO456:
        instrument.adapter.close()
        instrument = instrument_class(connected_device_address)
    yield instrument
    instrument.adapter.close()


@pytest.fixture(scope="module")
def scratch_directory(mso):
    """Create the scratch directory on the instrument and remove it when the module finishes.

    A directory left over from an earlier run is reused and kept.

    :param mso: The connected instrument.
    :returns: The directory path.
    """
    created = True
    try:
        mso.make_directory(SCRATCH_DIRECTORY)
    except ValueError as exc:
        if "257" not in str(exc):
            raise
        created = False
    yield SCRATCH_DIRECTORY
    if created:
        mso.delete_file(SCRATCH_DIRECTORY)


@pytest.fixture(scope="module", autouse=True)
def restored_setup(mso, scratch_directory):
    """Save the setup to the scratch directory and recall it when the module finishes.

    Deletes the measurements and the results table the tests use once the setup is saved, so
    they start from a known state; the recall restores them.

    :param mso: The connected instrument.
    :param scratch_directory: The scratch directory path.
    """
    wait_for_setup_command(mso, f'SAVe:SETUp "{SETUP_FILE}"')
    mso.delete_all_measurements()
    try:
        mso.delete_results_table(TABLE)
    except ValueError as exc:
        if "100" not in str(exc):  # Anything but a missing table.
            raise
    yield
    wait_for_setup_command(mso, f'RECAll:SETUp "{SETUP_FILE}"')
    mso.delete_file(SETUP_FILE)


@pytest.fixture
def keep():
    """Return a function that records properties now and sets them back after the test.

    ``keep(target, "a", "b")`` reads ``target.a`` and ``target.b`` and restores them in reverse
    order when the test finishes, so name a setting before the one it couples with.

    :returns: The recording function.
    """
    saved: list[tuple[object, str, object]] = []

    def record(target: object, *names: str) -> None:
        saved.extend((target, name, getattr(target, name)) for name in names)

    yield record
    for target, name, value in reversed(saved):
        setattr(target, name, value)


@pytest.fixture
def working_directory(mso, scratch_directory):
    """Make the scratch directory the working directory for one test and restore it after.

    :param mso: The connected instrument.
    :param scratch_directory: The scratch directory path.
    :returns: The scratch directory path.
    """
    original = mso.working_directory
    mso.working_directory = scratch_directory
    yield scratch_directory
    mso.working_directory = original


@pytest.fixture
def measurement(mso):
    """Add measurement 1 on CH1 for one test and delete every measurement after it.

    :param mso: The connected instrument.
    :returns: The measurement, with the type the instrument chose.
    """
    added = mso.add_measurement(1)
    added.source = "CH1"
    yield added
    mso.delete_all_measurements()


@pytest.fixture
def results_table(mso):
    """Add the results table for one test and delete it after, unless the test did.

    :param mso: The connected instrument.
    :returns: The table name.
    """
    mso.add_results_table(TABLE)
    yield TABLE
    try:
        mso.delete_results_table(TABLE)
    except ValueError as exc:
        if "100" not in str(exc):  # Anything but a table deleted already.
            raise


@pytest.fixture(scope="module")
def signal_triggers(mso) -> bool:
    """Set the edge trigger on CH1 at the midpoint of its signal and tell whether it fires.

    Runs one single sequence to find out, forcing the trigger if the signal does not fire it,
    and returns to continuous acquisition in AUTO mode afterwards. The trigger setup stays for
    the tests that need it.

    :param mso: The connected instrument.
    :returns: Whether the signal fired the trigger within 3 s.
    """
    mso.trigger_type = "EDGE"
    mso.trigger_source = "CH1"
    mso.set_trigger_level_to_midpoint()
    assert mso.complete == "1"
    mso.stop_after = "SEQUENCE"
    mso.sequence_count = 1
    mso.acquisition_enabled = True
    triggered = wait_until(lambda: not mso.acquisition_enabled, timeout=3.0)
    if not triggered:
        assert wait_until(lambda: mso.trigger_state == "READY")
        mso.force_trigger()
        assert wait_until(lambda: not mso.acquisition_enabled)
    mso.trigger_mode = "AUTO"  # Reverts stop_after to RUNSTOP.
    mso.acquisition_enabled = True
    return triggered


@pytest.fixture
def triggering_signal(signal_triggers):
    """Skip the test when CH1 carries no signal that fires the edge trigger.

    :param signal_triggers: Whether the edge trigger at the midpoint of CH1 fires.
    """
    if not signal_triggers:
        pytest.skip("CH1 carries no signal that fires the edge trigger at its midpoint.")


def test_write_termination_is_newline(mso):
    assert mso.adapter.connection.write_termination == "\n"


def test_read_termination_is_disabled(mso):
    assert mso.adapter.connection.read_termination is None


def test_id(mso):
    assert mso.id.startswith("TEKTRONIX,")


def test_connecting_sets_bare_verbose_replies(mso):
    assert mso.ask("HEADer?").strip() in ("0", "OFF")
    assert mso.ask("VERBose?").strip() in ("1", "ON")


def test_check_errors_reads_queued_error_once(mso):
    mso.write("BOGUS:COMMAND")
    errors = mso.check_errors()
    assert len(errors) == 1
    assert errors[0][0] in COMMAND_ERRORS
    assert mso.check_errors() == []


def test_connecting_clears_event_queue(mso, connected_device_address):
    mso.write("BOGUS:COMMAND")
    second = TektronixMSO456(connected_device_address)
    try:
        assert second.check_errors() == []
    finally:
        second.adapter.close()


def test_setup_file_is_saved_in_scratch_directory(mso, working_directory):
    assert SETUP_FILE_NAME in mso.directory_listing


def test_acquisition_enabled_stops_and_restarts_acquisition(mso, keep):
    keep(mso, "acquisition_enabled")
    mso.acquisition_enabled = False
    assert mso.acquisition_enabled is False
    assert mso.trigger_state == "SAVE"
    mso.acquisition_enabled = True
    assert mso.acquisition_enabled is True
    assert wait_until(lambda: mso.trigger_state != "SAVE")


def test_acquisition_count_carries_on_over_a_restart_and_resets_with_clear(mso, keep):
    keep(mso, "acquisition_enabled")
    mso.acquisition_enabled = True
    assert wait_until(lambda: mso.acquisition_count >= MANY_ACQUISITIONS, timeout=30.0)
    mso.acquisition_enabled = False
    count = mso.acquisition_count
    mso.acquisition_enabled = True
    assert mso.acquisition_count >= count
    mso.clear_acquisitions()
    assert mso.complete == "1"
    # The count resets a moment after the clear completes.
    assert wait_until(lambda: mso.acquisition_count < MANY_ACQUISITIONS)


def test_stop_after_sequence_switches_trigger_mode_to_normal_and_auto_reverts_it(mso, keep):
    keep(mso, "acquisition_enabled", "stop_after", "trigger_mode")
    mso.stop_after = "SEQUENCE"
    assert mso.stop_after == "SEQUENCE"
    assert mso.trigger_mode == "NORMAL"
    mso.trigger_mode = "AUTO"
    assert mso.trigger_mode == "AUTO"
    assert mso.stop_after == "RUNSTOP"


@pytest.mark.parametrize("count", [1, 7, 300])
def test_sequence_count_round_trips(mso, keep, count):
    keep(mso, "sequence_count")
    mso.sequence_count = count
    assert mso.sequence_count == count


def test_single_sequence_acquires_sequence_count_acquisitions(mso, keep, triggering_signal):
    keep(mso, "acquisition_enabled", "sequence_count", "stop_after", "trigger_mode")
    mso.stop_after = "SEQUENCE"
    mso.sequence_count = SEQUENCE_COUNT
    mso.acquisition_enabled = True
    with visa_timeout(mso, LONG_TIMEOUT):
        assert mso.complete == "1"
    assert mso.acquisition_enabled is False
    assert mso.trigger_state == "SAVE"
    assert mso.acquisition_count == SEQUENCE_COUNT


def test_force_trigger_completes_a_sequence_waiting_for_a_trigger(mso, keep):
    keep(mso, "acquisition_enabled", "sequence_count", "stop_after", "trigger_mode")
    keep(mso, "trigger_type", "trigger_source")
    keep(mso.ch1, "trigger_level")
    mso.trigger_type = "EDGE"
    mso.trigger_source = "CH1"
    # The level clamps to the top of the screen, which a signal on screen does not reach.
    mso.ch1.trigger_level = FAR_LEVEL
    mso.stop_after = "SEQUENCE"
    mso.sequence_count = 1
    mso.acquisition_enabled = True
    assert wait_until(lambda: mso.trigger_state == "READY" or not mso.acquisition_enabled)
    if not mso.acquisition_enabled:
        pytest.skip("The signal on CH1 reaches the highest trigger level, so nothing waited.")
    mso.force_trigger()
    with visa_timeout(mso, LONG_TIMEOUT):
        assert mso.complete == "1"
    assert mso.acquisition_enabled is False
    assert mso.acquisition_count == 1


def test_clear_acquisitions_resets_the_statistics(mso, keep, measurement):
    keep(mso, "acquisition_enabled")
    mso.acquisition_enabled = True
    measurement.type = "MEAN"
    assert wait_until(lambda: measurement.population >= MANY_ACQUISITIONS, timeout=30.0)
    mso.clear_acquisitions()
    assert mso.complete == "1"
    assert measurement.population < MANY_ACQUISITIONS


def test_channel_display_round_trips(mso, keep):
    keep(mso.ch1, "display_enabled", "global_display_enabled")
    mso.ch1.display_enabled = True
    mso.ch1.global_display_enabled = True
    assert mso.ch1.display_enabled is True
    assert mso.ch1.global_display_enabled is True
    mso.ch1.global_display_enabled = False
    assert mso.ch1.global_display_enabled is False
    # Disabling the global display also takes the channel out of the waveform view.
    assert mso.ch1.display_enabled is False
    mso.ch1.global_display_enabled = True
    assert mso.ch1.global_display_enabled is True
    assert mso.ch1.display_enabled is True
    mso.ch1.display_enabled = False
    assert mso.ch1.display_enabled is False
    mso.ch1.display_enabled = True
    assert mso.ch1.display_enabled is True


def test_trigger_level_round_trips_and_clamps_silently(mso, keep):
    keep(mso.ch1, "trigger_level")
    level = mso.ch1.trigger_level
    mso.ch1.trigger_level = FAR_LEVEL
    highest = mso.ch1.trigger_level
    mso.ch1.trigger_level = -FAR_LEVEL
    lowest = mso.ch1.trigger_level
    assert -FAR_LEVEL < lowest <= level <= highest < FAR_LEVEL
    assert mso.check_errors() == []
    mso.ch1.trigger_level = level
    assert mso.ch1.trigger_level == level


def test_bandwidth_limit_round_trips_and_full_is_the_maximum(mso, keep):
    keep(mso.ch1, "bandwidth")
    mso.ch1.bandwidth = "FULL"
    full = mso.ch1.bandwidth
    # The query returns the bandwidth in effect as a number, never the keyword.
    assert isinstance(full, float)
    mso.ch1.bandwidth = LOW_BANDWIDTH
    assert mso.ch1.bandwidth == LOW_BANDWIDTH
    assert full > LOW_BANDWIDTH
    mso.ch1.bandwidth = "FULL"
    assert mso.ch1.bandwidth == full
    assert mso.check_errors() == []


def test_bandwidth_rounds_off_step_value_and_clamps_silently(mso, keep):
    keep(mso.ch1, "bandwidth")
    mso.ch1.bandwidth = "FULL"
    full = mso.ch1.bandwidth
    # The query returns the bandwidth in effect as a number, never the keyword.
    assert isinstance(full, float)
    mso.ch1.bandwidth = OFF_STEP_BANDWIDTH
    rounded = mso.ch1.bandwidth
    assert rounded != OFF_STEP_BANDWIDTH
    assert LOW_BANDWIDTH <= rounded <= full
    mso.ch1.bandwidth = HUGE_BANDWIDTH
    assert mso.ch1.bandwidth == full
    assert mso.check_errors() == []


def test_set_trigger_level_to_midpoint_centers_the_level_in_the_signal(
    mso, keep, measurement, triggering_signal
):
    keep(mso.ch1, "trigger_level")
    measurement.type = "MINIMUM"
    maximum = mso.add_measurement(2)
    maximum.source = "CH1"
    maximum.type = "MAXIMUM"
    assert wait_until(lambda: min(measurement.population, maximum.population) >= FEW_VALUES)
    mso.ch1.trigger_level = -FAR_LEVEL
    mso.set_trigger_level_to_midpoint()
    assert mso.complete == "1"
    low, high = measurement.mean, maximum.mean
    # The level is quantised to a fraction of the vertical scale, so allow a bit of the span.
    assert mso.ch1.trigger_level == pytest.approx((low + high) / 2, abs=0.05 * (high - low))


@pytest.mark.parametrize(("prop", "values"), ROUND_TRIPS, ids=[prop for prop, _ in ROUND_TRIPS])
def test_setting_round_trips(mso, keep, prop, values):
    keep(mso, prop)
    for value in values:
        setattr(mso, prop, value)
        assert getattr(mso, prop) == value
    assert mso.check_errors() == []


def test_trigger_source_accepts_every_analog_channel_and_line(mso, keep):
    keep(mso, "trigger_source")
    for number in mso.channels:
        mso.trigger_source = f"CH{number}"
        assert mso.trigger_source == f"CH{number}"
    mso.trigger_source = "LINE"
    assert mso.trigger_source == "LINE"


def test_trigger_source_auxiliary_depends_on_the_model(mso, keep):
    keep(mso, "trigger_source")
    mso.trigger_source = "CH1"
    try:
        mso.trigger_source = "AUXILIARY"
    except ValueError as exc:
        # A model without an auxiliary input, such as the MSO58, rejects the keyword.
        assert "rejected the setting: 141" in str(exc)
        assert mso.trigger_source == "CH1"
    else:
        assert mso.trigger_source == "AUXILIARY"


def test_trigger_state_is_a_documented_state(mso):
    assert mso.trigger_state in TRIGGER_STATES


def test_horizontal_scale_rounds_off_step_value_silently(mso, keep):
    keep(mso, "horizontal_scale")
    mso.horizontal_scale = OFF_STEP_SCALE
    applied = mso.horizontal_scale
    # Which of the two steps applies is for the bench to tell.
    assert STEP_BELOW <= applied <= STEP_ABOVE
    assert mso.check_errors() == []


def test_horizontal_scale_clamps_to_the_limits_silently(mso, keep):
    keep(mso, "horizontal_scale")
    mso.horizontal_scale = HUGE_SCALE
    slowest = mso.horizontal_scale
    mso.horizontal_scale = TINY_SCALE
    fastest = mso.horizontal_scale
    assert TINY_SCALE < fastest < slowest < HUGE_SCALE
    assert mso.check_errors() == []


def test_horizontal_scale_fits_the_cycles_on_screen(mso, keep, measurement, triggering_signal):
    keep(mso, "horizontal_scale")
    measurement.type = "FREQUENCY"
    assert wait_until(lambda: measurement.population >= FEW_VALUES)
    frequency = measurement.mean
    mso.horizontal_scale = CYCLES_ON_SCREEN / (DIVISIONS * frequency)
    # The instrument rounds the scale, so the expectation follows the scale that applies.
    expected = DIVISIONS * mso.horizontal_scale * frequency
    mso.clear_acquisitions()
    assert mso.complete == "1"
    assert wait_until(lambda: measurement.current_population >= 1)
    # A per-cycle type counts the whole cycles in the record, one fewer when the edges fall
    # just inside the screen.
    assert abs(measurement.current_population - expected) <= 1
    assert mso.check_errors() == []


@pytest.mark.parametrize(("value", "applied"), [(1.26, 1.25), (0.074, 0.05), (99.98, 100.0)])
def test_persistence_decay_time_rounds_to_the_nearest_50_ms(mso, keep, value, applied):
    keep(mso, "persistence_decay_time")
    mso.persistence_decay_time = value
    assert mso.persistence_decay_time == applied
    assert mso.check_errors() == []


def test_reset_persistence_queues_no_event(mso, keep):
    keep(mso, "persistence")
    mso.persistence = "INFPERSIST"
    mso.reset_persistence()
    assert mso.complete == "1"
    assert mso.persistence == "INFPERSIST"
    assert mso.check_errors() == []


def test_add_and_delete_measurements_update_the_instrument_list(mso):
    assert mso.measurement_names == []
    first = mso.add_measurement(1)
    second = mso.add_measurement(2)
    assert mso.measurement_names == ["MEAS1", "MEAS2"]
    mso.delete_measurement(second)
    assert mso.measurement_names == ["MEAS1"]
    assert mso.measurements == {1: first}
    mso.delete_all_measurements()
    assert mso.measurement_names == []
    assert mso.measurements == {}
    assert mso.check_errors() == []


def test_add_measurement_twice_raises(mso, measurement):
    with pytest.raises(ValueError, match="rejected adding measurement 1: 100"):
        mso.add_measurement(1)
    assert mso.measurement_names == ["MEAS1"]
    assert mso.measurements == {1: measurement}


def test_delete_measurement_twice_raises(mso):
    added = mso.add_measurement(1)
    mso.delete_measurement(added)
    with pytest.raises(ValueError, match="rejected deleting measurement 1: 100"):
        mso.delete_measurement(added)


def test_new_measurement_has_a_listed_type_and_no_population_limit(mso, measurement):
    assert measurement.type in TektronixMSO456Measurement.TYPES
    assert measurement.population_limit_enabled is False
    assert measurement.uses_global_population_limit is False
    assert measurement.badge_statistics_enabled is False
    assert measurement.population_limit >= 1


@pytest.mark.parametrize("measurement_type", TektronixMSO456Measurement.TYPES)
def test_measurement_type_round_trips(mso, measurement, measurement_type):
    measurement.type = measurement_type
    assert measurement.type == measurement_type
    assert mso.check_errors() == []


def test_measurement_source_accepts_every_analog_channel(mso, measurement):
    for number in mso.channels:
        measurement.source = f"CH{number}"
        assert measurement.source == f"CH{number}"


@pytest.mark.parametrize("label", ['Rise 50% "µs", fast', "", "Rise time"])
def test_measurement_label_round_trips(mso, measurement, label):
    measurement.label = label
    assert measurement.label == label


@pytest.mark.parametrize("prop", ["uses_global_population_limit", "badge_statistics_enabled"])
def test_measurement_flag_round_trips(mso, measurement, prop):
    setattr(measurement, prop, True)
    assert getattr(measurement, prop) is True
    setattr(measurement, prop, False)
    assert getattr(measurement, prop) is False


def test_whole_record_results_accumulate_one_value_per_acquisition(mso, keep, measurement):
    keep(mso, "acquisition_enabled")
    mso.acquisition_enabled = True
    measurement.type = "MEAN"
    assert wait_until(lambda: measurement.population >= FEW_VALUES)
    assert measurement.current_population == 1
    assert math.isfinite(measurement.current_mean)
    assert measurement.current_peak_to_peak == 0
    assert measurement.current_standard_deviation == 0
    assert measurement.minimum <= measurement.mean <= measurement.maximum
    assert measurement.peak_to_peak == pytest.approx(measurement.maximum - measurement.minimum)
    assert measurement.standard_deviation >= 0
    assert mso.check_errors() == []


@pytest.mark.parametrize("measurement_type", TRANSIENT_TYPES)
def test_transient_types_measure_the_signal(
    mso, keep, measurement, triggering_signal, measurement_type
):
    keep(mso, "acquisition_enabled")
    mso.acquisition_enabled = True
    measurement.type = measurement_type
    assert wait_until(lambda: measurement.population >= FEW_VALUES)
    assert math.isfinite(measurement.mean)
    assert measurement.minimum <= measurement.mean <= measurement.maximum
    assert measurement.current_population >= 1
    if measurement_type in ("RISETIME", "FALLTIME", "PK2PK"):
        assert measurement.mean > 0
    assert mso.check_errors() == []


def test_whole_record_population_equals_sequence_count(mso, keep, measurement, triggering_signal):
    keep(mso, "acquisition_enabled", "sequence_count", "stop_after", "trigger_mode")
    measurement.type = "MEAN"
    mso.stop_after = "SEQUENCE"
    mso.sequence_count = SEQUENCE_COUNT
    mso.acquisition_enabled = True
    with visa_timeout(mso, LONG_TIMEOUT):
        assert mso.complete == "1"
    assert mso.acquisition_count == SEQUENCE_COUNT
    assert measurement.population == SEQUENCE_COUNT
    assert measurement.current_population == 1


def test_read_populations_matches_each_population_once_stopped(mso, keep, measurement):
    keep(mso, "acquisition_enabled")
    measurement.type = "MEAN"
    second = mso.add_measurement(2)
    second.source = "CH1"
    second.type = "MAXIMUM"
    mso.acquisition_enabled = True
    assert wait_until(lambda: min(mso.read_populations([1, 2]).values()) >= FEW_VALUES)
    mso.acquisition_enabled = False
    assert mso.complete == "1"
    populations = mso.read_populations([1, 2])
    assert populations == {1: measurement.population, 2: second.population}
    assert mso.check_errors() == []


def test_population_limit_freezes_the_statistics(mso, keep, measurement):
    keep(mso, "acquisition_enabled")
    mso.acquisition_enabled = True
    measurement.type = "MEAN"
    measurement.population_limit = POPULATION_LIMIT
    measurement.population_limit_enabled = True
    assert measurement.population_limit == POPULATION_LIMIT
    assert measurement.population_limit_enabled is True
    mso.clear_acquisitions()
    assert mso.complete == "1"
    assert wait_until(lambda: measurement.population == POPULATION_LIMIT)
    time.sleep(0.5)  # A few more acquisitions, which must not count.
    assert measurement.population == POPULATION_LIMIT
    assert measurement.current_population == 0
    assert math.isnan(measurement.current_mean)
    assert math.isfinite(measurement.mean)
    measurement.population_limit_enabled = False
    assert wait_until(lambda: measurement.population > POPULATION_LIMIT)
    assert mso.check_errors() == []


def test_results_after_clear_while_stopped_are_nan_and_queue_only_warnings(mso, keep, measurement):
    keep(mso, "acquisition_enabled")
    mso.acquisition_enabled = True
    measurement.type = "MEAN"
    assert wait_until(lambda: measurement.population >= 1)
    mso.acquisition_enabled = False
    mso.clear_acquisitions()
    assert mso.complete == "1"
    assert math.isnan(measurement.mean)
    assert math.isnan(measurement.current_mean)
    assert measurement.population == 0
    # One warning per invalid result read; the population reads queue none.
    assert [code for code, _ in mso.check_errors()] == [540, 540]
    # A setter reads the queue as well, and warnings must not make it raise.
    assert math.isnan(measurement.maximum)
    measurement.label = "after warning"
    assert measurement.label == "after warning"
    assert mso.check_errors() == []


def test_percent_presets_set_the_six_levels(mso, keep):
    keep(mso, "reference_level_percent_preset", *PERCENT_LEVELS)
    mso.reference_level_percent_preset = "TENNINETY"
    assert [getattr(mso, level) for level in PERCENT_LEVELS] == [90, 50, 10, 90, 50, 10]
    mso.reference_level_percent_preset = "TWENTYEIGHTY"
    assert [getattr(mso, level) for level in PERCENT_LEVELS] == [80, 50, 20, 80, 50, 20]


def test_custom_percent_levels_round_trip(mso, keep):
    keep(mso, "reference_level_percent_preset", *PERCENT_LEVELS)
    mso.reference_level_percent_preset = "CUSTOM"
    values = [85.5, 45.0, 15.5, 84.0, 46.0, 16.0]
    for level, value in zip(PERCENT_LEVELS, values, strict=True):
        setattr(mso, level, value)
    assert [getattr(mso, level) for level in PERCENT_LEVELS] == values
    assert mso.reference_level_percent_preset == "CUSTOM"
    assert mso.check_errors() == []


def test_add_and_delete_results_table(mso):
    mso.add_results_table(TABLE)
    mso.delete_results_table(TABLE)
    assert mso.check_errors() == []


def test_add_results_table_twice_raises(mso, results_table):
    with pytest.raises(ValueError, match=f"rejected adding results table {TABLE}: 100"):
        mso.add_results_table(results_table)


def test_delete_missing_results_table_raises(mso):
    with pytest.raises(ValueError, match=f"rejected deleting results table {TABLE}: 100"):
        mso.delete_results_table(TABLE)


def test_working_directory_round_trips_without_trailing_slash(mso, working_directory):
    assert mso.working_directory == working_directory
    mso.working_directory = f"{working_directory}/"
    assert mso.working_directory == working_directory


def test_working_directory_rejects_missing_directory_and_keeps_current(mso):
    original = mso.working_directory
    with pytest.raises(ValueError, match="rejected the setting: 256"):
        mso.working_directory = MISSING_DIRECTORY
    assert mso.working_directory == original


def test_save_image_read_file_and_delete_file(mso, scratch_directory):
    path = f"{scratch_directory}/pilgrim_test.png"
    mso.save_image(path)
    assert mso.complete == "1"
    assert mso.check_errors() == []
    contents = mso.read_file(path)
    mso.delete_file(path)
    assert contents.startswith(PNG_SIGNATURE)
    assert contents.endswith(PNG_TRAILER)


def test_directory_listing_shows_image_saved_with_relative_path(mso, working_directory):
    name = "pilgrim_listing.png"
    mso.save_image(name)
    assert mso.complete == "1"
    assert mso.check_errors() == []
    try:
        assert name in mso.directory_listing
    finally:
        mso.delete_file(name)
    assert name not in mso.directory_listing


def test_save_image_into_missing_directory_queues_error_once_complete(mso):
    mso.save_image(f"{MISSING_DIRECTORY}/pilgrim_test.png")
    assert mso.complete == "1"
    assert [code for code, _ in mso.check_errors()] == [200]


def test_read_file_of_missing_file_raises_and_leaves_no_event(mso, scratch_directory):
    path = f"{scratch_directory}/pilgrim_missing.png"
    with pytest.raises(ValueError, match=re.escape(f"rejected reading file {path}: 256")):
        mso.read_file(path)
    assert mso.check_errors() == []


def test_read_file_of_directory_raises(mso, scratch_directory):
    expected = f"rejected reading file {scratch_directory}: 257"
    with pytest.raises(ValueError, match=re.escape(expected)):
        mso.read_file(scratch_directory)


def test_make_directory_of_existing_directory_raises(mso, scratch_directory):
    expected = f"rejected creating directory {scratch_directory}: 257"
    with pytest.raises(ValueError, match=re.escape(expected)):
        mso.make_directory(scratch_directory)


def test_delete_file_of_missing_path_raises(mso, scratch_directory):
    path = f"{scratch_directory}/pilgrim_missing.png"
    with pytest.raises(ValueError, match=re.escape(f"rejected deleting {path}: 256")):
        mso.delete_file(path)


@pytest.fixture
def cursor(mso, keep) -> TektronixMSO456Cursor:
    """Record the cursor settings and the display state of CH1 and set them back after the test.

    CH1 is recorded first, so it is set back last and the cursor sources, which may name it,
    are restored while it is still shown.

    :param mso: The connected instrument.
    :param keep: The restoring recorder.
    :returns: The cursor pair.
    """
    keep(mso.ch1, "display_enabled")
    keep(mso.cursor, "enabled", "function", "mode", "split_mode", "source_a", "source_b")
    return mso.cursor


@pytest.fixture
def band_vertical(mso, keep):
    """Set the vertical settings of CH1 to those of the band tests and set them back after.

    :param mso: The connected instrument.
    :param keep: The restoring recorder.
    """
    keep(mso.ch1, "scale", "position", "offset")
    mso.ch1.scale = BAND_SCALE
    mso.ch1.position = BAND_POSITION
    mso.ch1.offset = BAND_OFFSET


def show_cursors_on_ch1(mso: TektronixMSO456) -> None:
    """Show the horizontal bar cursors on CH1 through the properties, not ``set_levels``.

    :param mso: The connected instrument.
    """
    mso.ch1.display_enabled = True
    mso.cursor.split_mode = "SAME"
    mso.cursor.source_a = "CH1"
    mso.cursor.function = "HBARS"
    mso.cursor.enabled = True


def test_vertical_settings_round_trip_and_set_the_screen_range(mso, keep):
    keep(mso.ch1, "scale", "position", "offset")
    mso.ch1.scale = 0.2
    mso.ch1.position = -2.0
    mso.ch1.offset = 0.1
    assert mso.ch1.scale == pytest.approx(0.2)
    assert mso.ch1.position == pytest.approx(-2.0)
    assert mso.ch1.offset == pytest.approx(0.1)
    # The offset is shown 2 divisions below the center, so 3 divisions above the bottom.
    assert mso.ch1.screen_range == pytest.approx((-0.5, 1.5))


@pytest.mark.parametrize(
    ("prop", "values"),
    [
        ("function", ["VBARS", "SCREEN", "WAVEFORM", "HBARS"]),
        ("mode", ["TRACK", "INDEPENDENT"]),
        ("split_mode", ["SPLIT", "SAME"]),
        ("enabled", [True, False]),
    ],
)
def test_cursor_setting_round_trips(cursor, prop, values):
    for value in values:
        setattr(cursor, prop, value)
        assert getattr(cursor, prop) == value


def test_cursor_source_needs_the_channel_in_the_waveform_view(mso, cursor):
    mso.ch1.display_enabled = False
    with pytest.raises(ValueError, match="141"):
        cursor.source_a = "CH1"
    mso.ch1.display_enabled = True
    cursor.split_mode = "SAME"
    cursor.source_a = "CH1"
    assert cursor.source_a == "CH1"
    assert cursor.source_b == "CH1"  # Follows cursor A while the sources are the same.


def test_split_sources_differ_and_same_copies_source_a(mso, cursor):
    mso.ch1.display_enabled = True
    cursor.split_mode = "SPLIT"
    cursor.source_a = "CH1"
    cursor.source_b = "AUTO"
    assert cursor.source_a == "CH1"
    assert cursor.source_b == "AUTO"
    cursor.split_mode = "SAME"
    assert cursor.source_b == "CH1"


def test_hiding_the_source_channel_reverts_the_sources_to_auto(mso, cursor):
    mso.ch1.display_enabled = True
    cursor.split_mode = "SAME"
    cursor.source_a = "CH1"
    mso.ch1.display_enabled = False
    assert cursor.source_a == "AUTO"
    assert cursor.source_b == "AUTO"
    assert mso.check_errors() == []


def test_levels_round_trip_and_clamp_to_the_screen_range(mso, cursor):
    show_cursors_on_ch1(mso)
    bottom, top = mso.ch1.screen_range
    quarter = (top - bottom) / 4
    cursor.level_a = bottom + quarter
    cursor.level_b = top - quarter
    assert cursor.level_a == pytest.approx(bottom + quarter, rel=LEVEL_TOLERANCE)
    assert cursor.level_b == pytest.approx(top - quarter, rel=LEVEL_TOLERANCE)
    assert cursor.level_delta == pytest.approx(2 * quarter, rel=LEVEL_TOLERANCE)
    cursor.level_a = top + mso.ch1.scale
    cursor.level_b = bottom - mso.ch1.scale
    assert cursor.level_a == pytest.approx(top, rel=LEVEL_TOLERANCE)
    assert cursor.level_b == pytest.approx(bottom, rel=LEVEL_TOLERANCE)
    assert mso.check_errors() == []


def test_level_delta_is_the_distance_whichever_cursor_is_higher(mso, cursor):
    show_cursors_on_ch1(mso)
    bottom, top = mso.ch1.screen_range
    quarter = (top - bottom) / 4
    cursor.level_a = top - quarter
    cursor.level_b = bottom + quarter
    assert cursor.level_delta == pytest.approx(2 * quarter, rel=LEVEL_TOLERANCE)


def test_hiding_the_cursors_reverts_the_function_to_waveform(mso, cursor):
    show_cursors_on_ch1(mso)
    bottom, top = mso.ch1.screen_range
    middle = (bottom + top) / 2
    cursor.level_a = middle
    cursor.enabled = False
    assert cursor.function == "WAVEFORM"
    # Selecting the horizontal bars again makes the levels available once more.
    cursor.enabled = True
    cursor.function = "HBARS"
    cursor.level_a = middle
    assert cursor.level_a == pytest.approx(middle, rel=LEVEL_TOLERANCE)
    assert mso.check_errors() == []


def test_set_level_band_shows_the_cursors_around_the_target(mso, cursor, band_vertical):
    mso.ch1.display_enabled = False
    assert cursor.set_level_band(mso.ch1, BAND_TARGET, BAND_PERCENT) == pytest.approx(BAND_LEVELS)
    assert mso.ch1.display_enabled is True
    assert cursor.enabled is True
    assert cursor.function == "HBARS"
    assert cursor.source_a == "CH1"
    assert cursor.source_b == "CH1"
    assert cursor.level_a == pytest.approx(BAND_LEVELS[0], rel=LEVEL_TOLERANCE)
    assert cursor.level_b == pytest.approx(BAND_LEVELS[1], rel=LEVEL_TOLERANCE)
    assert cursor.level_delta == pytest.approx(BAND_LEVELS[1] - BAND_LEVELS[0], rel=LEVEL_TOLERANCE)
    assert mso.check_errors() == []


def test_set_level_band_raises_when_the_band_does_not_fit_the_screen(mso, cursor, band_vertical):
    cursor.enabled = False
    with pytest.raises(ValueError, match="outside the screen range of CH1"):
        cursor.set_level_band(mso.ch1, BAND_TARGET, WIDE_PERCENT)
    assert cursor.enabled is False
