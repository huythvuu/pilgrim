"""Log file of each run, saved next to its results file."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

#: The format of the log records, on the console and in the log files.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def unique_log_path(results_path: Path) -> Path:
    """Return a path for the log file of a run whose results go to ``results_path``.

    The log file takes the name of the results file with the ``.log`` extension. If a file of
    that name already exists, it is left alone and an index is appended instead, as in
    ``ripple_0Vin_1_2.log``.

    :param results_path: The path of the results file.
    """
    path = results_path.with_suffix(".log")
    index = 2
    while path.exists():
        path = results_path.with_name(f"{results_path.stem}_{index}.log")
        index += 1
    return path


class RunLogHandler(logging.Handler):
    """Write log records to the log file of the run in progress, next to its results file.

    Each run gets its own file, named by :func:`unique_log_path` and created by
    :meth:`start_run` or at the first record of the run, whichever comes first. Records emitted
    between runs are not written. Call :meth:`end_run` once a run has ended to close its file.

    :param results_path: Return the path of the results file of the run in progress, or None
        when no run is in progress.
    """

    def __init__(self, results_path: Callable[[], Path | None]) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter(LOG_FORMAT))
        self._results_path = results_path
        self._run_results_path: Path | None = None
        self._stream: TextIO | None = None
        self.path: Path | None = None

    def emit(self, record: logging.LogRecord) -> None:
        """Write the record to the log file of the run in progress.

        :param record: The log record.
        """
        results_path = self._results_path()
        if results_path is None:
            return
        try:
            self._open_run(results_path)
            stream = self._stream
            if stream is not None:
                stream.write(f"{self.format(record)}\n")
                stream.flush()
        except Exception:
            self.handleError(record)

    def start_run(self, results_path: Path) -> Path:
        """Create the log file of a run that starts and return its path.

        The file is the one the records of the run go to; a record that came first has already
        created it.

        :param results_path: The path of the results file of the run.
        """
        self.acquire()
        try:
            return self._open_run(results_path)
        finally:
            self.release()

    def end_run(self) -> None:
        """Close the log file of the run that has ended."""
        self.acquire()
        try:
            self._close_stream()
        finally:
            self.release()

    def close(self) -> None:
        """Close the log file and the handler."""
        self.end_run()
        super().close()

    def _open_run(self, results_path: Path) -> Path:
        """Open the log file of the run, unless it is open already, and return its path."""
        path = self.path
        if self._stream is None or path is None or results_path != self._run_results_path:
            self._close_stream()
            path = unique_log_path(results_path)
            # Exclusive creation, so an existing log file is never overwritten.
            self._stream = path.open("x", encoding="utf-8")
            self.path = path
            self._run_results_path = results_path
        return path

    def _close_stream(self) -> None:
        if self._stream is not None:
            self._stream.close()
        self._stream = None
        self._run_results_path = None
