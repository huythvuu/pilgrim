"""Protocol tests for the Tektronix MSO4/5/6 base class."""

import logging
import math
import re
from functools import partial
from typing import NoReturn

import pytest
from pymeasure.test import expected_protocol
from pyvisa import VisaIOError
from pyvisa.constants import StatusCode

from pilgrim.instruments import (
    TektronixMSO58B,
    TektronixMSO456,
    TektronixMSO456Channel,
    TektronixMSO456Measurement,
)

CONNECT = [("HEADer OFF", None), ("VERBose ON", None), ("*CLS", None)]
NO_EVENT = [("*ESR?", "0"), ("ALLEv?", '0,"No events to report; queue empty"')]
LOGGER = "pilgrim.instruments.tektronix.mso456"
REFLEVELS = "MEASUrement:REFLevels"


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
        ("reference_level_method", "PERCENT", f"{REFLEVELS}:METHod PERCENT"),
        ("reference_level_method", "ABSOLUTE", f"{REFLEVELS}:METHod ABSOLUTE"),
        ("reference_level_mode", "LATCH", f"{REFLEVELS}:MODE LATCH"),
        ("reference_level_mode", "CONTINUOUS", f"{REFLEVELS}:MODE CONTINUOUS"),
        ("reference_level_scope", "GLOBAL", f"{REFLEVELS}:TYPE GLOBAL"),
        ("reference_level_scope", "PERSOURCE", f"{REFLEVELS}:TYPE PERSOURCE"),
        ("reference_level_percent_preset", "TENNINETY", f"{REFLEVELS}:PERCent:TYPE TENNINETY"),
        (
            "reference_level_percent_preset",
            "TWENTYEIGHTY",
            f"{REFLEVELS}:PERCent:TYPE TWENTYEIGHTY",
        ),
        ("reference_level_percent_preset", "CUSTOM", f"{REFLEVELS}:PERCent:TYPE CUSTOM"),
        ("reference_level_percent_rise_high", 90, f"{REFLEVELS}:PERCent:RISEHigh 90"),
        ("reference_level_percent_rise_mid", 50.5, f"{REFLEVELS}:PERCent:RISEMid 50.5"),
        ("reference_level_percent_rise_low", 0, f"{REFLEVELS}:PERCent:RISELow 0"),
        ("reference_level_percent_fall_high", 100, f"{REFLEVELS}:PERCent:FALLHigh 100"),
        ("reference_level_percent_fall_mid", 45, f"{REFLEVELS}:PERCent:FALLMid 45"),
        ("reference_level_percent_fall_low", 12.5, f"{REFLEVELS}:PERCent:FALLLow 12.5"),
        ("reference_level_absolute_rise_high", 1.5, f"{REFLEVELS}:ABSolute:RISEHigh 1.5"),
        ("reference_level_absolute_rise_mid", 0.01, f"{REFLEVELS}:ABSolute:RISEMid 0.01"),
        ("reference_level_absolute_rise_low", -1.5, f"{REFLEVELS}:ABSolute:RISELow -1.5"),
        ("reference_level_absolute_fall_high", 2, f"{REFLEVELS}:ABSolute:FALLHigh 2"),
        ("reference_level_absolute_fall_mid", 0, f"{REFLEVELS}:ABSolute:FALLMid 0"),
        ("reference_level_absolute_fall_low", -1.25e-3, f"{REFLEVELS}:ABSolute:FALLLow -0.00125"),
        ("cycle_statistics_enabled", True, "MEASUrement:STATIstics:CYCLEMode 1"),
        ("cycle_statistics_enabled", False, "MEASUrement:STATIstics:CYCLEMode 0"),
        ("image_color_mode", "NORMAL", "SAVe:IMAGe:COMPosition NORMAL"),
        ("image_color_mode", "INVERTED", "SAVe:IMAGe:COMPosition INVERTED"),
        ("working_directory", "C:/pilgrim_test", 'FILESystem:CWD "C:/pilgrim_test"'),
        ("working_directory", "E:/", 'FILESystem:CWD "E:/"'),
        ("working_directory", "Saved, Configs", 'FILESystem:CWD "Saved, Configs"'),
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
        ("reference_level_method", f"{REFLEVELS}:METHod?", "PERCENT", "PERCENT"),
        ("reference_level_method", f"{REFLEVELS}:METHod?", "ABSOLUTE", "ABSOLUTE"),
        ("reference_level_mode", f"{REFLEVELS}:MODE?", "LATCH", "LATCH"),
        ("reference_level_scope", f"{REFLEVELS}:TYPE?", "PERSOURCE", "PERSOURCE"),
        ("reference_level_percent_preset", f"{REFLEVELS}:PERCent:TYPE?", "CUSTOM", "CUSTOM"),
        ("reference_level_percent_rise_high", f"{REFLEVELS}:PERCent:RISEHigh?", "80.0000", 80.0),
        ("reference_level_percent_rise_mid", f"{REFLEVELS}:PERCent:RISEMid?", "50.0000", 50.0),
        ("reference_level_percent_rise_low", f"{REFLEVELS}:PERCent:RISELow?", "20.0000", 20.0),
        ("reference_level_percent_fall_high", f"{REFLEVELS}:PERCent:FALLHigh?", "90.0000", 90.0),
        ("reference_level_percent_fall_mid", f"{REFLEVELS}:PERCent:FALLMid?", "55.5000", 55.5),
        ("reference_level_percent_fall_low", f"{REFLEVELS}:PERCent:FALLLow?", "10.0000", 10.0),
        ("reference_level_absolute_rise_high", f"{REFLEVELS}:ABSolute:RISEHigh?", "1.0000", 1.0),
        ("reference_level_absolute_rise_mid", f"{REFLEVELS}:ABSolute:RISEMid?", "0.0E+0", 0.0),
        ("reference_level_absolute_rise_low", f"{REFLEVELS}:ABSolute:RISELow?", "-1.0000", -1.0),
        ("reference_level_absolute_fall_high", f"{REFLEVELS}:ABSolute:FALLHigh?", "1.5000", 1.5),
        ("reference_level_absolute_fall_mid", f"{REFLEVELS}:ABSolute:FALLMid?", "30.0E-3", 0.03),
        ("reference_level_absolute_fall_low", f"{REFLEVELS}:ABSolute:FALLLow?", "-1.5000", -1.5),
        ("cycle_statistics_enabled", "MEASUrement:STATIstics:CYCLEMode?", "0", False),
        ("cycle_statistics_enabled", "MEASUrement:STATIstics:CYCLEMode?", "1", True),
        ("cycle_statistics_enabled", "MEASUrement:STATIstics:CYCLEMode?", "OFF", False),
        ("cycle_statistics_enabled", "MEASUrement:STATIstics:CYCLEMode?", "ON", True),
        ("image_color_mode", "SAVe:IMAGe:COMPosition?", "NORMAL", "NORMAL"),
        ("image_color_mode", "SAVe:IMAGe:COMPosition?", "INVERTED", "INVERTED"),
        ("working_directory", "FILESystem:CWD?", '"C:/pilgrim_test"', "C:/pilgrim_test"),
        ("working_directory", "FILESystem:CWD?", '"C:/Saved, Configs"', "C:/Saved, Configs"),
        ("directory_listing", "FILESystem:DIR?", '""', []),
        ("directory_listing", "FILESystem:DIR?", '"shot.png"', ["shot.png"]),
        (
            "directory_listing",
            "FILESystem:DIR?",
            '"a.png","Saved Configs","b,c.set","E:"',
            ["a.png", "Saved Configs", "b,c.set", "E:"],
        ),
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
        ("reference_level_method", "PERC"),
        ("reference_level_method", "percent"),
        ("reference_level_mode", "AUTO"),
        ("reference_level_scope", "LOCAL"),
        ("reference_level_percent_preset", "TENNINE"),
        ("reference_level_percent_rise_high", -0.1),
        ("reference_level_percent_rise_high", 100.1),
        ("reference_level_percent_fall_low", -1),
        ("reference_level_percent_fall_mid", 101),
        ("cycle_statistics_enabled", "ON"),
        ("cycle_statistics_enabled", 2),
        ("image_color_mode", "NORMal"),
        ("image_color_mode", "ON"),
        ("working_directory", ""),
        ("working_directory", 'C:/"quoted"'),
        ("working_directory", 1),
        ("working_directory", None),
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


ADD_MEAS1 = [('MEASUrement:ADDNew "MEAS1"', None), *NO_EVENT]
RESULT_QUERIES = {
    "current_mean": "MEASUrement:MEAS1:RESUlts:CURRentacq:MEAN?",
    "current_minimum": "MEASUrement:MEAS1:RESUlts:CURRentacq:MINimum?",
    "current_maximum": "MEASUrement:MEAS1:RESUlts:CURRentacq:MAXimum?",
    "current_peak_to_peak": "MEASUrement:MEAS1:RESUlts:CURRentacq:PK2PK?",
    "current_standard_deviation": "MEASUrement:MEAS1:RESUlts:CURRentacq:STDDev?",
    "mean": "MEASUrement:MEAS1:RESUlts:ALLAcqs:MEAN?",
    "minimum": "MEASUrement:MEAS1:RESUlts:ALLAcqs:MINimum?",
    "maximum": "MEASUrement:MEAS1:RESUlts:ALLAcqs:MAXimum?",
    "peak_to_peak": "MEASUrement:MEAS1:RESUlts:ALLAcqs:PK2PK?",
    "standard_deviation": "MEASUrement:MEAS1:RESUlts:ALLAcqs:STDDev?",
}


def test_measurements_collection_starts_empty():
    with expected_protocol(TektronixMSO456, CONNECT) as mso:
        assert mso.measurements == {}


def test_measurement_types_include_those_of_the_transient_procedure():
    needed = {"MEAN", "MINIMUM", "MAXIMUM", "TOP", "BASE", "PK2PK", "RISETIME", "FALLTIME"}
    assert needed <= set(TektronixMSO456Measurement.TYPES)


def test_add_measurement_sends_command_then_checks_errors_and_returns_child():
    with expected_protocol(TektronixMSO456, [*CONNECT, *ADD_MEAS1]) as mso:
        measurement = mso.add_measurement(1)
        assert isinstance(measurement, TektronixMSO456Measurement)
        assert measurement.id == 1
        assert measurement.parent is mso
        assert mso.measurements == {1: measurement}
        assert getattr(mso, f"meas_{measurement.id}") is measurement


def test_add_measurement_keeps_earlier_measurements():
    protocol = [*CONNECT, *ADD_MEAS1, ('MEASUrement:ADDNew "MEAS12"', None), *NO_EVENT]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        first = mso.add_measurement(1)
        second = mso.add_measurement(12)
        assert mso.measurements == {1: first, 12: second}
        assert getattr(mso, f"meas_{second.id}") is second


@pytest.mark.parametrize("number", [0, -1, 1.0, 1.5, "1", "MEAS1"])
def test_add_measurement_rejects_invalid_number_without_writing(number):
    with (
        expected_protocol(TektronixMSO456, CONNECT) as mso,
        pytest.raises(ValueError, match="1 or more"),
    ):
        mso.add_measurement(number)


def test_add_measurement_raises_when_instrument_refuses_and_adds_no_child():
    reply = '221,"Settings conflict; Operation not allowed at this time"'
    protocol = [*CONNECT, ('MEASUrement:ADDNew "MEAS1"', None), *events(reply)]
    with (
        expected_protocol(TektronixMSO456, protocol) as mso,
        pytest.raises(ValueError, match="rejected adding measurement 1: 221, Settings conflict"),
    ):
        mso.add_measurement(1)
    assert mso.measurements == {}
    assert not hasattr(mso, "meas_1")


def test_delete_measurement_sends_command_then_checks_errors_and_removes_child():
    protocol = [*CONNECT, *ADD_MEAS1, ('MEASUrement:DELete "MEAS1"', None), *NO_EVENT]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        measurement = mso.add_measurement(1)
        mso.delete_measurement(measurement)
        assert mso.measurements == {}
        assert not hasattr(mso, "meas_1")


def test_delete_measurement_keeps_the_other_measurements():
    protocol = [
        *CONNECT,
        *ADD_MEAS1,
        ('MEASUrement:ADDNew "MEAS2"', None),
        *NO_EVENT,
        ('MEASUrement:DELete "MEAS2"', None),
        *NO_EVENT,
    ]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        first = mso.add_measurement(1)
        mso.delete_measurement(mso.add_measurement(2))
        assert mso.measurements == {1: first}
        assert not hasattr(mso, "meas_2")


def test_delete_measurement_raises_when_instrument_refuses_and_keeps_child():
    reply = '100,"Command error; Measurement MEAS1 does not exist"'
    protocol = [*CONNECT, *ADD_MEAS1, ('MEASUrement:DELete "MEAS1"', None), *events(reply)]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        measurement = mso.add_measurement(1)
        with pytest.raises(ValueError, match="rejected deleting measurement 1: 100, Command error"):
            mso.delete_measurement(measurement)
        assert mso.measurements == {1: measurement}


def test_delete_all_measurements_empties_the_collection():
    protocol = [
        *CONNECT,
        *ADD_MEAS1,
        ('MEASUrement:ADDNew "MEAS2"', None),
        *NO_EVENT,
        ("MEASUrement:DELETEALL", None),
        *NO_EVENT,
    ]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        mso.add_measurement(1)
        mso.add_measurement(2)
        mso.delete_all_measurements()
        assert mso.measurements == {}
        assert not hasattr(mso, "meas_1")
        assert not hasattr(mso, "meas_2")


def test_delete_all_measurements_with_none_added():
    protocol = [*CONNECT, ("MEASUrement:DELETEALL", None), *NO_EVENT]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        mso.delete_all_measurements()
        assert mso.measurements == {}


def test_delete_all_measurements_raises_when_instrument_refuses_and_keeps_children():
    reply = '221,"Settings conflict; Operation not allowed at this time"'
    protocol = [*CONNECT, *ADD_MEAS1, ("MEASUrement:DELETEALL", None), *events(reply)]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        measurement = mso.add_measurement(1)
        with pytest.raises(ValueError, match="rejected deleting every measurement: 221"):
            mso.delete_all_measurements()
        assert mso.measurements == {1: measurement}


@pytest.mark.parametrize(
    ("reply", "names"),
    [
        ("NONE", []),
        ("MEAS1", ["MEAS1"]),
        ("MEAS1,MEAS2,MEAS7", ["MEAS1", "MEAS2", "MEAS7"]),
    ],
)
def test_measurement_names(reply, names):
    with expected_protocol(TektronixMSO456, [*CONNECT, ("MEASUrement:LIST?", reply)]) as mso:
        assert mso.measurement_names == names


@pytest.mark.parametrize("name", ["TABLE1", "TABLE12"])
def test_add_results_table_sends_command_then_checks_errors(name):
    protocol = [*CONNECT, (f'MEASTABle:ADDNew "{name}"', None), *NO_EVENT]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        mso.add_results_table(name)


@pytest.mark.parametrize("name", ["TABLE1", "TABLE12"])
def test_delete_results_table_sends_command_then_checks_errors(name):
    protocol = [*CONNECT, (f'MEASTABle:DELETE "{name}"', None), *NO_EVENT]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        mso.delete_results_table(name)


INVALID_TABLE_NAMES = ["TABLE0", "TABLE", "TABLE1.5", "table1", "MEAS1", "FOO", "", 1, None]


@pytest.mark.parametrize("name", INVALID_TABLE_NAMES)
def test_add_results_table_rejects_invalid_name_without_writing(name):
    with (
        expected_protocol(TektronixMSO456, CONNECT) as mso,
        pytest.raises(ValueError, match="must be TABLE<n>"),
    ):
        mso.add_results_table(name)


@pytest.mark.parametrize("name", INVALID_TABLE_NAMES)
def test_delete_results_table_rejects_invalid_name_without_writing(name):
    with (
        expected_protocol(TektronixMSO456, CONNECT) as mso,
        pytest.raises(ValueError, match="must be TABLE<n>"),
    ):
        mso.delete_results_table(name)


def test_add_results_table_raises_when_instrument_refuses():
    reply = '100,"Command error; MEASTABLE:ADDNEW ""TABLE1"""'
    protocol = [*CONNECT, ('MEASTABle:ADDNew "TABLE1"', None), *events(reply)]
    with (
        expected_protocol(TektronixMSO456, protocol) as mso,
        pytest.raises(ValueError, match="rejected adding results table TABLE1: 100, Command error"),
    ):
        mso.add_results_table("TABLE1")


def test_delete_results_table_raises_when_instrument_refuses():
    reply = '100,"Command error; MEASTABLE:DELETE ""TABLE1"""'
    protocol = [*CONNECT, ('MEASTABle:DELETE "TABLE1"', None), *events(reply)]
    expected = "rejected deleting results table TABLE1: 100, Command error"
    with (
        expected_protocol(TektronixMSO456, protocol) as mso,
        pytest.raises(ValueError, match=expected),
    ):
        mso.delete_results_table("TABLE1")


@pytest.mark.parametrize(
    ("prop", "value", "command"),
    [
        ("type", "MEAN", "MEASUrement:MEAS1:TYPe MEAN"),
        ("type", "PK2PK", "MEASUrement:MEAS1:TYPe PK2PK"),
        ("type", "RISETIME", "MEASUrement:MEAS1:TYPe RISETIME"),
        ("source", "CH1", "MEASUrement:MEAS1:SOURCE CH1"),
        ("source", "CH4", "MEASUrement:MEAS1:SOURCE CH4"),
        ("source", "MATH1", "MEASUrement:MEAS1:SOURCE MATH1"),
        ("source", "REF12", "MEASUrement:MEAS1:SOURCE REF12"),
        ("label", "Rise", 'MEASUrement:MEAS1:LABel "Rise"'),
        ("label", "", 'MEASUrement:MEAS1:LABel ""'),
        ("population_limit_enabled", True, "MEASUrement:MEAS1:POPUlation:LIMIT:STATE 1"),
        ("population_limit_enabled", False, "MEASUrement:MEAS1:POPUlation:LIMIT:STATE 0"),
        ("population_limit", 1, "MEASUrement:MEAS1:POPUlation:LIMIT:VALue 1"),
        ("population_limit", 300, "MEASUrement:MEAS1:POPUlation:LIMIT:VALue 300"),
        ("uses_global_population_limit", True, "MEASUrement:MEAS1:POPUlation:GLOBal 1"),
        ("uses_global_population_limit", False, "MEASUrement:MEAS1:POPUlation:GLOBal 0"),
        ("badge_statistics_enabled", True, "MEASUrement:MEAS1:DISPlaystat:ENABle 1"),
        ("badge_statistics_enabled", False, "MEASUrement:MEAS1:DISPlaystat:ENABle 0"),
    ],
)
def test_measurement_setter_writes_command_then_checks_errors(prop, value, command):
    protocol = [*CONNECT, *ADD_MEAS1, (command, None), *NO_EVENT]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        setattr(mso.add_measurement(1), prop, value)


def test_measurement_setter_substitutes_its_number():
    protocol = [
        *CONNECT,
        ('MEASUrement:ADDNew "MEAS12"', None),
        *NO_EVENT,
        ("MEASUrement:MEAS12:TYPe FALLTIME", None),
        *NO_EVENT,
    ]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        mso.add_measurement(12).type = "FALLTIME"


def test_label_round_trips_percent_quotes_and_non_ascii_text():
    label = 'Rise 50% "µs", fast'
    encoded = "Rise%2050%25%20%22%C2%B5s%22%2C%20fast"
    protocol = [
        *CONNECT,
        *ADD_MEAS1,
        (f'MEASUrement:MEAS1:LABel "{encoded}"', None),
        *NO_EVENT,
        ("MEASUrement:MEAS1:LABel?", f'"{encoded}"'),
    ]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        measurement = mso.add_measurement(1)
        measurement.label = label
        assert measurement.label == label


@pytest.mark.parametrize(
    ("prop", "command", "reply", "value"),
    [
        ("type", "MEASUrement:MEAS1:TYPe?", "AMPLITUDE", "AMPLITUDE"),
        ("type", "MEASUrement:MEAS1:TYPe?", "PK2PK", "PK2PK"),
        ("source", "MEASUrement:MEAS1:SOURCE?", "CH2", "CH2"),
        ("source", "MEASUrement:MEAS1:SOURCE?", "MATH1", "MATH1"),
        ("label", "MEASUrement:MEAS1:LABel?", '"Rise"', "Rise"),
        ("label", "MEASUrement:MEAS1:LABel?", '""', ""),
        ("current_mean", RESULT_QUERIES["current_mean"], "2.2807617754647", 2.2807617754647),
        ("current_minimum", RESULT_QUERIES["current_minimum"], "2.260", 2.26),
        ("current_maximum", RESULT_QUERIES["current_maximum"], "2.280", 2.28),
        ("current_peak_to_peak", RESULT_QUERIES["current_peak_to_peak"], "0.0E+0", 0.0),
        ("current_population", "MEASUrement:MEAS1:RESUlts:CURRentacq:POPUlation?", "1", 1),
        ("current_population", "MEASUrement:MEAS1:RESUlts:CURRentacq:POPUlation?", "0", 0),
        ("current_standard_deviation", RESULT_QUERIES["current_standard_deviation"], "0.0E+0", 0.0),
        ("mean", RESULT_QUERIES["mean"], "2.2807617754647", 2.2807617754647),
        ("minimum", RESULT_QUERIES["minimum"], "2.220", 2.22),
        ("maximum", RESULT_QUERIES["maximum"], "2.420", 2.42),
        ("peak_to_peak", RESULT_QUERIES["peak_to_peak"], "200.0E-3", 0.2),
        ("population", "MEASUrement:MEAS1:RESUlts:ALLAcqs:POPUlation?", "29825", 29825),
        ("standard_deviation", RESULT_QUERIES["standard_deviation"], "1.2345E-3", 0.0012345),
        ("population_limit_enabled", "MEASUrement:MEAS1:POPUlation:LIMIT:STATE?", "1", True),
        ("population_limit_enabled", "MEASUrement:MEAS1:POPUlation:LIMIT:STATE?", "OFF", False),
        ("population_limit", "MEASUrement:MEAS1:POPUlation:LIMIT:VALue?", "1000", 1000),
        ("uses_global_population_limit", "MEASUrement:MEAS1:POPUlation:GLOBal?", "0", False),
        ("uses_global_population_limit", "MEASUrement:MEAS1:POPUlation:GLOBal?", "ON", True),
        ("badge_statistics_enabled", "MEASUrement:MEAS1:DISPlaystat:ENABle?", "1", True),
    ],
)
def test_measurement_getter(prop, command, reply, value):
    with expected_protocol(TektronixMSO456, [*CONNECT, *ADD_MEAS1, (command, reply)]) as mso:
        result = getattr(mso.add_measurement(1), prop)
    assert result == value
    assert type(result) is type(value)


@pytest.mark.parametrize("reply", ["9.91E+37", "9.9100E+37"])
@pytest.mark.parametrize(("prop", "command"), list(RESULT_QUERIES.items()))
def test_measurement_invalid_result_reads_as_nan(prop, command, reply):
    with expected_protocol(TektronixMSO456, [*CONNECT, *ADD_MEAS1, (command, reply)]) as mso:
        assert math.isnan(getattr(mso.add_measurement(1), prop))


def test_measurement_result_below_the_invalid_value_is_kept():
    reply = "9.9E+37"
    protocol = [*CONNECT, *ADD_MEAS1, (RESULT_QUERIES["maximum"], reply)]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        assert mso.add_measurement(1).maximum == float(reply)


@pytest.mark.parametrize(
    ("prop", "value"),
    [
        ("type", "AMPL"),
        ("type", "mean"),
        ("type", "TIE"),
        ("source", "CH5"),
        ("source", "ch1"),
        ("source", "CH1_D0"),
        ("source", "MATH0"),
        ("source", "MATH"),
        ("source", "REF1.5"),
        ("source", 1),
        ("population_limit_enabled", "ON"),
        ("population_limit", 0),
        ("population_limit", 1.5),
        ("population_limit", -1),
        ("uses_global_population_limit", 2),
        ("badge_statistics_enabled", "OFF"),
    ],
)
def test_measurement_invalid_value_raises_without_writing(prop, value):
    with (
        expected_protocol(TektronixMSO456, [*CONNECT, *ADD_MEAS1]) as mso,
        pytest.raises(ValueError),
    ):
        setattr(mso.add_measurement(1), prop, value)


def test_measurement_setter_raises_when_instrument_rejects_value():
    command = "MEASUrement:MEAS1:SOURCE REF1"
    reply = '221,"Settings conflict; MEASUREMENT:MEAS1:SOURCE REF1"'
    protocol = [*CONNECT, *ADD_MEAS1, (command, None), *events(reply)]
    with (
        expected_protocol(TektronixMSO456, protocol) as mso,
        pytest.raises(ValueError, match="rejected the setting: 221, Settings conflict"),
    ):
        mso.add_measurement(1).source = "REF1"


def test_mso58b_measurement_source_accepts_its_eight_channels():
    protocol = [*CONNECT, *ADD_MEAS1, ("MEASUrement:MEAS1:SOURCE CH8", None), *NO_EVENT]
    with expected_protocol(TektronixMSO58B, protocol) as mso:
        mso.add_measurement(1).source = "CH8"


def test_mso58b_measurement_source_rejects_a_ninth_channel_without_writing():
    with (
        expected_protocol(TektronixMSO58B, [*CONNECT, *ADD_MEAS1]) as mso,
        pytest.raises(ValueError),
    ):
        mso.add_measurement(1).source = "CH9"


@pytest.mark.parametrize(
    "path",
    ["C:/pilgrim_test/shot.png", "shot.PNG", "E:/shot.bmp", "shot.jpg", "shot.JPEG", "a.b/x.Jpeg"],
)
def test_save_image_writes_command_without_checking_errors(path):
    with expected_protocol(TektronixMSO456, [*CONNECT, (f'SAVe:IMAGe "{path}"', None)]) as mso:
        mso.save_image(path)


@pytest.mark.parametrize(
    "path",
    ["shot.gif", "shot.tif", "shot.png.bak", "shot", "shot.", "png", 'C:/"shot".png', "", 1, None],
)
def test_save_image_rejects_invalid_path_without_writing(path):
    with expected_protocol(TektronixMSO456, CONNECT) as mso, pytest.raises(ValueError):
        mso.save_image(path)


def test_working_directory_setter_raises_when_instrument_rejects_directory():
    command = 'FILESystem:CWD "C:/missing"'
    reply = '256,"File name not found; Path doesn\'t exist"'
    with (
        expected_protocol(TektronixMSO456, [*CONNECT, (command, None), *events(reply)]) as mso,
        pytest.raises(ValueError, match="rejected the setting: 256, File name not found"),
    ):
        mso.working_directory = "C:/missing"


@pytest.mark.parametrize("path", ["C:/pilgrim_test", "sub"])
def test_make_directory_sends_command_then_checks_errors(path):
    protocol = [*CONNECT, (f'FILESystem:MKDir "{path}"', None), *NO_EVENT]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        mso.make_directory(path)


def test_make_directory_raises_when_instrument_refuses():
    reply = '257,"Specified file is not a regular file"'
    protocol = [*CONNECT, ('FILESystem:MKDir "C:/pilgrim_test"', None), *events(reply)]
    with (
        expected_protocol(TektronixMSO456, protocol) as mso,
        pytest.raises(ValueError, match="rejected creating directory C:/pilgrim_test: 257"),
    ):
        mso.make_directory("C:/pilgrim_test")


@pytest.mark.parametrize("path", ["C:/pilgrim_test/shot.png", "shot.png", "C:/pilgrim_test"])
def test_delete_file_sends_command_then_checks_errors(path):
    protocol = [*CONNECT, (f'FILESystem:DELEte "{path}"', None), *NO_EVENT]
    with expected_protocol(TektronixMSO456, protocol) as mso:
        mso.delete_file(path)


def test_delete_file_raises_when_instrument_refuses():
    reply = '250,"Specified directory is not empty"'
    protocol = [*CONNECT, ('FILESystem:DELEte "C:/pilgrim_test"', None), *events(reply)]
    with (
        expected_protocol(TektronixMSO456, protocol) as mso,
        pytest.raises(ValueError, match="rejected deleting C:/pilgrim_test: 250"),
    ):
        mso.delete_file("C:/pilgrim_test")


PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) + b"IEND\xaeB`\x82"
SHOT = "C:/pilgrim_test/shot.png"
READ_SHOT = (f'FILESystem:READFile "{SHOT}"', None)
# PyMeasure's ProtocolAdapter cannot simulate the end of a message, up to which the driver
# reads, and warns that it ignores the request.
reads_whole_message = pytest.mark.filterwarnings(
    "ignore:Breaking on termination character:UserWarning"
)


@reads_whole_message
@pytest.mark.parametrize(
    ("reply", "contents"),
    [(PNG + b"\n", PNG), (b"a,b\nc\n\n", b"a,b\nc\n"), (b"\n", b"")],
)
def test_read_file_returns_the_message_without_its_trailing_newline(reply, contents):
    with expected_protocol(TektronixMSO456, [*CONNECT, READ_SHOT, (None, reply)]) as mso:
        assert mso.read_file(SHOT) == contents


def raise_visa_error(code: StatusCode, count: int, **kwargs) -> NoReturn:
    """Stand in for ``read_bytes`` when the read fails with the VISA status ``code``.

    :param code: The VISA status code of the failure.
    :param count: Ignored; the number of bytes the driver asked for.
    :param kwargs: Ignored; the further arguments of the read.
    """
    raise VisaIOError(code)


def test_read_file_raises_when_instrument_reports_missing_file(monkeypatch):
    reply = '256,"File name not found; Path doesn\'t exist"'
    with expected_protocol(TektronixMSO456, [*CONNECT, READ_SHOT, *events(reply)]) as mso:
        monkeypatch.setattr(mso, "read_bytes", partial(raise_visa_error, StatusCode.error_timeout))
        with pytest.raises(
            ValueError, match=re.escape(f"rejected reading file {SHOT}: 256")
        ) as info:
            mso.read_file(SHOT)
    assert isinstance(info.value.__context__, VisaIOError)


def test_read_file_reraises_timeout_when_no_event_is_queued(monkeypatch):
    with expected_protocol(TektronixMSO456, [*CONNECT, READ_SHOT, *NO_EVENT]) as mso:
        monkeypatch.setattr(mso, "read_bytes", partial(raise_visa_error, StatusCode.error_timeout))
        with pytest.raises(VisaIOError, match="VI_ERROR_TMO"):
            mso.read_file(SHOT)


def test_read_file_reraises_other_visa_errors_without_reading_events(monkeypatch):
    with expected_protocol(TektronixMSO456, [*CONNECT, READ_SHOT]) as mso:
        monkeypatch.setattr(
            mso, "read_bytes", partial(raise_visa_error, StatusCode.error_connection_lost)
        )
        with pytest.raises(VisaIOError, match="VI_ERROR_CONN_LOST"):
            mso.read_file(SHOT)


@pytest.mark.parametrize("path", ["", 'C:/"quoted".png', 1, None])
@pytest.mark.parametrize("method", ["make_directory", "read_file", "delete_file"])
def test_file_method_rejects_invalid_path_without_writing(method, path):
    with (
        expected_protocol(TektronixMSO456, CONNECT) as mso,
        pytest.raises(ValueError, match="non-empty string without double quotes"),
    ):
        getattr(mso, method)(path)
