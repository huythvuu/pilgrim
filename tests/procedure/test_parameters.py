"""Tests for the typed procedure parameters."""

import numpy as np
import pytest
from pymeasure.units import ureg

from pilgrim.procedure.parameters import FloatListParameter, FloatRangeListParameter

DECADES = [100.0, 1e3, 1e4, 1e5, 1e6]


@pytest.fixture
def frequencies() -> FloatListParameter:
    return FloatListParameter("Frequencies", units="Hz", minimum=1e-3, maximum=1e6)


@pytest.mark.parametrize(
    "value",
    [
        DECADES,
        tuple(DECADES),
        np.logspace(2, 6, 5),
        ["100 Hz", "1 kHz", "10kHz", ureg.Quantity(100, "kHz"), 1e6],
        "[100 Hz, 1 kHz, 10 kHz, 100 kHz, 1 MHz]",
        "[100, 1000, 1e4, 1e5, 1e6] Hz",
        " [ 100 , 1 kHz , 1e4 , 100 kHz , 1 MHz ] ",
    ],
)
def test_float_list_converts_to_units(frequencies: FloatListParameter, value: object) -> None:
    frequencies.value = value
    assert frequencies.value == DECADES
    assert all(type(element) is float for element in frequencies.value)


@pytest.mark.parametrize("value", [[], (), "[]", "[ ] Hz"])
def test_float_list_accepts_empty(frequencies: FloatListParameter, value: object) -> None:
    frequencies.value = value
    assert frequencies.value == []
    assert frequencies.is_set()


@pytest.mark.parametrize("value", [DECADES, []])
def test_float_list_round_trips_through_string(
    frequencies: FloatListParameter, value: list[float]
) -> None:
    frequencies.value = value
    assert " " not in str(frequencies).removesuffix(" Hz")
    assert frequencies.convert(str(frequencies)) == value


@pytest.mark.parametrize(
    "value",
    [
        1e3,
        "1 kHz",
        "100 Hz, 1 kHz",
        "[100, 1000] V",
        "[1 V]",
        "[abc]",
        "[100,]",
        "[(100]",
        "[2 MHz]",
        [0.0],
        [float("nan")],
    ],
)
def test_float_list_rejects_invalid(frequencies: FloatListParameter, value: object) -> None:
    with pytest.raises(ValueError, match="Frequencies"):
        frequencies.value = value


def test_float_list_without_units_rejects_quantities() -> None:
    parameter = FloatListParameter("Ratios")
    parameter.value = "[1, 2.5]"
    assert parameter.value == [1.0, 2.5]
    with pytest.raises(ValueError, match="Ratios"):
        parameter.value = ["1 kHz"]


FRACTIONS = [(0.0, 0.25), (0.25, 0.75), (0.5, 1.0), (0.0, 1.0)]


@pytest.fixture
def windows() -> FloatRangeListParameter:
    return FloatRangeListParameter("Windows", minimum=0, maximum=1)


@pytest.mark.parametrize(
    "value",
    [
        FRACTIONS,
        [[0, 0.25], [0.25, 0.75], [0.5, 1], [0, 1]],
        tuple(FRACTIONS),
        np.array(FRACTIONS),
        "[(0, .25), (.25, .75), (.5, 1), (0, 1)]",
        " [ ( 0 , .25 ),( .25,.75 ) , (.5, 1),(0, 1) ] ",
    ],
)
def test_float_range_list_converts(windows: FloatRangeListParameter, value: object) -> None:
    windows.value = value
    assert windows.value == FRACTIONS
    assert all(type(bound) is float for bounds in windows.value for bound in bounds)


def test_float_range_list_converts_to_units() -> None:
    bands = FloatRangeListParameter("Bands", units="Hz")
    expected = [(100.0, 1e3), (1e3, 1e4)]
    for value in [
        "[(100, 1000), (1 kHz, 10 kHz)] Hz",
        "[(100 Hz, 1 kHz), (1000, 1e4)]",
        [(100, "1 kHz"), (ureg.Quantity(1, "kHz"), 1e4)],
    ]:
        bands.value = value
        assert bands.value == expected


@pytest.mark.parametrize("value", [[], (), "[]", "[ ]"])
def test_float_range_list_accepts_empty(windows: FloatRangeListParameter, value: object) -> None:
    windows.value = value
    assert windows.value == []
    assert windows.is_set()


@pytest.mark.parametrize("value", [FRACTIONS, [(0.5, 0.5)], []])
def test_float_range_list_round_trips_through_string(
    windows: FloatRangeListParameter, value: list[tuple[float, float]]
) -> None:
    windows.value = value
    assert " " not in str(windows)
    assert windows.convert(str(windows)) == value


def test_float_range_list_round_trips_with_units() -> None:
    bands = FloatRangeListParameter("Bands", units="Hz", default=[(100, 1e3)])
    assert str(bands) == "[(100.0,1000.0)] Hz"
    assert bands.convert(str(bands)) == [(100.0, 1e3)]


@pytest.mark.parametrize(
    "value",
    [
        0.5,
        (0, 1),
        "(0, 1)",
        "[0, 1]",
        "[(0, 1), 0.5]",
        "[(0, 1) (0.5, 1)]",
        "[(0, 1)(0.5, 1)]",
        "[(0, 0.5, 1)]",
        "[(0)]",
        "[((0, 1)]",
        "[(0, 1),]",
        "[(0, abc)]",
        "[(0, 1 V)]",
        "[(0.75, 0.25)]",
        "[(0, 2)]",
        [(0, 1, 2)],
        [0, 1],
        [(-0.5, 1)],
        [(0, float("nan"))],
    ],
)
def test_float_range_list_rejects_invalid(windows: FloatRangeListParameter, value: object) -> None:
    with pytest.raises(ValueError, match="Windows"):
        windows.value = value
