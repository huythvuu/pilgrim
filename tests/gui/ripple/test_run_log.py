"""Tests for the log file of each run of the ripple window."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from pilgrim.gui.ripple.run_log import RunLogHandler, unique_log_path


class Runs:
    """Stand in for the manager: hold the results path of the run in progress."""

    def __init__(self) -> None:
        self.results_path: Path | None = None

    def __call__(self) -> Path | None:
        return self.results_path


@pytest.fixture
def runs() -> Runs:
    return Runs()


@pytest.fixture
def logger(runs: Runs) -> Iterator[tuple[logging.Logger, RunLogHandler]]:
    """Return a logger that writes to a run log handler, and the handler."""
    logger = logging.getLogger("tests.run_log")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RunLogHandler(runs)
    logger.addHandler(handler)
    yield logger, handler
    logger.removeHandler(handler)
    handler.close()


def test_unique_log_path_takes_the_results_name(tmp_path: Path) -> None:
    assert unique_log_path(tmp_path / "ripple_0Vin_1.csv") == tmp_path / "ripple_0Vin_1.log"


def test_unique_log_path_appends_an_index_past_existing_files(tmp_path: Path) -> None:
    (tmp_path / "ripple_0Vin_1.log").touch()
    (tmp_path / "ripple_0Vin_1_2.log").touch()
    assert unique_log_path(tmp_path / "ripple_0Vin_1.csv") == tmp_path / "ripple_0Vin_1_3.log"


def test_records_between_runs_are_not_written(
    logger: tuple[logging.Logger, RunLogHandler], tmp_path: Path
) -> None:
    log, handler = logger
    log.info("Idle.")
    assert handler.path is None
    assert list(tmp_path.iterdir()) == []


def test_each_run_gets_its_own_file(
    logger: tuple[logging.Logger, RunLogHandler], runs: Runs, tmp_path: Path
) -> None:
    log, handler = logger
    runs.results_path = tmp_path / "ripple_0Vin_1.csv"
    log.info("First run.")
    handler.end_run()
    runs.results_path = None
    log.info("Idle.")
    runs.results_path = tmp_path / "ripple_0Vin_2.csv"
    log.info("Second run.")
    handler.end_run()
    first = (tmp_path / "ripple_0Vin_1.log").read_text(encoding="utf-8")
    second = (tmp_path / "ripple_0Vin_2.log").read_text(encoding="utf-8")
    assert first.endswith("INFO tests.run_log: First run.\n")
    assert first.count("\n") == 1
    assert second.endswith("INFO tests.run_log: Second run.\n")
    assert second.count("\n") == 1
    assert handler.path == tmp_path / "ripple_0Vin_2.log"


def test_an_existing_log_file_is_not_overwritten(
    logger: tuple[logging.Logger, RunLogHandler], runs: Runs, tmp_path: Path
) -> None:
    log, handler = logger
    (tmp_path / "ripple_0Vin_1.log").write_text("Old run.\n", encoding="utf-8")
    runs.results_path = tmp_path / "ripple_0Vin_1.csv"
    log.info("New run.")
    handler.end_run()
    assert (tmp_path / "ripple_0Vin_1.log").read_text(encoding="utf-8") == "Old run.\n"
    assert "New run." in (tmp_path / "ripple_0Vin_1_2.log").read_text(encoding="utf-8")


def test_end_run_closes_the_file(
    logger: tuple[logging.Logger, RunLogHandler], runs: Runs, tmp_path: Path
) -> None:
    log, handler = logger
    runs.results_path = tmp_path / "ripple_0Vin_1.csv"
    log.info("Run.")
    handler.end_run()
    # Windows refuses to delete a file that is still open.
    (tmp_path / "ripple_0Vin_1.log").unlink()
