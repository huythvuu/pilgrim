"""Tests for the directory input."""

from pathlib import Path

import pytest
from pymeasure.display.Qt import QtWidgets
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import DirectoryInput
from pilgrim.procedure.parameters import StringParameter


def make_input(qtbot: QtBot, default: str) -> tuple[DirectoryInput, StringParameter]:
    """Return a shown input of a directory parameter with the given default, and the parameter."""
    parameter = StringParameter("Local capture directory", default=default)
    widget = DirectoryInput(parameter)
    qtbot.addWidget(widget)
    widget.show()
    return widget, parameter


def test_shows_the_parameter_value(qtbot: QtBot) -> None:
    widget, _ = make_input(qtbot, "./results/captures/")
    assert widget.text() == "./results/captures/"
    assert widget.value() == "./results/captures/"
    assert "Local capture directory" in widget.toolTip()
    assert "browses" in widget.toolTip()
    assert len(widget.actions()) == 1  # The browse button.


def test_typed_text_updates_the_parameter(qtbot: QtBot, tmp_path: Path) -> None:
    widget, parameter = make_input(qtbot, "")
    widget.setText(str(tmp_path))
    widget.update_parameter()
    assert parameter.value == str(tmp_path)


def test_browsing_sets_the_chosen_directory(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    widget, parameter = make_input(qtbot, "")
    chosen = tmp_path / "captures"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(chosen)
    )
    widget.actions()[0].trigger()
    assert widget.text() == str(chosen)
    widget.update_parameter()
    assert parameter.value == str(chosen)


def test_cancelled_browsing_keeps_the_text(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    widget, _ = make_input(qtbot, str(tmp_path))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *args, **kwargs: "")
    widget.actions()[0].trigger()
    assert widget.text() == str(tmp_path)


def test_browsing_starts_at_the_directory_typed(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    widget, _ = make_input(qtbot, str(tmp_path))
    starts: list[str] = []

    def dialog(parent: QtWidgets.QWidget, caption: str, directory: str) -> str:
        starts.append(directory)
        return ""

    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", dialog)
    widget.actions()[0].trigger()
    assert starts == [str(tmp_path)]
