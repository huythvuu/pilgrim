"""Device tests for the Tektronix MSO4/5/6 base class.

Run with ``--device-address=<VISA resource name>`` of any 4, 5 or 6 Series MSO.
The tests only read the identification and the event queue; they change no settings beyond the
response header and verbosity states every connection sets.
"""

import re

import pytest

from pilgrim.instruments import TektronixMSO456

MODEL_PATTERN = re.compile(r"TEKTRONIX,(MSO[456]\d|LPD64)")
COMMAND_ERRORS = range(100, 200)


@pytest.fixture(scope="module")
def mso(connected_device_address):
    """Connect to the instrument once per module.

    Skips if the instrument is not a 4, 5 or 6 Series MSO.

    :param connected_device_address: VISA resource name from ``--device-address``.
    :returns: The connected instrument, closed when the module finishes.
    """
    instrument = TektronixMSO456(connected_device_address)
    try:
        if not MODEL_PATTERN.match(instrument.id):
            pytest.skip(f"Not a 4, 5 or 6 Series MSO: {instrument.id}")
        yield instrument
    finally:
        instrument.adapter.close()


def test_write_termination_is_newline(mso):
    assert mso.adapter.connection.write_termination == "\n"


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
