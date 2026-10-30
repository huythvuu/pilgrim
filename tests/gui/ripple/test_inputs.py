"""Tests for the inputs widget of the ripple procedure."""

import pytest
from pymeasure.display.inputs import BooleanInput, IntegerInput, ScientificInput, StringInput
from pymeasure.display.Qt import QtWidgets
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import ListInput, RippleInputsWidget, VisaResourceInput
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
    assert isinstance(widget.target, ScientificInput)
    assert isinstance(widget.use_cursor, BooleanInput)
    assert isinstance(widget.cursor_absolute, BooleanInput)
    assert isinstance(widget.cursor_hi_rel, ScientificInput)
    assert isinstance(widget.cursor_lo_rel, ScientificInput)
    assert isinstance(widget.cursor_hi_abs, ScientificInput)
    assert isinstance(widget.cursor_lo_abs, ScientificInput)
    assert type(widget.name) is StringInput
    assert type(widget.scope_capture_dir) is StringInput


def test_shows_the_defaults(widget: RippleInputsWidget) -> None:
    assert widget.scope_resource.value() == ""
    assert widget.loads.text() == "[0 A, 1 A, 2 A]"
    assert widget.horizontal_divs.text() == "[1 us, 1 ms]"
    assert widget.bandwidths.text() == "[20 MHz, 250 MHz, 1 GHz]"
    assert (widget.channel.minimum(), widget.channel.maximum()) == (1, 8)
    assert widget.target.value() == 0
    assert widget.use_cursor.isChecked()
    assert not widget.cursor_absolute.isChecked()
    assert (widget.cursor_hi_rel.value(), widget.cursor_lo_rel.value()) == (0, 0)
    assert (widget.cursor_hi_rel.minimum(), widget.cursor_lo_rel.minimum()) == (0, 0)
    assert (widget.cursor_hi_abs.value(), widget.cursor_lo_abs.value()) == (0, 0)
    assert widget.cursor_lo_abs.minimum() < 0


def cursor_elements(widget: RippleInputsWidget, kind: str) -> tuple[QtWidgets.QWidget, ...]:
    """Return the inputs and labels of the relative or absolute cursor levels."""
    names = (f"cursor_hi_{kind}", f"cursor_lo_{kind}")
    return tuple(getattr(widget, name) for name in names) + tuple(
        widget.labels[name] for name in names
    )


def test_cursor_levels_are_shown_only_while_cursors_are_used(widget: RippleInputsWidget) -> None:
    widget.show()
    relative, absolute = cursor_elements(widget, "rel"), cursor_elements(widget, "abs")
    assert all(not element.isHidden() for element in relative)
    assert all(element.isHidden() for element in absolute)
    assert not widget.cursor_absolute.isHidden()
    widget.use_cursor.setChecked(False)
    assert all(element.isHidden() for element in relative + absolute)
    assert widget.cursor_absolute.isHidden()
    # The target stays, since the procedure uses it for the results as well.
    assert not widget.target.isHidden()
    widget.use_cursor.setChecked(True)
    assert all(not element.isHidden() for element in relative)
    assert all(element.isHidden() for element in absolute)
    assert not widget.cursor_absolute.isHidden()


def test_absolute_box_swaps_the_relative_cursor_levels_for_the_absolute_ones(
    widget: RippleInputsWidget,
) -> None:
    widget.show()
    relative, absolute = cursor_elements(widget, "rel"), cursor_elements(widget, "abs")
    widget.cursor_absolute.setChecked(True)
    assert all(element.isHidden() for element in relative)
    assert all(not element.isHidden() for element in absolute)
    # Neither pair shows while the cursors are not used, whichever is chosen.
    widget.use_cursor.setChecked(False)
    assert all(element.isHidden() for element in relative + absolute)
    widget.use_cursor.setChecked(True)
    assert all(element.isHidden() for element in relative)
    assert all(not element.isHidden() for element in absolute)
    widget.cursor_absolute.setChecked(False)
    assert all(not element.isHidden() for element in relative)
    assert all(element.isHidden() for element in absolute)


def test_cursor_boxes_share_a_line(widget: RippleInputsWidget) -> None:
    assert widget.cursor_absolute.text() == "Absolute"
    parent = widget.use_cursor.parentWidget()
    assert parent is not None
    column = parent.layout()
    assert isinstance(column, QtWidgets.QBoxLayout)
    items = [column.itemAt(index) for index in range(column.count())]
    layouts = [item.layout() if item is not None else None for item in items]
    ((index, row),) = [(i, layout) for i, layout in enumerate(layouts) if layout is not None]
    assert row.indexOf(widget.use_cursor) == 0
    assert row.indexOf(widget.cursor_absolute) == 1
    # The line takes the place of the first box in the column.
    assert column.indexOf(widget.target) < index < column.indexOf(widget.cursor_hi_rel)
    # Showing the parameters of a run keeps the short text of the box.
    procedure = widget.get_procedure()
    widget.set_parameters(procedure.parameter_objects())
    assert widget.cursor_absolute.text() == "Absolute"


def test_get_procedure_reads_the_inputs(widget: RippleInputsWidget) -> None:
    widget.scope_resource.setCurrentText("USB0::0x0699::0x0522::C031408::INSTR")
    widget.channel.setValue(5)
    widget.vin.setValue(54)
    widget.loads.setText("[0, 500 mA]")
    widget.horizontal_divs.setText("[2.5 us]")
    widget.bandwidths.setText("[20e6, 1e9] Hz")
    widget.target.setValue(12)
    widget.use_cursor.setChecked(False)
    widget.cursor_absolute.setChecked(True)
    widget.cursor_hi_rel.setValue(5)
    widget.cursor_lo_rel.setValue(4)
    widget.cursor_hi_abs.setValue(12.5)
    widget.cursor_lo_abs.setValue(-11.5)
    procedure = widget.get_procedure()
    assert isinstance(procedure, RippleProcedure)
    assert procedure.scope_resource == "USB0::0x0699::0x0522::C031408::INSTR"
    assert (procedure.channel, procedure.vin) == (5, 54)
    assert (procedure.target, procedure.use_cursor, procedure.cursor_absolute) == (12, False, True)
    # Hidden cursor levels are still read, in case the boxes are changed again.
    assert (procedure.cursor_hi_rel, procedure.cursor_lo_rel) == (5, 4)
    assert (procedure.cursor_hi_abs, procedure.cursor_lo_abs) == (12.5, -11.5)
    assert procedure.loads == [0, 0.5]
    # Pint lands a prefixed value such as 2.5 us within rounding of the plain number.
    assert procedure.horizontal_divs == pytest.approx([2.5e-6])
    assert procedure.bandwidths == [20e6, 1e9]
    assert [(load, bandwidth) for load, _, bandwidth in procedure.combinations()] == [
        (0, 20e6),
        (0, 1e9),
        (0.5, 20e6),
        (0.5, 1e9),
    ]


def test_get_procedure_rejects_an_invalid_list(widget: RippleInputsWidget) -> None:
    widget.loads.setText("[abc]")
    with pytest.raises(ValueError, match="Loads"):
        widget.get_procedure()


def test_procedure_class_keeps_no_input_classes(widget: RippleInputsWidget) -> None:
    assert RippleProcedure.scope_resource.ui_class is None
    assert RippleProcedure.loads.ui_class is None
    assert RippleProcedure().parameter_objects()["bandwidths"].ui_class is None
    assert RippleProcedure.cursor_absolute.group_by == {}
    assert RippleProcedure.cursor_hi_rel.group_by == {}
    assert RippleProcedure().parameter_objects()["cursor_lo_abs"].group_by == {}
