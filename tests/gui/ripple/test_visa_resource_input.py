"""Tests for the VISA resource input."""

import logging

import pytest
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import (
    VisaResourceInput,
    list_visa_resources,
    visa_resource_input,
)
from pilgrim.procedure.parameters import StringParameter

RESOURCES = ["USB0::0x0699::0x0522::C031408::INSTR", "ASRL3::INSTR"]


def make_input(qtbot: QtBot, parameter: StringParameter) -> VisaResourceInput:
    """Return a shown input of a resource parameter, listing ``RESOURCES`` when opened."""
    widget = VisaResourceInput(parameter, list_resources=lambda: RESOURCES)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_shows_the_parameter_value_as_editable_text(qtbot: QtBot) -> None:
    widget = make_input(qtbot, StringParameter("Scope resource", default="GPIB0::1::INSTR"))
    assert widget.isEditable()
    assert widget.currentText() == "GPIB0::1::INSTR"
    assert widget.value() == "GPIB0::1::INSTR"
    assert widget.count() == 0


def test_lists_the_resources_when_opened(qtbot: QtBot) -> None:
    widget = make_input(qtbot, StringParameter("Scope resource", default=""))
    widget.showPopup()
    widget.hidePopup()
    assert [widget.itemText(i) for i in range(widget.count())] == RESOURCES
    assert widget.value() == ""


def test_keeps_the_typed_text_when_opened(qtbot: QtBot) -> None:
    parameter = StringParameter("Scope resource", default="")
    widget = make_input(qtbot, parameter)
    widget.setCurrentText("TCPIP::scope::INSTR")
    widget.showPopup()
    widget.hidePopup()
    assert widget.value() == "TCPIP::scope::INSTR"
    widget.update_parameter()
    assert parameter.value == "TCPIP::scope::INSTR"


def test_value_is_stripped(qtbot: QtBot) -> None:
    parameter = StringParameter("Scope resource", default="")
    widget = make_input(qtbot, parameter)
    widget.setCurrentText("  ASRL3::INSTR ")
    assert widget.value() == "ASRL3::INSTR"
    widget.update_parameter()
    assert parameter.value == "ASRL3::INSTR"


def test_tooltip_mentions_the_listing(qtbot: QtBot) -> None:
    widget = make_input(qtbot, StringParameter("Scope resource", default=""))
    assert "Scope resource" in widget.toolTip()
    assert "VISA finds" in widget.toolTip()


def test_list_visa_resources_sorts_what_visa_finds(monkeypatch: pytest.MonkeyPatch) -> None:
    class ResourceManager:
        def list_resources(self) -> tuple[str, ...]:
            return ("USB0::1::INSTR", "ASRL3::INSTR")

    monkeypatch.setattr(visa_resource_input.pyvisa, "ResourceManager", ResourceManager)
    assert list_visa_resources() == ["ASRL3::INSTR", "USB0::1::INSTR"]


@pytest.mark.parametrize("error", [ValueError("Could not locate a VISA implementation"), OSError()])
def test_list_visa_resources_without_visa_is_empty(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    def resource_manager() -> None:
        raise error

    monkeypatch.setattr(visa_resource_input.pyvisa, "ResourceManager", resource_manager)
    with caplog.at_level(logging.WARNING):
        assert list_visa_resources() == []
    assert "Could not list the VISA resources" in caplog.text
