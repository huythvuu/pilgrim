"""Protocol tests for the Tektronix MSO4/5/6 base class."""

import logging
import re

import pytest
from pymeasure.test import expected_protocol

from pilgrim.instruments import TektronixMSO58B, TektronixMSO456

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
