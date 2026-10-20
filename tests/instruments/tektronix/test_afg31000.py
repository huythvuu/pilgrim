"""Protocol tests for the Tektronix AFG31000 base classes, run on a fictitious model."""

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pymeasure.instruments import Instrument
from pymeasure.test import expected_protocol
from pyvisa.resources import MessageBasedResource

from pilgrim.instruments import TektronixAFG31000, TektronixAFG31000Channel

CLEAR = ("*CLS", None)
NO_ERROR = ("SYST:ERR?", '0,"No error"')
SIM_LIBRARY = f"{Path(__file__).with_name('afg31000_sim.yaml').as_posix()}@sim"


class FakeChannel(TektronixAFG31000Channel):
    """Channel of a fictitious model with round-number ranges."""

    frequency_values = (1e-6, 10e6)
    offset_values = (-5.0, 5.0)
    amplitude_high_values = (-5.0, 5.0)
    amplitude_low_values = (-5.0, 5.0)
    pulse_leading_values = (0.01, 1e6)
    pulse_trailing_values = (0.01, 1e6)


class FakeAFG(TektronixAFG31000):
    """Fictitious two-channel model, so channel numbering is covered too."""

    # Instantiation replaces the creator with the channel itself, which pyright cannot follow.
    if TYPE_CHECKING:
        ch1: FakeChannel
        ch2: FakeChannel
    else:
        ch1 = Instrument.ChannelCreator(FakeChannel, 1)
        ch2 = Instrument.ChannelCreator(FakeChannel, 2)


def test_init_clears_error_queue():
    with expected_protocol(FakeAFG, [CLEAR]) as afg:
        assert afg.name == "Tektronix AFG31000"


@pytest.mark.parametrize(
    ("resource", "kwargs"),
    [
        ("USB0::0x0699::0x0357::SIM0001::INSTR", {}),
        ("USB0::0x0699::0x0357::SIM0002::INSTR", {"write_termination": "\r\n"}),
    ],
    ids=["default", "explicit"],
)
def test_write_termination_reaches_visa_session(resource, kwargs):
    afg = FakeAFG(resource, visa_library=SIM_LIBRARY, **kwargs)
    try:
        connection = afg.adapter.connection
        assert isinstance(connection, MessageBasedResource)
        assert connection.write_termination == kwargs.get("write_termination", "\n")
        assert afg.id.startswith("TEKTRONIX,AFG31051")
    finally:
        afg.adapter.close()


@pytest.mark.parametrize("ch", [1, 2])
@pytest.mark.parametrize(
    ("prop", "value", "command"),
    [
        ("frequency", 10e6, "SOUR{ch}:FREQ 10000000"),
        ("frequency", 9999999.1, "SOUR{ch}:FREQ 9999999.1"),
        ("frequency", 1.5e-6, "SOUR{ch}:FREQ 1.5e-06"),
        ("amplitude", 2.5, "SOUR{ch}:VOLT 2.5"),
        ("amplitude", -56.02, "SOUR{ch}:VOLT -56.02"),
        ("amplitude_unit", "VRMS", "SOUR{ch}:VOLT:UNIT VRMS"),
        ("offset", -1.25, "SOUR{ch}:VOLT:OFFS -1.25"),
        ("amplitude_high", 1.5, "SOUR{ch}:VOLT:HIGH 1.5"),
        ("amplitude_low", -0.5, "SOUR{ch}:VOLT:LOW -0.5"),
        ("pulse_dutycycle", 12.5, "SOUR{ch}:PULS:DCYC 12.5"),
        ("pulse_leading", 0.01, "SOUR{ch}:PULS:TRAN:LEAD 1e-08"),
        ("pulse_trailing", 2.5, "SOUR{ch}:PULS:TRAN:TRA 2.5e-06"),
    ],
)
def test_setter_writes_command_then_checks_errors(ch, prop, value, command):
    with expected_protocol(FakeAFG, [CLEAR, (command.format(ch=ch), None), NO_ERROR]) as afg:
        setattr(afg.channels[ch], prop, value)


@pytest.mark.parametrize("ch", [1, 2])
@pytest.mark.parametrize(
    ("prop", "value", "command"),
    [
        ("shape", "SQU", "SOUR{ch}:FUNC:SHAP SQU"),
        ("pulse_hold", "DUTY", "SOUR{ch}:PULS:HOLD DUTY"),
        ("output_enabled", True, "OUTP{ch}:STAT 1"),
        ("output_enabled", False, "OUTP{ch}:STAT 0"),
    ],
)
def test_setter_writes_command(ch, prop, value, command):
    with expected_protocol(FakeAFG, [CLEAR, (command.format(ch=ch), None)]) as afg:
        setattr(afg.channels[ch], prop, value)


@pytest.mark.parametrize("ch", [1, 2])
@pytest.mark.parametrize(
    ("prop", "command", "reply", "value"),
    [
        ("frequency", "SOUR{ch}:FREQ?", "10.0000001000e6", 10000000.1),
        ("amplitude", "SOUR{ch}:VOLT?", "500.0e-3", 0.5),
        ("offset", "SOUR{ch}:VOLT:OFFS?", "250e-3", 0.25),
        ("amplitude_high", "SOUR{ch}:VOLT:HIGH?", "500e-3", 0.5),
        ("amplitude_low", "SOUR{ch}:VOLT:LOW?", "0.0E+0", 0.0),
        ("pulse_dutycycle", "SOUR{ch}:PULS:DCYC?", "50.000", 50.0),
        ("pulse_leading", "SOUR{ch}:PULS:TRAN:LEAD?", "6.00e-9", 0.006),
        ("pulse_trailing", "SOUR{ch}:PULS:TRAN:TRA?", "625.0e0", 625e6),
    ],
)
def test_numeric_getter(ch, prop, command, reply, value):
    with expected_protocol(FakeAFG, [CLEAR, (command.format(ch=ch), reply)]) as afg:
        assert getattr(afg.channels[ch], prop) == pytest.approx(value, rel=1e-12)


@pytest.mark.parametrize("ch", [1, 2])
@pytest.mark.parametrize(
    ("prop", "command", "reply", "value"),
    [
        ("shape", "SOUR{ch}:FUNC:SHAP?", "PULS", "PULS"),
        ("amplitude_unit", "SOUR{ch}:VOLT:UNIT?", "DBM", "DBM"),
        ("pulse_hold", "SOUR{ch}:PULS:HOLD?", "WIDT", "WIDT"),
        ("output_enabled", "OUTP{ch}:STAT?", "0", False),
        ("output_enabled", "OUTP{ch}:STAT?", "1", True),
    ],
)
def test_getter(ch, prop, command, reply, value):
    with expected_protocol(FakeAFG, [CLEAR, (command.format(ch=ch), reply)]) as afg:
        assert getattr(afg.channels[ch], prop) == value


@pytest.mark.parametrize(
    ("prop", "value"),
    [
        ("shape", "SINC"),
        ("amplitude_unit", "DBMV"),
        ("pulse_hold", "WIDTH"),
        ("frequency", 0.0),
        ("frequency", 10.1e6),
        ("offset", 5.001),
        ("amplitude_high", -5.001),
        ("amplitude_low", 5.001),
        ("pulse_dutycycle", 0.0),
        ("pulse_dutycycle", 100.0),
        ("pulse_leading", 0.009),
        ("pulse_trailing", 1.1e6),
    ],
)
def test_out_of_range_value_raises_without_writing(prop, value):
    with expected_protocol(FakeAFG, [CLEAR]) as afg, pytest.raises(ValueError):
        setattr(afg.ch1, prop, value)


def test_instrument_error_raises():
    message = '"Data out of range; parameter out of range - SOUR1:VOLT 30"'
    with (
        expected_protocol(
            FakeAFG,
            [CLEAR, ("SOUR1:VOLT 30", None), ("SYST:ERR?", f"-222,{message}"), NO_ERROR],
        ) as afg,
        pytest.raises(ValueError, match=re.escape(f"rejected the setting: -222, {message}")),
    ):
        afg.ch1.amplitude = 30


def test_instrument_error_message_keeps_commas_and_lists_every_error():
    conflict = '"Settings conflict;  - SOUR2:VOLT:UNIT DBM"'
    with_commas = '"Data out of range, 10, see manual"'
    expected = f"rejected the setting: -221, {conflict}; -222, {with_commas}"
    with (
        expected_protocol(
            FakeAFG,
            [
                CLEAR,
                ("SOUR2:VOLT:UNIT DBM", None),
                ("SYST:ERR?", f"-221,{conflict}"),
                ("SYST:ERR?", f"-222,{with_commas}"),
                NO_ERROR,
            ],
        ) as afg,
        pytest.raises(ValueError, match=re.escape(expected)),
    ):
        afg.ch2.amplitude_unit = "DBM"


def test_model_channel_missing_ranges_raises():
    with pytest.raises(TypeError, match="must define offset_values, pulse_trailing_values"):

        class IncompleteChannel(TektronixAFG31000Channel):
            frequency_values = (1e-6, 1e6)
            amplitude_high_values = (-1.0, 1.0)
            amplitude_low_values = (-1.0, 1.0)
            pulse_leading_values = (0.01, 1.0)
