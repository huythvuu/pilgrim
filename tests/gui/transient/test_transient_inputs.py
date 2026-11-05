"""Tests for the inputs widget of the transient procedure."""

import pytest
from pymeasure.display.inputs import BooleanInput, IntegerInput, ScientificInput, StringInput
from pytestqt.qtbot import QtBot

from pilgrim.gui.components import VisaResourceInput
from pilgrim.gui.transient.components import INPUTS, TransientInputsWidget
from pilgrim.procedure import TransientProcedure

AFG_RESOURCE = "USB0::0x0699::0x0359::C012345::INSTR"
SCOPE_RESOURCE = "USB0::0x0699::0x0522::C031408::INSTR"


@pytest.fixture
def widget(qtbot: QtBot) -> TransientInputsWidget:
    """Return an inputs widget showing every parameter the window shows."""
    widget = TransientInputsWidget()
    qtbot.addWidget(widget)
    return widget


def test_shows_every_parameter_of_the_procedure() -> None:
    assert set(INPUTS) == set(TransientProcedure().parameter_objects())


def test_uses_an_input_suited_to_each_parameter(widget: TransientInputsWidget) -> None:
    assert isinstance(widget.afg_resource, VisaResourceInput)
    assert isinstance(widget.scope_resource, VisaResourceInput)
    assert isinstance(widget.vin, ScientificInput)
    assert isinstance(widget.vout_channel, IntegerInput)
    assert isinstance(widget.iout_channel, IntegerInput)
    assert isinstance(widget.safe_mode, BooleanInput)
    assert isinstance(widget.use_cursor, BooleanInput)
    assert isinstance(widget.cursor_hi_abs, ScientificInput)
    assert isinstance(widget.cursor_lo_abs, ScientificInput)
    assert isinstance(widget.iout_trigger_level, ScientificInput)
    assert isinstance(widget.pop_min, IntegerInput)
    assert type(widget.name) is StringInput
    assert type(widget.scope_capture_dir) is StringInput


def test_shows_the_defaults(widget: TransientInputsWidget) -> None:
    assert widget.name.text() == "transient"
    assert (widget.afg_resource.value(), widget.scope_resource.value()) == ("", "")
    assert widget.vin.value() == 0
    assert (widget.vout_channel.value(), widget.iout_channel.value()) == (1, 2)
    assert (widget.vout_channel.minimum(), widget.vout_channel.maximum()) == (1, 8)
    assert (widget.iout_channel.minimum(), widget.iout_channel.maximum()) == (1, 8)
    assert widget.safe_mode.isChecked()
    assert widget.use_cursor.isChecked()
    assert (widget.cursor_hi_abs.value(), widget.cursor_lo_abs.value()) == (0, 0)
    assert widget.cursor_lo_abs.minimum() < 0
    assert (widget.iout_trigger_level.value(), widget.pop_min.value()) == (144, 300)
    assert widget.scope_capture_dir.text() == "C:/Temp/Transient/"


def test_cursor_levels_stay_shown_while_cursors_are_not_used(
    widget: TransientInputsWidget,
) -> None:
    widget.show()
    names = ("cursor_hi_abs", "cursor_lo_abs")
    elements = [getattr(widget, name) for name in names] + [widget.labels[name] for name in names]
    assert all(not element.isHidden() for element in elements)
    # The low level is also the trigger level of the inputs that trigger on Vout.
    widget.use_cursor.setChecked(False)
    assert all(not element.isHidden() for element in elements)


def test_get_procedure_reads_the_inputs(widget: TransientInputsWidget) -> None:
    widget.name.setText("dut1")
    widget.afg_resource.setCurrentText(AFG_RESOURCE)
    widget.scope_resource.setCurrentText(SCOPE_RESOURCE)
    widget.vin.setValue(12)
    widget.vout_channel.setValue(3)
    widget.iout_channel.setValue(4)
    widget.safe_mode.setChecked(False)
    widget.use_cursor.setChecked(False)
    widget.cursor_hi_abs.setValue(5.25)
    widget.cursor_lo_abs.setValue(4.8)
    widget.iout_trigger_level.setValue(150)
    widget.pop_min.setValue(50)
    widget.scope_capture_dir.setText("C:/Temp/dut1/")
    procedure = widget.get_procedure()
    assert isinstance(procedure, TransientProcedure)
    assert procedure.name == "dut1"
    assert (procedure.afg_resource, procedure.scope_resource) == (AFG_RESOURCE, SCOPE_RESOURCE)
    assert (procedure.vin, procedure.vout_channel, procedure.iout_channel) == (12, 3, 4)
    assert (procedure.safe_mode, procedure.use_cursor) == (False, False)
    assert (procedure.cursor_hi_abs, procedure.cursor_lo_abs) == (5.25, 4.8)
    assert (procedure.iout_trigger_level, procedure.pop_min) == (150, 50)
    assert procedure.scope_capture_dir == "C:/Temp/dut1/"


def test_procedure_class_keeps_no_input_classes(widget: TransientInputsWidget) -> None:
    assert TransientProcedure.afg_resource.ui_class is None
    assert TransientProcedure.scope_resource.ui_class is None
    assert TransientProcedure().parameter_objects()["scope_resource"].ui_class is None
