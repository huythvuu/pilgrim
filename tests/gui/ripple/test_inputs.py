"""Tests for the inputs widget of the ripple procedure."""

import pytest
from pymeasure.display.inputs import IntegerInput, StringInput
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import (
    DirectoryInput,
    ListInput,
    RippleInputsWidget,
    VisaResourceInput,
)
from pilgrim.procedure import RippleProcedure


@pytest.fixture
def widget(qtbot: QtBot) -> RippleInputsWidget:
    """Return an inputs widget showing every parameter the window shows."""
    widget = RippleInputsWidget()
    qtbot.addWidget(widget)
    return widget


def test_uses_an_input_suited_to_each_parameter(widget: RippleInputsWidget) -> None:
    assert isinstance(widget.scope_resource, VisaResourceInput)
    assert isinstance(widget.loads, ListInput)
    assert isinstance(widget.horizontal_divs, ListInput)
    assert isinstance(widget.bandwidths, ListInput)
    assert isinstance(widget.channel, IntegerInput)
    assert isinstance(widget.vin, IntegerInput)
    assert isinstance(widget.pop_min, IntegerInput)
    assert type(widget.name) is StringInput
    assert type(widget.scope_capture_dir) is StringInput
    assert isinstance(widget.local_capture_dir, DirectoryInput)


def test_shows_the_defaults(widget: RippleInputsWidget) -> None:
    assert widget.scope_resource.value() == ""
    assert widget.loads.text() == "[0, 50, 100]"
    assert widget.horizontal_divs.text() == "[1 us, 1 ms]"
    assert widget.bandwidths.text() == "[20 MHz, 250 MHz, 1 GHz]"
    assert widget.local_capture_dir.text() == "./results/captures/"
    assert (widget.channel.minimum(), widget.channel.maximum()) == (1, 8)


def test_get_procedure_reads_the_inputs(widget: RippleInputsWidget) -> None:
    widget.scope_resource.setCurrentText("USB0::0x0699::0x0522::C031408::INSTR")
    widget.channel.setValue(5)
    widget.vin.setValue(54)
    widget.loads.setText("[0, 100]")
    widget.horizontal_divs.setText("[2.5 us]")
    widget.bandwidths.setText("[20e6, 1e9] Hz")
    procedure = widget.get_procedure()
    assert isinstance(procedure, RippleProcedure)
    assert procedure.scope_resource == "USB0::0x0699::0x0522::C031408::INSTR"
    assert (procedure.channel, procedure.vin) == (5, 54)
    assert procedure.loads == [0, 100]
    # Pint lands a prefixed value such as 2.5 us within rounding of the plain number.
    assert procedure.horizontal_divs == pytest.approx([2.5e-6])
    assert procedure.bandwidths == [20e6, 1e9]
    assert [(load, bandwidth) for load, _, bandwidth in procedure.combinations()] == [
        (0, 20e6),
        (0, 1e9),
        (100, 20e6),
        (100, 1e9),
    ]


def test_get_procedure_rejects_an_invalid_list(widget: RippleInputsWidget) -> None:
    widget.loads.setText("[abc]")
    with pytest.raises(ValueError, match="Loads"):
        widget.get_procedure()


def test_procedure_class_keeps_no_input_classes(widget: RippleInputsWidget) -> None:
    assert RippleProcedure.scope_resource.ui_class is None
    assert RippleProcedure.loads.ui_class is None
    assert RippleProcedure().parameter_objects()["bandwidths"].ui_class is None
