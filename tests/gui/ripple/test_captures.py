"""Tests for the captures tab."""

import os
from pathlib import Path

from pymeasure.display.Qt import QtGui
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import CapturesWidget
from pilgrim.gui.ripple.components.captures import IDLE_TEXT

RED = QtGui.QColor("red")
BLUE = QtGui.QColor("blue")


def write_capture(path: Path, color: QtGui.QColor, modified: float) -> None:
    """Write a small single-colour PNG with the given modification time."""
    image = QtGui.QImage(8, 6, QtGui.QImage.Format.Format_RGB32)
    image.fill(color)
    assert image.save(str(path))
    os.utime(path, (modified, modified))


def listed_names(widget: CapturesWidget) -> list[str]:
    """Return the file names listed, from the top."""
    return [widget.file_list.item(row).text() for row in range(widget.file_list.count())]


def shown_color(widget: CapturesWidget) -> QtGui.QColor:
    """Return the colour of the first pixel of the capture shown."""
    return widget.image.pixmap().toImage().pixelColor(0, 0)


def make_widget(qtbot: QtBot) -> CapturesWidget:
    """Return a shown captures widget that only refreshes when told to."""
    widget = CapturesWidget(refresh_time=3600)
    qtbot.addWidget(widget)
    widget.resize(400, 300)
    widget.show()
    return widget


def test_idle_until_watching(qtbot: QtBot) -> None:
    widget = make_widget(qtbot)
    assert widget.directory is None
    assert widget.directory_label.text() == IDLE_TEXT
    assert not widget.open_button.isEnabled()
    assert listed_names(widget) == []
    widget.refresh()  # Nothing to do without a directory.


def test_lists_the_captures_newest_first_and_shows_the_newest(qtbot: QtBot, tmp_path: Path) -> None:
    write_capture(tmp_path / "older.png", RED, 1000)
    write_capture(tmp_path / "newer.png", BLUE, 2000)
    (tmp_path / "notes.txt").write_text("not a capture", encoding="utf-8")
    widget = make_widget(qtbot)
    widget.watch(tmp_path)
    assert widget.directory == tmp_path
    assert widget.directory_label.text() == str(tmp_path)
    assert widget.open_button.isEnabled()
    assert listed_names(widget) == ["newer.png", "older.png"]
    assert widget.file_list.currentItem().text() == "newer.png"
    assert shown_color(widget) == BLUE


def test_refresh_adds_new_captures_and_selects_them(qtbot: QtBot, tmp_path: Path) -> None:
    write_capture(tmp_path / "first.png", RED, 1000)
    widget = make_widget(qtbot)
    widget.watch(tmp_path)
    write_capture(tmp_path / "second.png", BLUE, 2000)
    widget.refresh()
    assert listed_names(widget) == ["second.png", "first.png"]
    assert widget.file_list.currentItem().text() == "second.png"
    assert shown_color(widget) == BLUE
    widget.refresh()  # Nothing changed, so nothing moves.
    assert listed_names(widget) == ["second.png", "first.png"]


def test_selecting_a_capture_shows_it(qtbot: QtBot, tmp_path: Path) -> None:
    write_capture(tmp_path / "first.png", RED, 1000)
    write_capture(tmp_path / "second.png", BLUE, 2000)
    widget = make_widget(qtbot)
    widget.watch(tmp_path)
    widget.file_list.setCurrentRow(1)
    assert shown_color(widget) == RED


def test_replaced_capture_is_shown_again(qtbot: QtBot, tmp_path: Path) -> None:
    write_capture(tmp_path / "capture.png", RED, 1000)
    widget = make_widget(qtbot)
    widget.watch(tmp_path)
    assert shown_color(widget) == RED
    write_capture(tmp_path / "capture.png", BLUE, 2000)
    widget.refresh()
    assert listed_names(widget) == ["capture.png"]
    assert shown_color(widget) == BLUE


def test_capture_still_being_written_is_read_again(qtbot: QtBot, tmp_path: Path) -> None:
    path = tmp_path / "capture.png"
    path.write_bytes(b"")
    widget = make_widget(qtbot)
    widget.watch(tmp_path)
    assert listed_names(widget) == ["capture.png"]
    assert widget.image.text() == "Loading capture.png..."
    write_capture(path, RED, path.stat().st_mtime)  # Same time, as a write in progress has.
    widget.refresh()
    assert shown_color(widget) == RED


def test_missing_directory_is_watched_until_it_appears(qtbot: QtBot, tmp_path: Path) -> None:
    directory = tmp_path / "captures"
    widget = make_widget(qtbot)
    widget.watch(directory)
    assert widget.directory_label.text() == str(directory)
    assert listed_names(widget) == []
    directory.mkdir()
    write_capture(directory / "capture.png", RED, 1000)
    widget.refresh()
    assert listed_names(widget) == ["capture.png"]


def test_watching_none_clears(qtbot: QtBot, tmp_path: Path) -> None:
    write_capture(tmp_path / "capture.png", RED, 1000)
    widget = make_widget(qtbot)
    widget.watch(tmp_path)
    widget.watch(None)
    assert widget.directory is None
    assert listed_names(widget) == []
    assert widget.directory_label.text() == IDLE_TEXT
    assert not widget.open_button.isEnabled()
    assert widget.image.pixmap().isNull()
