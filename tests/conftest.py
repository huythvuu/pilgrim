"""Shared pytest configuration: the option and fixture that enable device tests."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the ``--device-address`` option that enables device tests.

    :param parser: The pytest parser to register the option on.
    """
    parser.addoption(
        "--device-address",
        action="store",
        default=None,
        help="VISA resource name of the instrument used by *_with_device tests",
    )


@pytest.fixture(scope="module")
def connected_device_address(pytestconfig: pytest.Config) -> str:
    """Return the VISA resource name from ``--device-address``, or skip the test.

    :param pytestconfig: The pytest configuration holding the command line options.
    :returns: The VISA resource name of the instrument to test against.
    """
    address = pytestconfig.getoption("device_address")
    if address is None:
        pytest.skip("Device test; pass --device-address=<VISA resource name> to run it.")
    return address
