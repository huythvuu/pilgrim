"""Device tests for the Tektronix MSO4/5/6 base class.

Run with ``--device-address=<VISA resource name>`` of any 4, 5 or 6 Series MSO.
The tests read the identification and the event queue, and create the directory
``C:/pilgrim_test`` on the instrument for the screen images they save, read back and delete
again. The working directory and the image color mode are restored after the tests that change
them; no other settings change beyond the response header and verbosity states every connection
sets.
"""

import re

import pytest

from pilgrim.instruments import TektronixMSO456

MODEL_PATTERN = re.compile(r"TEKTRONIX,(MSO[456]\d|LPD64)")
COMMAND_ERRORS = range(100, 200)
SCRATCH_DIRECTORY = "C:/pilgrim_test"
MISSING_DIRECTORY = "C:/pilgrim_missing_directory"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_TRAILER = b"IEND\xaeB`\x82"


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


def test_working_directory_round_trips_without_trailing_slash(mso, working_directory):
    assert mso.working_directory == working_directory
    mso.working_directory = f"{working_directory}/"
    assert mso.working_directory == working_directory


def test_working_directory_rejects_missing_directory_and_keeps_current(mso):
    original = mso.working_directory
    with pytest.raises(ValueError, match="rejected the setting: 256"):
        mso.working_directory = MISSING_DIRECTORY
    assert mso.working_directory == original


def test_image_color_mode_round_trips(mso):
    original = mso.image_color_mode
    other = "INVERTED" if original == "NORMAL" else "NORMAL"
    try:
        mso.image_color_mode = other
        assert mso.image_color_mode == other
    finally:
        mso.image_color_mode = original
    assert mso.image_color_mode == original


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
