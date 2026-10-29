"""Shared setup of the GUI tests: run Qt without a display and contain the logging changes."""

import logging
import os
from collections.abc import Iterator

import pytest
from pymeasure.display.log import LogHandler

from pilgrim.gui.ripple import RunLogHandler

# Qt reads the platform when the application is created, which pytest-qt does lazily at the
# first test that needs it, so setting it here is early enough.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """Undo what a window does to the root logger, which other tests' log capture relies on.

    PyMeasure's window sets the root logger to INFO and shows its records in the log tab, and the
    ripple window saves them to the log file of each run.
    """
    root = logging.getLogger()
    level = root.level
    yield
    root.setLevel(level)
    for handler in root.handlers[:]:
        if isinstance(handler, LogHandler | RunLogHandler):
            root.removeHandler(handler)
            handler.close()
