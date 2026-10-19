import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the option that enables device tests."""
    parser.addoption(
        "--device-address",
        action="store",
        default=None,
        help="VISA resource name of the instrument used by *_with_device tests",
    )


@pytest.fixture(scope="module")
def connected_device_address(pytestconfig: pytest.Config) -> str:
    """Return the VISA resource name from --device-address, or skip the test."""
    address = pytestconfig.getoption("device_address")
    if address is None:
        pytest.skip("Device test; pass --device-address=<VISA resource name> to run it.")
    return address
