"""Tests for the experiment log tab."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from pymeasure.display.Qt import QtWidgets
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import ExperimentLogWidget
from pilgrim.gui.ripple.components.experiment_log import IDLE_TEXT


@pytest.fixture
def widget(qtbot: QtBot) -> Iterator[ExperimentLogWidget]:
    """Return a shown log tab that receives the records of a test logger.

    The tab sits in a tab widget, as PyMeasure's log widget expects when a record arrives.
    """
    widget = ExperimentLogWidget("Experiment Log")
    tabs = QtWidgets.QTabWidget()
    qtbot.addWidget(tabs)
    tabs.addTab(widget, widget.name)
    tabs.resize(600, 300)
    tabs.show()
    qtbot.waitExposed(tabs)
    logger = logging.getLogger("tests.experiment_log")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(widget.handler)
    yield widget
    logger.removeHandler(widget.handler)


def test_banner_sits_above_the_records(widget: ExperimentLogWidget) -> None:
    assert widget.name == "Experiment Log"
    assert widget.banner.open_button.text() == "Open log"
    assert widget.banner.label.text() == IDLE_TEXT
    assert not widget.banner.open_button.isEnabled()
    assert widget.banner.geometry().bottom() < widget.view.geometry().top()
    assert widget.view.height() > widget.banner.height()


def test_records_still_reach_the_view(qtbot: QtBot, widget: ExperimentLogWidget) -> None:
    logging.getLogger("tests.experiment_log").info("Connecting to the scope.")
    qtbot.waitUntil(lambda: "Connecting to the scope." in widget.view.toPlainText())


def test_names_the_log_file(widget: ExperimentLogWidget, tmp_path: Path) -> None:
    widget.set_log_path(tmp_path / "ripple_0Vin_1.log")
    assert widget.banner.path == tmp_path / "ripple_0Vin_1.log"
    assert widget.banner.label.text() == str(tmp_path / "ripple_0Vin_1.log")
    assert widget.banner.open_button.isEnabled()
    widget.set_log_path(None)
    assert widget.banner.path is None
    assert not widget.banner.open_button.isEnabled()
