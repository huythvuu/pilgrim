"""Tests for the list parameter input."""

import pytest
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import ListInput
from pilgrim.gui.ripple.components.list_input import format_element
from pilgrim.procedure.parameters import FloatListParameter, IntegerListParameter


@pytest.fixture
def bandwidths() -> FloatListParameter:
    """Return a bandwidth list parameter with the ripple defaults."""
    return FloatListParameter("Bandwidths", units="Hz", default=[20e6, 250e6, 1e9])


def test_shows_values_with_si_prefixes_and_units(
    qtbot: QtBot, bandwidths: FloatListParameter
) -> None:
    widget = ListInput(bandwidths)
    qtbot.addWidget(widget)
    assert widget.text() == "[20 MHz, 250 MHz, 1 GHz]"
    assert widget.error is None
    widget.update_parameter()
    assert bandwidths.value == [20e6, 250e6, 1e9]


def test_shows_micro_as_u(qtbot: QtBot) -> None:
    parameter = FloatListParameter("Horizontal divs", units="s", default=[1e-6, 1e-3])
    widget = ListInput(parameter)
    qtbot.addWidget(widget)
    assert widget.text() == "[1 us, 1 ms]"
    widget.update_parameter()
    assert parameter.value == [1e-6, 1e-3]


def test_shows_unitless_integers_plainly(qtbot: QtBot) -> None:
    parameter = IntegerListParameter("Loads", default=[0, 50, 100], minimum=0, maximum=100)
    widget = ListInput(parameter)
    qtbot.addWidget(widget)
    assert widget.text() == "[0, 50, 100]"
    widget.update_parameter()
    assert parameter.value == [0, 50, 100]


def test_shows_empty_list(qtbot: QtBot) -> None:
    parameter = FloatListParameter("Bandwidths", units="Hz", default=[])
    widget = ListInput(parameter)
    qtbot.addWidget(widget)
    assert widget.text() == "[]"
    widget.update_parameter()
    assert parameter.value == []


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("[20e6, 250e6] Hz", [20e6, 250e6]),
        ("[1 kHz, 2.5 MHz]", [1e3, 2.5e6]),
        ("[100, 1e9]", [100.0, 1e9]),
        ("[]", []),
    ],
)
def test_accepts_any_form_the_parameter_converts(
    qtbot: QtBot, bandwidths: FloatListParameter, text: str, value: list[float]
) -> None:
    widget = ListInput(bandwidths)
    qtbot.addWidget(widget)
    widget.setText(text)
    assert widget.error is None
    widget.update_parameter()
    assert bandwidths.value == value


def test_flags_invalid_text_until_fixed(qtbot: QtBot, bandwidths: FloatListParameter) -> None:
    widget = ListInput(bandwidths)
    qtbot.addWidget(widget)
    widget.setText("[abc]")
    assert widget.error is not None
    assert "Bandwidths" in widget.error
    assert len(widget.actions()) == 1
    assert widget.actions()[0].toolTip() == widget.error
    widget.setText("[2 GHz]")  # Beyond the default maximum of 1e9.
    assert widget.error is not None
    assert "outside" in widget.error
    widget.setText("[1 GHz]")
    assert widget.error is None
    assert widget.actions() == []


def test_invalid_text_raises_when_the_parameter_is_read(
    qtbot: QtBot, bandwidths: FloatListParameter
) -> None:
    widget = ListInput(bandwidths)
    qtbot.addWidget(widget)
    widget.setText("20 MHz")
    with pytest.raises(ValueError, match="Bandwidths"):
        widget.update_parameter()


def test_set_parameter_shows_the_new_value(qtbot: QtBot, bandwidths: FloatListParameter) -> None:
    widget = ListInput(bandwidths)
    qtbot.addWidget(widget)
    widget.set_parameter(FloatListParameter("Bandwidths", units="Hz", default=[500e6]))
    assert widget.text() == "[500 MHz]"
    assert widget.error is None


@pytest.mark.parametrize(
    ("value", "units", "text"),
    [
        (20e6, "Hz", "20 MHz"),
        (1234.5678, "Hz", "1.2345678 kHz"),
        (2.5e-6, "s", "2.5 us"),
        (0.0, "s", "0 s"),
        (-0.5, "V", "-500 mV"),
        (50, None, "50"),
        (0.1, None, "0.1"),
    ],
)
def test_format_element(value: float, units: str | None, text: str) -> None:
    assert format_element(value, units) == text
