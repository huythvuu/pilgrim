"""Protocol tests for the Tektronix MSO4/5/6 base class."""

import logging
import re

import pytest
from pymeasure.test import expected_protocol

from pilgrim.instruments import TektronixMSO58B, TektronixMSO456, TektronixMSO456Channel

CONNECT = [("HEADer OFF", None), ("VERBose ON", None), ("*CLS", None)]
NO_EVENT = [("*ESR?", "0"), ("ALLEv?", '0,"No events to report; queue empty"')]
LOGGER = "pilgrim.instruments.tektronix.mso456"


def events(reply: str, status: str = "16") -> list[tuple[str, str]]:
    """Build the protocol of one event-queue read that returns ``reply``.

    :param reply: The raw reply to ``ALLEv?``.
    :param status: The raw reply to ``*ESR?``, whose value the driver ignores.
    :returns: The command and reply pairs for ``expected_protocol``.
    """
    return [("*ESR?", status), ("ALLEv?", reply)]


def test_init_configures_replies_and_clears_event_queue():
    with expected_protocol(TektronixMSO456, CONNECT) as mso:
        assert mso.name == "Tektronix MSO456"


def test_mso58b_init():
    with expected_protocol(TektronixMSO58B, CONNECT) as mso:
        assert mso.name == "Tektronix MSO58B"
        assert isinstance(mso, TektronixMSO456)


def test_next_error_is_not_supported():
    with (
        expected_protocol(TektronixMSO456, CONNECT) as mso,
        pytest.raises(NotImplementedError, match=r"no SYST:ERR\? query; use check_errors"),
    ):
        _ = mso.next_error


def test_check_errors_with_empty_queue():
    with expected_protocol(TektronixMSO456, [*CONNECT, *NO_EVENT]) as mso:
        assert mso.check_errors() == []


def test_check_errors_treats_pending_events_as_none():
    reply = '1,"No events to report; new events pending *ESR?"'
    with expected_protocol(TektronixMSO456, [*CONNECT, *events(reply, "0")]) as mso:
        assert mso.check_errors() == []


def test_check_errors_returns_every_event_in_order_and_logs_it(caplog):
    reply = (
        '2225,"Measurement error, Waveform mismatch",420,"Query UNTERMINATED",'
        '540,"Measurement warning, Low signal amplitude"'
    )
    with (
        expected_protocol(TektronixMSO456, [*CONNECT, *events(reply)]) as mso,
        caplog.at_level(logging.WARNING, logger=LOGGER),
    ):
        assert mso.check_errors() == [
            (2225, "Measurement error, Waveform mismatch"),
            (420, "Query UNTERMINATED"),
            (540, "Measurement warning, Low signal amplitude"),
        ]
    assert [(r.levelname, r.message) for r in caplog.records] == [
        ("ERROR", "Tektronix MSO456: 2225, Measurement error, Waveform mismatch"),
        ("ERROR", "Tektronix MSO456: 420, Query UNTERMINATED"),
        ("WARNING", "Tektronix MSO456: 540, Measurement warning, Low signal amplitude"),
    ]


def test_check_errors_copes_with_header_and_quotes_in_message():
    reply = ':ALLEV 151,"Invalid string data; MEASTAB:ADDNEW "FOO""'
    with expected_protocol(TektronixMSO456, [*CONNECT, *events(reply)]) as mso:
        assert mso.check_errors() == [(151, 'Invalid string data; MEASTAB:ADDNEW "FOO"')]


def test_check_set_errors_with_empty_queue():
    with expected_protocol(TektronixMSO456, [*CONNECT, *NO_EVENT]) as mso:
        assert mso.check_set_errors() == []


def test_check_set_errors_raises_on_error():
    message = '"Data out of range; ACQUIRE:SEQUENCE:NUMSEQUENCE 0"'
    with (
        expected_protocol(TektronixMSO456, [*CONNECT, *events(f"222,{message}")]) as mso,
        pytest.raises(ValueError, match=re.escape(f"rejected the setting: 222, {message[1:-1]}")),
    ):
        mso.check_set_errors()


def test_check_set_errors_lists_every_error_but_no_warning():
    reply = (
        '540,"Measurement warning, Low signal amplitude",221,"Settings conflict",'
        '222,"Data out of range, see manual"'
    )
    expected = "rejected the setting: 221, Settings conflict; 222, Data out of range, see manual"
    with (
        expected_protocol(TektronixMSO456, [*CONNECT, *events(reply)]) as mso,
        pytest.raises(ValueError, match=re.escape(expected)),
    ):
        mso.check_set_errors()


def test_check_set_errors_returns_warnings_without_raising(caplog):
    reply = '528,"Parameter out of range",630,"Internal warning, 50 overload"'
    with (
        expected_protocol(TektronixMSO456, [*CONNECT, *events(reply)]) as mso,
        caplog.at_level(logging.WARNING, logger=LOGGER),
    ):
        assert mso.check_set_errors() == [
            (528, "Parameter out of range"),
            (630, "Internal warning, 50 overload"),
        ]
    assert [r.levelname for r in caplog.records] == ["WARNING", "WARNING"]


@pytest.mark.parametrize(
    ("prop", "value", "command"),
    [
        ("acquisition_enabled", True, "ACQuire:STATE 1"),
        ("acquisition_enabled", False, "ACQuire:STATE 0"),
        ("stop_after", "RUNSTOP", "ACQuire:STOPAfter RUNSTOP"),
        ("stop_after", "SEQUENCE", "ACQuire:STOPAfter SEQUENCE"),
        ("sequence_count", 1, "ACQuire:SEQuence:NUMSEQuence 1"),
        ("sequence_count", 300, "ACQuire:SEQuence:NUMSEQuence 300"),
        ("trigger_type", "EDGE", "TRIGger:A:TYPe EDGE"),
        ("trigger_type", "TRANSITION", "TRIGger:A:TYPe TRANSITION"),
        ("trigger_source", "CH1", "TRIGger:A:EDGE:SOUrce CH1"),
        ("trigger_source", "CH4", "TRIGger:A:EDGE:SOUrce CH4"),
        ("trigger_source", "LINE", "TRIGger:A:EDGE:SOUrce LINE"),
        ("trigger_source", "AUXILIARY", "TRIGger:A:EDGE:SOUrce AUXILIARY"),
        ("trigger_slope", "RISE", "TRIGger:A:EDGE:SLOpe RISE"),
        ("trigger_slope", "EITHER", "TRIGger:A:EDGE:SLOpe EITHER"),
        ("trigger_coupling", "DC", "TRIGger:A:EDGE:COUPling DC"),
        ("trigger_coupling", "NOISEREJ", "TRIGger:A:EDGE:COUPling NOISEREJ"),
        ("trigger_mode", "AUTO", "TRIGger:A:MODe AUTO"),
        ("trigger_mode", "NORMAL", "TRIGger:A:MODe NORMAL"),
        ("trigger_holdoff_type", "TIME", "TRIGger:A:HOLDoff:BY TIME"),
        ("trigger_holdoff_type", "RANDOM", "TRIGger:A:HOLDoff:BY RANDOM"),
        ("trigger_holdoff_time", 0, "TRIGger:A:HOLDoff:TIMe 0"),
        ("trigger_holdoff_time", 1.2e-6, "TRIGger:A:HOLDoff:TIMe 1.2e-06"),
        ("trigger_holdoff_time", 10, "TRIGger:A:HOLDoff:TIMe 10"),
        ("persistence", "OFF", "DISplay:PERSistence OFF"),
        ("persistence", "AUTO", "DISplay:PERSistence AUTO"),
        ("persistence", "INFPERSIST", "DISplay:PERSistence INFPERSIST"),
        ("persistence", "VARPERSIST", "DISplay:PERSistence VARPERSIST"),
        ("persistence_decay_time", 0.05, "DISplay:VARpersist 0.05"),
        ("persistence_decay_time", 2.5, "DISplay:VARpersist 2.5"),
        ("persistence_decay_time", 100, "DISplay:VARpersist 100"),
    ],
)
def test_setter_writes_command_then_checks_errors(prop, value, command):
    with expected_protocol(TektronixMSO456, [*CONNECT, (command, None), *NO_EVENT]) as mso:
        setattr(mso, prop, value)


@pytest.mark.parametrize(
    ("prop", "command", "reply", "value"),
    [
        ("acquisition_enabled", "ACQuire:STATE?", "0", False),
        ("acquisition_enabled", "ACQuire:STATE?", "1", True),
        ("acquisition_enabled", "ACQuire:STATE?", "OFF", False),
        ("acquisition_enabled", "ACQuire:STATE?", "ON", True),
        ("stop_after", "ACQuire:STOPAfter?", "RUNSTOP", "RUNSTOP"),
        ("stop_after", "ACQuire:STOPAfter?", "SEQUENCE", "SEQUENCE"),
        ("sequence_count", "ACQuire:SEQuence:NUMSEQuence?", "1", 1),
        ("acquisition_count", "ACQuire:NUMACq?", "350", 350),
        ("trigger_type", "TRIGger:A:TYPe?", "EDGE", "EDGE"),
        ("trigger_type", "TRIGger:A:TYPe?", "RUNT", "RUNT"),
        ("trigger_source", "TRIGger:A:EDGE:SOUrce?", "CH1", "CH1"),
        ("trigger_source", "TRIGger:A:EDGE:SOUrce?", "LINE", "LINE"),
        ("trigger_source", "TRIGger:A:EDGE:SOUrce?", "CH1_D0", "CH1_D0"),
        ("trigger_slope", "TRIGger:A:EDGE:SLOpe?", "FALL", "FALL"),
        ("trigger_coupling", "TRIGger:A:EDGE:COUPling?", "HFREJ", "HFREJ"),
        ("trigger_mode", "TRIGger:A:MODe?", "NORMAL", "NORMAL"),
        ("trigger_holdoff_type", "TRIGger:A:HOLDoff:BY?", "RANDOM", "RANDOM"),
        ("trigger_holdoff_time", "TRIGger:A:HOLDoff:TIMe?", "1.2000E-06", 1.2e-6),
        ("trigger_state", "TRIGger:STATE?", "READY", "READY"),
        ("trigger_state", "TRIGger:STATE?", "SAVE", "SAVE"),
        ("persistence", "DISplay:PERSistence?", "OFF", "OFF"),
        ("persistence", "DISplay:PERSistence?", "INFPERSIST", "INFPERSIST"),
        ("persistence", "DISplay:PERSistence?", "VARPERSIST", "VARPERSIST"),
        ("persistence_decay_time", "DISplay:VARpersist?", "500.0000E-3", 0.5),
        ("persistence_decay_time", "DISplay:VARpersist?", "100.0000", 100.0),
    ],
)
def test_getter(prop, command, reply, value):
    with expected_protocol(TektronixMSO456, [*CONNECT, (command, reply)]) as mso:
        result = getattr(mso, prop)
    assert result == value
    assert type(result) is type(value)


@pytest.mark.parametrize(
    ("prop", "value"),
    [
        ("acquisition_enabled", "RUN"),
        ("stop_after", "SINGLE"),
        ("sequence_count", 0),
        ("sequence_count", 1.5),
        ("trigger_type", "PULSE"),
        ("trigger_source", "CH5"),
        ("trigger_source", "ch1"),
        ("trigger_source", "CH1_D0"),
        ("trigger_slope", "POSITIVE"),
        ("trigger_coupling", "AC"),
        ("trigger_mode", "SINGLE"),
        ("trigger_holdoff_type", "AUTO"),
        ("trigger_holdoff_time", -0.1),
        ("trigger_holdoff_time", 10.5),
        ("persistence", "ON"),
        ("persistence", True),
        ("persistence", "INFP"),
        ("persistence_decay_time", 0.04),
        ("persistence_decay_time", 100.5),
    ],
)
def test_invalid_value_raises_without_writing(prop, value):
    with expected_protocol(TektronixMSO456, CONNECT) as mso, pytest.raises(ValueError):
        setattr(mso, prop, value)


def test_setter_raises_when_instrument_rejects_value():
    command = "ACQuire:SEQuence:NUMSEQuence 10000000000000"
    reply = '222,"Data out of range; ACQUIRE:SEQUENCE:NUMSEQUENCE 10000000000000"'
    with (
        expected_protocol(TektronixMSO456, [*CONNECT, (command, None), *events(reply)]) as mso,
        pytest.raises(ValueError, match="rejected the setting: 222, Data out of range"),
    ):
        mso.sequence_count = 10_000_000_000_000


def test_clear_acquisitions():
    with expected_protocol(TektronixMSO456, [*CONNECT, ("CLEAR", None)]) as mso:
        mso.clear_acquisitions()


def test_base_class_has_four_analog_channels():
    with expected_protocol(TektronixMSO456, CONNECT) as mso:
        assert list(mso.channels) == [1, 2, 3, 4]
        assert all(isinstance(ch, TektronixMSO456Channel) for ch in mso.channels.values())
        assert mso.ch1 is mso.channels[1]
        assert not hasattr(mso, "ch5")


def test_mso58b_has_eight_analog_channels():
    with expected_protocol(TektronixMSO58B, CONNECT) as mso:
        assert list(mso.channels) == [1, 2, 3, 4, 5, 6, 7, 8]
        assert isinstance(mso.ch8, TektronixMSO456Channel)
        assert mso.ch8 is mso.channels[8]


@pytest.mark.parametrize(
    ("channel", "prop", "value", "command"),
    [
        ("ch1", "display_enabled", True, "DISplay:WAVEView1:CH1:STATE 1"),
        ("ch4", "display_enabled", False, "DISplay:WAVEView1:CH4:STATE 0"),
        ("ch1", "global_display_enabled", False, "DISplay:GLObal:CH1:STATE 0"),
        ("ch4", "global_display_enabled", True, "DISplay:GLObal:CH4:STATE 1"),
        ("ch1", "trigger_level", 0.25, "TRIGger:A:LEVel:CH1 0.25"),
        ("ch4", "trigger_level", -1.5, "TRIGger:A:LEVel:CH4 -1.5"),
        ("ch4", "trigger_level", 1.23456789123e-3, "TRIGger:A:LEVel:CH4 0.00123456789123"),
    ],
)
def test_channel_setter_writes_command_then_checks_errors(channel, prop, value, command):
    with expected_protocol(TektronixMSO456, [*CONNECT, (command, None), *NO_EVENT]) as mso:
        setattr(getattr(mso, channel), prop, value)


def test_mso58b_channel_setter_substitutes_its_number():
    command = "DISplay:WAVEView1:CH8:STATE 1"
    with expected_protocol(TektronixMSO58B, [*CONNECT, (command, None), *NO_EVENT]) as mso:
        mso.ch8.display_enabled = True


@pytest.mark.parametrize(
    ("channel", "prop", "command", "reply", "value"),
    [
        ("ch1", "display_enabled", "DISplay:WAVEView1:CH1:STATE?", "0", False),
        ("ch4", "display_enabled", "DISplay:WAVEView1:CH4:STATE?", "1", True),
        ("ch1", "display_enabled", "DISplay:WAVEView1:CH1:STATE?", "OFF", False),
        ("ch1", "display_enabled", "DISplay:WAVEView1:CH1:STATE?", "ON", True),
        ("ch1", "global_display_enabled", "DISplay:GLObal:CH1:STATE?", "1", True),
        ("ch4", "global_display_enabled", "DISplay:GLObal:CH4:STATE?", "0", False),
        ("ch4", "global_display_enabled", "DISplay:GLObal:CH4:STATE?", "ON", True),
        ("ch4", "global_display_enabled", "DISplay:GLObal:CH4:STATE?", "OFF", False),
        ("ch1", "trigger_level", "TRIGger:A:LEVel:CH1?", "1.3000E+00", 1.3),
        ("ch4", "trigger_level", "TRIGger:A:LEVel:CH4?", "-250.0000E-3", -0.25),
    ],
)
def test_channel_getter(channel, prop, command, reply, value):
    with expected_protocol(TektronixMSO456, [*CONNECT, (command, reply)]) as mso:
        result = getattr(getattr(mso, channel), prop)
    assert result == value
    assert type(result) is type(value)


@pytest.mark.parametrize(
    ("prop", "value"),
    [
        ("display_enabled", "ON"),
        ("display_enabled", 2),
        ("global_display_enabled", "OFF"),
    ],
)
def test_channel_invalid_value_raises_without_writing(prop, value):
    with expected_protocol(TektronixMSO456, CONNECT) as mso, pytest.raises(ValueError):
        setattr(mso.ch1, prop, value)


def test_channel_setter_raises_when_instrument_rejects_value():
    command = "DISplay:GLObal:CH1:STATE 1"
    reply = '221,"Settings conflict; DISPLAY:GLOBAL:CH1:STATE 1"'
    with (
        expected_protocol(TektronixMSO456, [*CONNECT, (command, None), *events(reply)]) as mso,
        pytest.raises(ValueError, match="rejected the setting: 221, Settings conflict"),
    ):
        mso.ch1.global_display_enabled = True


def test_mso58b_trigger_source_accepts_its_eight_channels():
    command = "TRIGger:A:EDGE:SOUrce CH8"
    with expected_protocol(TektronixMSO58B, [*CONNECT, (command, None), *NO_EVENT]) as mso:
        mso.trigger_source = "CH8"


def test_mso58b_trigger_source_rejects_a_ninth_channel_without_writing():
    with expected_protocol(TektronixMSO58B, CONNECT) as mso, pytest.raises(ValueError):
        mso.trigger_source = "CH9"


def test_trigger_setter_raises_when_instrument_rejects_value():
    command = "TRIGger:A:EDGE:SOUrce AUXILIARY"
    reply = '221,"Settings conflict; TRIGGER:A:EDGE:SOURCE AUXILIARY"'
    with (
        expected_protocol(TektronixMSO456, [*CONNECT, (command, None), *events(reply)]) as mso,
        pytest.raises(ValueError, match="rejected the setting: 221, Settings conflict"),
    ):
        mso.trigger_source = "AUXILIARY"


def test_force_trigger():
    with expected_protocol(TektronixMSO456, [*CONNECT, ("TRIGger FORCe", None)]) as mso:
        mso.force_trigger()


def test_set_trigger_level_to_midpoint():
    with expected_protocol(TektronixMSO456, [*CONNECT, ("TRIGger:A SETLevel", None)]) as mso:
        mso.set_trigger_level_to_midpoint()


def test_reset_persistence():
    with expected_protocol(TektronixMSO456, [*CONNECT, ("DISplay:PERSistence:RESET", None)]) as mso:
        mso.reset_persistence()
