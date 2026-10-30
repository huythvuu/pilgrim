"""Tests for the banner that names a path and opens it."""

import logging
from pathlib import Path

import pytest
from pymeasure.display.Qt import QtCore, QtGui
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import PathBanner

IDLE_TEXT = "Nothing is named."


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[QtCore.QUrl]:
    """Record the URLs the banner asks the system to open instead of opening them."""
    urls: list[QtCore.QUrl] = []
    monkeypatch.setattr(
        QtGui.QDesktopServices, "openUrl", staticmethod(lambda url: urls.append(url) or True)
    )
    return urls


def make_banner(qtbot: QtBot) -> PathBanner:
    banner = PathBanner(IDLE_TEXT, "Open it")
    qtbot.addWidget(banner)
    return banner


def test_idle_until_a_path_is_named(qtbot: QtBot) -> None:
    banner = make_banner(qtbot)
    assert banner.path is None
    assert banner.label.text() == IDLE_TEXT
    assert banner.open_button.text() == "Open it"
    assert not banner.open_button.isEnabled()


def test_naming_a_path_shows_it_and_enables_the_button(qtbot: QtBot, tmp_path: Path) -> None:
    banner = make_banner(qtbot)
    banner.set_path(tmp_path / "missing.csv")  # The path need not exist yet.
    assert banner.path == tmp_path / "missing.csv"
    assert banner.label.text() == str(tmp_path / "missing.csv")
    assert banner.open_button.isEnabled()
    banner.set_path(None)
    assert banner.path is None
    assert banner.label.text() == IDLE_TEXT
    assert not banner.open_button.isEnabled()


def test_button_opens_the_file_named(
    qtbot: QtBot, tmp_path: Path, opened: list[QtCore.QUrl]
) -> None:
    path = tmp_path / "results.csv"
    path.write_text("a,b\n", encoding="utf-8")
    banner = make_banner(qtbot)
    banner.set_path(path)
    banner.open_button.click()
    assert opened == [QtCore.QUrl.fromLocalFile(str(path))]


def test_button_opens_the_directory_named(
    qtbot: QtBot, tmp_path: Path, opened: list[QtCore.QUrl]
) -> None:
    banner = make_banner(qtbot)
    banner.set_path(tmp_path)
    banner.open_button.click()
    assert opened == [QtCore.QUrl.fromLocalFile(str(tmp_path))]


def test_missing_path_is_reported_instead_of_opened(
    qtbot: QtBot, tmp_path: Path, opened: list[QtCore.QUrl], caplog: pytest.LogCaptureFixture
) -> None:
    banner = make_banner(qtbot)
    banner.set_path(tmp_path / "missing.csv")
    with caplog.at_level(logging.WARNING):
        banner.open_path()
    assert opened == []
    assert f"{tmp_path / 'missing.csv'} does not exist yet." in caplog.text
