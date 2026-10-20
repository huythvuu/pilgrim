"""Device tests for the Tektronix AFG31000 base classes.

Run with ``--device-address=<VISA resource name>`` of any AFG31000 model. The tests drive
channel 1 with values valid on every model and assume it runs in continuous mode. They never
switch the output on and skip if it already is. They save the setup to the last-setup memory
(location 0) first and recall it afterwards.
"""

from typing import TYPE_CHECKING

import pytest
from pymeasure.instruments import Instrument

from pilgrim.instruments import TektronixAFG31000, TektronixAFG31000Channel

SAVED_SETTINGS = (
    "OUTP1:IMP",
    "SOUR1:VOLT:LIM:HIGH",
    "SOUR1:VOLT:LIM:LOW",
    "SOUR1:FUNC:SHAP",
    "SOUR1:FREQ",
    "SOUR1:PULS:DCYC",
    "SOUR1:PULS:TRAN:LEAD",
    "SOUR1:PULS:TRAN:TRA",
    "SOUR1:VOLT:HIGH",
    "SOUR1:VOLT:LOW",
    "SOUR1:VOLT:UNIT",
)
SINE_INTO_50_OHM = (
    "OUTP1:IMP 50",
    "SOUR1:VOLT:LIM:HIGH MAX",
    "SOUR1:VOLT:LIM:LOW MIN",
    "SOUR1:FUNC:SHAP SIN",
    "SOUR1:FREQ 1kHz",
    "SOUR1:VOLT:UNIT VPP",
    "SOUR1:VOLT:OFFS 0",
    "SOUR1:VOLT 1",
)
PULSE_AT_1_KHZ = (
    *SINE_INTO_50_OHM,
    "SOUR1:FUNC:SHAP PULS",
    "SOUR1:PULS:TRAN:LEAD 1us",
    "SOUR1:PULS:TRAN:TRA 1us",
    "SOUR1:PULS:DCYC 50",
)


class AnyAFG31000Channel(TektronixAFG31000Channel):
    """Channel with the ranges of the whole series, leaving model limits to the instrument."""

    frequency_values = (1e-6, 250e6)
    offset_values = (-9.999, 9.999)
    amplitude_high_values = (-9.998, 10.0)
    amplitude_low_values = (-10.0, 9.998)
    pulse_leading_values = (0.002, 625e6)
    pulse_trailing_values = (0.002, 625e6)


class AnyAFG31000(TektronixAFG31000):
    """Any AFG31000 model, driven through channel 1."""

    # Instantiation replaces the creator with the channel itself, which pyright cannot follow.
    if TYPE_CHECKING:
        ch1: AnyAFG31000Channel
    else:
        ch1 = Instrument.ChannelCreator(AnyAFG31000Channel, 1)


def prepare(afg: TektronixAFG31000, *commands: str) -> None:
    """Write raw setup commands and fail if the instrument rejects any of them."""
    for command in commands:
        afg.write(command)
    assert afg.check_errors() == [], f"Instrument rejected the setup: {commands}"


def snapshot(afg: TektronixAFG31000) -> dict[str, str]:
    """Read the settings these tests change."""
    return {setting: afg.ask(f"{setting}?").strip() for setting in SAVED_SETTINGS}


@pytest.fixture(scope="module")
def afg(connected_device_address):
    instrument = AnyAFG31000(connected_device_address)
    try:
        if not instrument.id.startswith("TEKTRONIX,AFG31"):
            pytest.skip(f"Not an AFG31000 series instrument: {instrument.id}")
        if instrument.ch1.output_enabled:
            pytest.skip("Channel 1 output is on, and these tests change its levels.")
        saved = snapshot(instrument)
        # Location 0 is overwritten with the current setup at every power-off anyway, and
        # recalling it restores settings that cannot be written back one by one.
        prepare(instrument, "*SAV 0")
        yield instrument
        prepare(instrument, "*RCL 0")
        assert snapshot(instrument) == saved, "Instrument settings were not restored"
    finally:
        instrument.adapter.close()


def test_write_termination_is_newline(afg):
    assert afg.adapter.connection.write_termination == "\n"


def test_connecting_clears_error_queue(afg, connected_device_address):
    afg.write("SOUR1:FREQ 1e12")  # queue an out-of-range error
    second = AnyAFG31000(connected_device_address)
    try:
        assert second.check_errors() == []
    finally:
        second.adapter.close()


def test_output_is_off(afg):
    assert afg.ch1.output_enabled is False


@pytest.mark.parametrize("shape", ["SIN", "SQU", "PULS", "RAMP"])
def test_shape(afg, shape):
    prepare(afg, *SINE_INTO_50_OHM)
    afg.ch1.shape = shape
    assert afg.ch1.shape == shape


@pytest.mark.parametrize("frequency", [1e-6, 1234.5678, 10000000.1])
def test_frequency_keeps_full_resolution(afg, frequency):
    prepare(afg, *SINE_INTO_50_OHM)
    afg.ch1.frequency = frequency
    assert afg.ch1.frequency == pytest.approx(frequency, rel=1e-12)


@pytest.mark.parametrize(("unit", "amplitude"), [("VPP", 1.5), ("VRMS", 0.5), ("DBM", 0.0)])
def test_amplitude_in_unit(afg, unit, amplitude):
    prepare(afg, *SINE_INTO_50_OHM)
    afg.ch1.amplitude_unit = unit
    afg.ch1.amplitude = amplitude
    assert afg.ch1.amplitude_unit == unit
    assert afg.ch1.amplitude == pytest.approx(amplitude, abs=1e-4)


@pytest.mark.parametrize("offset", [-1.5, 0.25, 1.5])
def test_offset(afg, offset):
    prepare(afg, *SINE_INTO_50_OHM)
    afg.ch1.offset = offset
    assert afg.ch1.offset == pytest.approx(offset, abs=1e-3)


def test_levels(afg):
    prepare(afg, *SINE_INTO_50_OHM)
    afg.ch1.amplitude_low = -0.75
    afg.ch1.amplitude_high = 1.25
    assert afg.ch1.amplitude_low == pytest.approx(-0.75, abs=1e-3)
    assert afg.ch1.amplitude_high == pytest.approx(1.25, abs=1e-3)


def test_low_level_above_high_level_moves_high_level_up(afg):
    prepare(afg, *SINE_INTO_50_OHM)
    afg.ch1.amplitude_low = 1.0
    assert 1.0 < afg.ch1.amplitude_high <= 1.0 + 0.01


def test_high_level_below_low_level_moves_low_level_down(afg):
    prepare(afg, *SINE_INTO_50_OHM)
    afg.ch1.amplitude_high = -1.0
    assert -1.0 - 0.01 <= afg.ch1.amplitude_low < -1.0


@pytest.mark.parametrize("duty", [25.0, 75.0])
def test_pulse_dutycycle(afg, duty):
    prepare(afg, *PULSE_AT_1_KHZ)
    afg.ch1.pulse_dutycycle = duty
    assert afg.ch1.pulse_dutycycle == pytest.approx(duty, abs=1e-3)


@pytest.mark.parametrize("prop", ["pulse_leading", "pulse_trailing"])
@pytest.mark.parametrize("microseconds", [0.008, 1.0, 100.0])
def test_pulse_edge_time_in_microseconds(afg, prop, microseconds):
    prepare(afg, *PULSE_AT_1_KHZ)
    setattr(afg.ch1, prop, microseconds)
    assert getattr(afg.ch1, prop) == pytest.approx(microseconds, rel=1e-3)


def test_frequency_above_shape_maximum_raises(afg):
    prepare(afg, *SINE_INTO_50_OHM, "SOUR1:FUNC:SHAP RAMP")
    # Ramp tops out at 2.5 MHz even on the fastest model.
    with pytest.raises(ValueError, match="-222"):
        afg.ch1.frequency = 10e6
    assert afg.ch1.frequency == pytest.approx(1e3)


def test_amplitude_above_range_raises(afg):
    prepare(afg, *SINE_INTO_50_OHM)
    with pytest.raises(ValueError, match="-222"):
        afg.ch1.amplitude = 30
    assert afg.ch1.amplitude == pytest.approx(1.0)


def test_offset_beyond_50_ohm_range_raises(afg):
    prepare(afg, *SINE_INTO_50_OHM)
    with pytest.raises(ValueError, match="-222"):
        afg.ch1.offset = 9.999


def test_edge_time_beyond_pulse_period_raises(afg):
    prepare(afg, *PULSE_AT_1_KHZ)
    # 0.625 times the 1 ms period is the longest edge the instrument accepts.
    with pytest.raises(ValueError, match="-222"):
        afg.ch1.pulse_leading = 1000


def test_dbm_with_high_z_load_raises(afg):
    prepare(afg, *SINE_INTO_50_OHM, "OUTP1:IMP INF")
    with pytest.raises(ValueError, match="-221"):
        afg.ch1.amplitude_unit = "DBM"


def test_dbm_with_non_sine_shape_raises(afg):
    prepare(afg, *SINE_INTO_50_OHM, "SOUR1:FUNC:SHAP SQU")
    with pytest.raises(ValueError, match="-221"):
        afg.ch1.amplitude_unit = "DBM"


def test_leaving_sine_switches_dbm_to_vpp(afg):
    prepare(afg, *SINE_INTO_50_OHM, "SOUR1:VOLT:UNIT DBM")
    afg.ch1.shape = "SQU"
    assert afg.ch1.amplitude_unit == "VPP"
