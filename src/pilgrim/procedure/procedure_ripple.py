"""Output voltage ripple capture procedure."""

import logging
import math
import re
import threading
from collections.abc import Callable
from enum import IntEnum
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, ClassVar, NamedTuple

from pymeasure.experiment import Procedure

from pilgrim.instruments import TektronixMSO58, TektronixMSO456Measurement

from .parameters import (
    BooleanParameter,
    FloatListParameter,
    FloatParameter,
    IntegerListParameter,
    IntegerParameter,
    StringParameter,
)

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# How often the scope measurement populations are polled while waiting for them to fill, and how
# often a paused procedure checks whether it was asked to stop.
POLL_INTERVAL = 0.1
# How long the populations still short of the minimum may stay unchanged, in seconds,
# before a warning names them; a measurement that collects no values would otherwise hold
# the acquisition silently.
STALL_WARNING_INTERVAL = 10.0
# The VISA timeout of the scope in ms; the scope can take seconds to answer while it applies a
# setting or saves a capture, longer than the VISA default.
SCOPE_TIMEOUT = 10_000
#: The prompt of a pause the user asked for through :meth:`RippleProcedure.request_pause`.
PAUSE_PROMPT = "Paused as requested."


class ScopeEvent(IntEnum):
    """Event codes the scope reports that the procedure handles."""

    #: A command could not be carried out, e.g. adding a results table that exists already.
    COMMAND_ERROR = 100
    #: A save, e.g. of a capture, could not be completed.
    SAVE_FAILED = 200
    #: A file or directory path does not exist.
    PATH_NOT_FOUND = 256


VIN_COLUMN = "Vin (V)"
LOAD_COLUMN = "Load"
HORIZONTAL_DIV_COLUMN = "Horizontal Div (s)"
BANDWIDTH_COLUMN = "Bandwidth (Hz)"
VMEAN_COLUMN = "Mean Vmean (V)"
VMAX_COLUMN = "Max Vmax (V)"
VMIN_COLUMN = "Min Vmin (V)"
PK2PK_COLUMN = "Mean Vpk2pk (V)"
OVERSHOOT_COLUMN = "Overshoot (%)"
UNDERSHOOT_COLUMN = "Undershoot (%)"
# The measurement types of the output voltage channel the scope must have; startup adds the
# missing ones.
REQUIRED_TYPES = ["MAXIMUM", "MINIMUM", "MEAN", "PK2PK"]


class Result(NamedTuple):
    """A results column and the scope measurement statistic recorded in it."""

    #: The column of :attr:`RippleProcedure.DATA_COLUMNS`.
    column: str
    #: The type of the measurement of the output voltage channel the statistic comes from.
    measurement_type: str
    #: The statistic of that measurement over all acquisitions, as the measurement interface
    #: names it.
    statistic: str


#: The results read from the scope at every combination, in column order; the overshoot and
#: undershoot columns follow, computed from them.
RESULTS = [
    Result(VMEAN_COLUMN, "MEAN", "mean"),
    Result(VMAX_COLUMN, "MAXIMUM", "maximum"),
    Result(VMIN_COLUMN, "MINIMUM", "minimum"),
    Result(PK2PK_COLUMN, "PK2PK", "mean"),
]
# The results table startup shows on the scope, which lists every measurement with its statistics.
RESULTS_TABLE = "TABLE1"

# The SI prefixes of the capture file names, from the largest down; "u" stands for micro.
_SI_PREFIXES = [
    (1e12, "T"),
    (1e9, "G"),
    (1e6, "M"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
    (1e-6, "u"),
    (1e-9, "n"),
    (1e-12, "p"),
]


def format_with_si_prefix(value: float, unit: str) -> str:
    """Return a value with the SI prefix that puts its number from 1 to below 1000.

    So 1e-6 s gives ``"1us"``, 2.5e-6 s ``"2.5us"`` and 250e6 Hz ``"250MHz"``. The number
    keeps up to six significant digits, and a value below 1 p takes the pico prefix.

    :param value: The value in ``unit``.
    :param unit: The unit symbol, e.g. ``"s"`` or ``"Hz"``.
    :returns: The number with its prefix and unit, without a space.
    """
    scale, prefix = next(
        ((scale, prefix) for scale, prefix in _SI_PREFIXES if abs(value) >= scale),
        _SI_PREFIXES[-1],
    )
    return f"{value / scale:g}{prefix}{unit}"


class RippleProcedure(Procedure):
    """Capture the output voltage ripple on an MSO58 oscilloscope.

    Startup makes sure the scope measures the maximum, minimum, mean and peak-to-peak of
    ``channel``, adding the measurements it lacks to those already set up, e.g. from the front
    panel, see :meth:`add_missing_measurements`, shows them in a results table, and, when
    ``use_cursor`` is set, shows the horizontal bar cursors on ``channel`` at ``cursor_lo``
    percent below and ``cursor_hi`` percent above ``target``, see :meth:`show_cursors`;
    otherwise it hides the cursors.

    No instrument here controls the load, so before the first combination and whenever the load
    changes, the procedure pauses for the user to set it: it hands a prompt such as
    ``"Set the load to 50."`` to :attr:`pause_handler` and waits until :meth:`resume` is called,
    or until it is asked to stop. By default the prompt is printed and Enter resumes, as on a
    command line; a GUI replaces the handler with one that shows the prompt and resumes from a
    button. The user can also ask for a pause at any time through :meth:`request_pause`, which
    the procedure honours at its next step with the prompt ``PAUSE_PROMPT``.
    """

    name = StringParameter(
        "Procedure name",
        default="ripple",
        description="Name of the run, recorded with the results",
    )
    scope_resource = StringParameter(
        "Scope resource",
        default="",
        description="VISA resource name of the oscilloscope",
    )
    channel = IntegerParameter(
        "Channel",
        default=1,
        minimum=1,
        maximum=8,
        description="Scope channel of the output voltage",
    )
    vin = IntegerParameter("Vin", default=0, description="Nominal input voltage")
    loads = IntegerListParameter(
        "Loads",
        default=[0, 50, 100],
        minimum=0,
        maximum=100,
        description="Load values to check",
    )
    horizontal_divs = FloatListParameter(
        "Horizontal divs",
        units="s",
        default=[1e-6, 1e-3],  # 1 us, 1 ms
        description="Horizontal div values to check",
    )
    bandwidths = FloatListParameter(
        "Bandwidths",
        units="Hz",
        default=[20e6, 250e6, 1e9],
        description="Bandwith values to check",
    )
    target = FloatParameter(
        "Rail target",
        units="V",
        default=0,
        description="Target voltage for rail under test",
    )
    use_cursor = BooleanParameter(
        "Use cursors",
        default=True,
        description="Show the cursors about the target on the scope, or hide them",
    )
    cursor_hi = FloatParameter(
        "Cursor high",
        units="%",
        default=0,
        minimum=0,
        description="High cursor value given as a percentage above target",
    )
    cursor_lo = FloatParameter(
        "Cursor low",
        units="%",
        default=0,
        minimum=0,
        description="Low cursor value given as a percentage below target",
    )
    pop_min = IntegerParameter(
        "Sample population minimum",
        default=300,
        minimum=0,
        description="Takes captures until at least the population minimum",
    )
    scope_capture_dir = StringParameter(
        "Scope capture directory",
        default="C:/Temp/Ripple/",
        description="Directory on the scope the captures are saved to before they are moved",
    )
    local_capture_dir = StringParameter(
        "Local capture directory",
        default="./results/captures/",
        description="Directory on PC to save captures",
    )

    DATA_COLUMNS: ClassVar[list[str]] = [
        VIN_COLUMN,
        LOAD_COLUMN,
        HORIZONTAL_DIV_COLUMN,
        BANDWIDTH_COLUMN,
        *(result.column for result in RESULTS),
        OVERSHOOT_COLUMN,
        UNDERSHOOT_COLUMN,
    ]

    scope: TektronixMSO58

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._resumed = threading.Event()
        self._pause_requested = threading.Event()
        #: Receives the prompt each time the procedure pauses; see :meth:`pause`.
        self.pause_handler: Callable[[str], None] = self.prompt_on_console

    @property
    def local_capture_directory(self) -> Path:
        """Get the directory on this computer the captures are moved to.

        It is ``captures`` inside ``local_capture_dir``.
        """
        return Path(self.local_capture_dir) / "captures"

    def startup(self) -> None:
        """Connect to the scope, stop acquisition, add the missing measurements of ``channel``
        and a results table, show or hide the cursors, and reset the measurement populations.

        See :meth:`add_missing_measurements` for the measurements added. The results table is
        ``RESULTS_TABLE``; one the scope shows already is kept as it is. When ``use_cursor`` is
        set, the cursors are shown on ``channel`` about ``target``, see :meth:`show_cursors`;
        otherwise they are hidden, so a capture shows none left from an earlier run.

        :raises ValueError: If ``use_cursor`` is set and a cursor level lies outside the
            voltages the scope shows of ``channel``.
        """
        log.info("Connecting to the scope.")
        self.scope = TektronixMSO58(self.scope_resource, timeout=SCOPE_TIMEOUT)
        state = "acquiring" if self.scope.acquisition_enabled else "stopped"
        log.info(f"Connected to {self.scope.id}, found {state}.")
        self.scope.acquisition_enabled = False
        self.add_missing_measurements()
        self._add_results_table()
        if self.use_cursor:
            self.show_cursors()
        else:
            self.hide_cursors()
        self.scope.clear_acquisitions()
        _ = self.scope.complete  # The scope clears in the background.

    def execute(self) -> None:
        """Acquire at every combination until each scope measurement has ``pop_min`` values.

        The measurements are the ones on the scope, those set up e.g. from its front panel and
        those :meth:`startup` added. Before the first combination and whenever the load changes,
        the procedure pauses until the user has set the load, see :meth:`pause`. At every
        combination, the scope's horizontal scale and the bandwidth limit of ``channel`` are set,
        the statistics restart, acquisition runs until every measurement has collected
        ``pop_min`` values, and then stops. The scope rounds both settings to ones it supports.
        A capture of the screen is then saved on the scope in ``scope_capture_dir`` under
        :meth:`capture_file_name`, and moved to :attr:`local_capture_directory` on this
        computer, so the scope keeps none of the captures; both directories are created first
        if they do not exist. Every combination records one row of :attr:`DATA_COLUMNS`, with
        the mean of the first MEAN measurement of ``channel`` that the scope lists, the maximum
        of its first MAXIMUM measurement, the minimum of its first MINIMUM measurement and the
        mean of its first PK2PK measurement, each over all acquisitions, and the overshoot and
        undershoot computed from them, see :func:`deviation_percent`. Stops early when asked
        to.

        :raises ValueError: If the scope has no measurements or no MEAN, MAXIMUM, MINIMUM or
            PK2PK measurement of ``channel``, which :meth:`startup` adds, or a capture could
            not be saved.
        """
        measurements = self._attach_measurements()
        if not measurements:
            raise ValueError("The scope has no measurements; add at least one on the scope.")
        for measurement in measurements:
            _log_measurement(measurement, self.pop_min)
        recorded = {
            result.measurement_type: self._measurement_of(measurements, result.measurement_type)
            for result in RESULTS
        }
        numbers = [_measurement_number(measurement) for measurement in measurements]
        scope_directory = self._make_scope_capture_dir()
        local_directory = self.local_capture_directory
        local_directory.mkdir(parents=True, exist_ok=True)
        channel = self.scope.channels[self.channel]
        combinations = self.combinations()
        log.info(f"Starting ripple measurement over {len(combinations)} combinations.")
        previous_load: int | None = None
        for done, (load, horizontal_div, bandwidth) in enumerate(combinations, start=1):
            if load != previous_load:
                # No instrument here controls the load, so the user sets it.
                if not self.pause(f"Set the load to {load}."):
                    log.info("Ripple measurement stopped.")
                    return
                previous_load = load
            elif not self._pause_if_requested():
                log.info("Ripple measurement stopped.")
                return
            started = perf_counter()
            self.scope.horizontal_scale = horizontal_div
            channel.bandwidth = bandwidth
            actual_scale, actual_bandwidth = self.scope.horizontal_scale, channel.bandwidth
            # The scope can take seconds over these settings, so the time spent is logged.
            log.info(
                f"Acquiring at load {load}, {actual_scale} s/div, {actual_bandwidth} Hz; set "
                f"and read back in {perf_counter() - started:.1f} s."
            )
            # Starting acquisition carries the statistics on, so they are cleared first.
            self.scope.clear_acquisitions()
            _ = self.scope.complete  # The scope clears in the background.
            self.scope.acquisition_enabled = True
            filled = self._wait_for_population(numbers)
            self.scope.acquisition_enabled = False
            if not filled:
                log.info("Ripple measurement stopped.")
                return
            _ = self.scope.complete  # Lets the stop take effect before the results are read.
            # Acquisitions in flight when the populations filled still add values, so the
            # populations the results come from are read once acquisition has stopped.
            populations = self.scope.read_populations(numbers)
            log.info(f"Acquisition stopped at {_format_populations(populations)}.")
            results = self._read_results(recorded, populations)
            file_name = self.capture_file_name(load, horizontal_div, bandwidth)
            scope_path = f"{scope_directory}/{file_name}"
            self._save_capture(scope_path)
            self._cut_capture(scope_path, local_directory / file_name)
            self.emit(
                "results",
                {
                    VIN_COLUMN: self.vin,
                    LOAD_COLUMN: load,
                    HORIZONTAL_DIV_COLUMN: horizontal_div,
                    BANDWIDTH_COLUMN: bandwidth,
                    **results,
                },
            )
            self.emit("progress", 100 * done / len(combinations))

    def shutdown(self) -> None:
        """Close the connection to the scope."""
        # Startup can fail before the scope is connected, leaving the attribute unset.
        if getattr(self, "scope", None) is not None:
            self.scope.adapter.close()
        log.info("Ending ripple measurement.")

    def pause(self, prompt: str) -> bool:
        """Pause until :meth:`resume` is called, or the procedure is asked to stop.

        The prompt is logged and passed to :attr:`pause_handler`, which must see to it that
        :meth:`resume` gets called, e.g. by reading a line from the console or by enabling a
        button.

        :param prompt: What the user is to do before the procedure continues, e.g.
            ``"Set the load to 50."``.
        :returns: Whether the procedure was resumed; False when it was asked to stop first.
        """
        self._resumed.clear()
        self._pause_requested.clear()  # Any pause satisfies a request for one.
        log.info(f"Paused: {prompt}")
        self.pause_handler(prompt)
        while not self._resumed.wait(POLL_INTERVAL):
            if self.should_stop():
                return False
        log.info("Resumed.")
        return True

    def resume(self) -> None:
        """Let a paused procedure continue; may be called from any thread, e.g. by a GUI."""
        self._resumed.set()

    def request_pause(self) -> None:
        """Ask the running procedure to pause at its next step; may be called from any thread.

        The procedure then pauses with ``PAUSE_PROMPT`` as :meth:`pause` does, at the next poll
        of the populations, stopping the acquisition meanwhile and restarting it on resume, or
        before the next combination. A pause for a load change that comes first satisfies the
        request; a request made while the procedure is paused is satisfied by that pause.
        """
        self._pause_requested.set()

    def _pause_if_requested(self) -> bool:
        """Pause when a pause was requested, see :meth:`request_pause`.

        :returns: Whether the procedure may go on; False when it was asked to stop while paused.
        """
        if not self._pause_requested.is_set():
            return True
        return self.pause(PAUSE_PROMPT)

    def prompt_on_console(self, prompt: str) -> None:
        """Print the prompt on the console and resume the procedure once Enter is pressed.

        The default :attr:`pause_handler`.

        :param prompt: The prompt to print.
        """
        input(f"{prompt} Press Enter to continue. ")
        self.resume()

    def combinations(self) -> list[tuple[int, float, float]]:
        """Return every combination of load, horizontal scale and bandwidth to capture at.

        The load changes slowest and the bandwidth fastest, so loads of ``[0, 50]``, horizontal
        scales of ``[1e-6]`` and bandwidths of ``[20e6, 1e9]`` give ``[(0, 1e-6, 20e6),
        (0, 1e-6, 1e9), (50, 1e-6, 20e6), (50, 1e-6, 1e9)]``.

        :returns: The ``(load, horizontal scale in s/div, bandwidth in Hz)`` combinations, or
            an empty list when any of the three lists is empty.
        """
        return [
            (load, horizontal_div, bandwidth)
            for load in self.loads
            for horizontal_div in self.horizontal_divs
            for bandwidth in self.bandwidths
        ]

    def add_missing_measurements(self) -> list[TektronixMSO456Measurement]:
        """Add the measurements of ``channel`` in :attr:`REQUIRED_TYPES` that the scope lacks.

        The measurements already on the scope, e.g. from its front panel, are kept, and a type
        the channel already has is not added again. Each missing one is added under the next
        number above the highest in use, with its type and source set, and logged.

        :returns: The measurements added, in the order of :attr:`REQUIRED_TYPES`; empty when
            the scope has them all.
        :raises ValueError: If the scope refuses a measurement.
        """
        source = f"CH{self.channel}"
        measurements = self._attach_measurements()
        present = {measurement.type for measurement in measurements if measurement.source == source}
        missing = [kind for kind in REQUIRED_TYPES if kind not in present]
        if not missing:
            log.info(f"The scope has the {', '.join(REQUIRED_TYPES)} measurements of {source}.")
            return []
        number = max((_measurement_number(measurement) for measurement in measurements), default=0)
        added: list[TektronixMSO456Measurement] = []
        for kind in missing:
            number += 1
            measurement = self.scope.add_measurement(number)
            measurement.type = kind
            measurement.source = source
            log.info(f"Added scope measurement MEAS{number}: {kind} of {source}.")
            added.append(measurement)
        return added

    def show_cursors(self) -> tuple[float, float]:
        """Show the horizontal bar cursors on ``channel`` below and above ``target``.

        Cursor A marks the low level, ``cursor_lo`` percent below ``target``, and cursor B the
        high level, ``cursor_hi`` percent above it, see :func:`cursor_levels`. The scope shows
        the cursors, on ``channel``, as soon as the levels are set, and logs them.

        :returns: The low and high levels in volts.
        :raises ValueError: If a level lies outside the voltages the scope shows of ``channel``;
            change the channel's scale, position or offset on the scope first.
        """
        low, high = cursor_levels(self.target, self.cursor_lo, self.cursor_hi)
        self.scope.cursor.set_levels(self.scope.channels[self.channel], low, high)
        log.info(
            f"Cursors shown on CH{self.channel} at {low} V (target {self.target} V - "
            f"{self.cursor_lo} %) and {high} V (target + {self.cursor_hi} %)."
        )
        return low, high

    def hide_cursors(self) -> None:
        """Hide the cursors on the scope, wherever they are, and log it."""
        self.scope.cursor.enabled = False
        log.info("Cursors hidden.")

    def capture_file_name(self, load: int, horizontal_div: float, bandwidth: float) -> str:
        """Return the file name of the capture taken at one combination.

        :param load: The load.
        :param horizontal_div: The horizontal scale in seconds per division.
        :param bandwidth: The bandwidth limit in Hz.
        :returns: The file name, e.g. ``"12Vin ripple 50Load 1us 20MHz.png"``, with both values
            formatted by :func:`format_with_si_prefix`.
        """
        time = format_with_si_prefix(horizontal_div, "s")
        frequency = format_with_si_prefix(bandwidth, "Hz")
        return f"{self.vin}Vin {self.name} {load}Load {time} {frequency}.png"

    def _make_scope_capture_dir(self) -> str:
        """Create ``scope_capture_dir`` on the scope where it does not exist, level by level.

        Each level is checked by making it the scope's working directory, so the working
        directory is left changed.

        :returns: The directory, without a trailing slash.
        """
        directory = self.scope_capture_dir.rstrip("/")
        path, *levels = directory.split("/")
        for level in levels:
            path = f"{path}/{level}"
            try:
                self.scope.working_directory = path
            except ValueError as exc:
                if not _has_event(exc, ScopeEvent.PATH_NOT_FOUND):
                    raise
                log.info(f"Creating {path} on the scope.")
                self.scope.make_directory(path)
        return directory

    def _add_results_table(self) -> None:
        """Show ``RESULTS_TABLE`` on the scope, leaving the table as it is where it exists.

        The scope has no query for the tables it shows, so the table is added and the refusal
        of one that exists already is passed over.

        :raises ValueError: If the scope refuses the table for any other reason.
        """
        try:
            self.scope.add_results_table(RESULTS_TABLE)
        except ValueError as exc:
            if not _has_event(exc, ScopeEvent.COMMAND_ERROR):
                raise
            log.info(f"The scope shows results table {RESULTS_TABLE} already.")
            return
        log.info(f"Added results table {RESULTS_TABLE} to the scope.")

    def _save_capture(self, path: str) -> None:
        """Save a capture of the scope screen to ``path`` on the scope.

        :param path: The path of the capture on the scope, whose extension selects the format.
        :raises ValueError: If the scope could not save the capture.
        """
        started = perf_counter()
        self.scope.save_image(path)
        _ = self.scope.complete  # The scope saves in the background.
        # A failed save queues its event only once complete, and raises nothing by itself.
        events = self.scope.check_errors()
        if any(code == ScopeEvent.SAVE_FAILED for code, _ in events):
            raise ValueError(f"The scope could not save the capture {path}.")
        log.info(f"Capture saved to {path} on the scope in {perf_counter() - started:.1f} s.")

    def _cut_capture(self, scope_path: str, local_path: Path) -> None:
        """Move a capture from the scope to this computer.

        The capture is deleted from the scope only once it has been written here, so a failed
        transfer leaves it on the scope.

        :param scope_path: The path of the capture on the scope.
        :param local_path: The path to write the capture to, replacing any file there.
        """
        started = perf_counter()
        capture = self.scope.read_file(scope_path)
        transfer = perf_counter() - started
        local_path.write_bytes(capture)
        self.scope.delete_file(scope_path)
        # A short transfer is the first sign of a read the scope cut off.
        log.info(f"Capture moved to {local_path}: {len(capture)} bytes in {transfer:.1f} s.")

    def _measurement_of(
        self, measurements: list[TektronixMSO456Measurement], measurement_type: str
    ) -> TektronixMSO456Measurement:
        """Return the first measurement of ``channel`` of a type, e.g. MEAN.

        :param measurements: The measurements on the scope.
        :param measurement_type: The type looked for.
        :returns: The measurement.
        :raises ValueError: If no measurement of the channel has the type.
        """
        source = f"CH{self.channel}"
        for measurement in measurements:
            if measurement.type == measurement_type and measurement.source == source:
                return measurement
        raise ValueError(f"The scope has no {measurement_type} measurement of {source}.")

    def _read_results(
        self, recorded: dict[str, TektronixMSO456Measurement], populations: dict[int, int]
    ) -> dict[str, float]:
        """Read the statistics of :attr:`RESULTS` from the scope, derive the rest, and log them.

        The overshoot is the deviation of Max Vmax from Mean Vmean and the undershoot that of
        Min Vmin, each as a percentage of Mean Vmean, see :func:`deviation_percent`.

        :param recorded: The measurements of ``channel`` by type.
        :param populations: The populations by measurement number the statistics come from.
        :returns: The values by column, in the order of :attr:`DATA_COLUMNS`.
        """
        results: dict[str, float] = {}
        read: list[str] = []
        for result in RESULTS:
            measurement = recorded[result.measurement_type]
            name = f"{result.measurement_type} {result.statistic}"
            value = _number(getattr(measurement, result.statistic), name)
            results[result.column] = value
            population = populations[_measurement_number(measurement)]
            read.append(f"{name} {value} V from {population} values")
        log.info(f"{', '.join(read)}, over {self.scope.acquisition_count} acquisitions.")
        mean = results[VMEAN_COLUMN]
        overshoot = deviation_percent(results[VMAX_COLUMN], mean)
        undershoot = deviation_percent(results[VMIN_COLUMN], mean)
        log.info(f"Overshoot {overshoot} %, undershoot {undershoot} %.")
        results[OVERSHOOT_COLUMN] = overshoot
        results[UNDERSHOOT_COLUMN] = undershoot
        return results

    def _attach_measurements(self) -> list[TektronixMSO456Measurement]:
        """Return interfaces to the measurements on the scope, in the order the scope lists them.

        :returns: The measurements.
        """
        scope = self.scope
        numbers = _measurement_numbers(scope.measurement_names)
        for number, measurement in list(scope.measurements.items()):
            # Querying a deleted measurement through its interface would create it again.
            if number not in numbers:
                scope.remove_child(measurement)
        for number in numbers:
            if number not in scope.measurements:
                # add_measurement would send MEASUrement:ADDNew, which the scope refuses for a
                # measurement that exists, so only the interface is added.
                scope.add_child(
                    TektronixMSO456Measurement,
                    number,
                    collection="measurements",
                    prefix="meas_",
                )
        return [scope.measurements[number] for number in numbers]

    def _wait_for_population(self, numbers: list[int]) -> bool:
        """Wait until every measurement has collected ``pop_min`` values.

        Every poll reads the populations of all the measurements in a single query and logs them
        at debug level. Once the populations still short of the minimum have not changed for
        ``STALL_WARNING_INTERVAL`` seconds, a warning names them.

        :param numbers: The numbers of the measurements on the scope.
        :returns: Whether the populations filled; False when the procedure was asked to stop
            first.
        """
        started = perf_counter()
        previous_short: dict[int, int] | None = None
        unchanged_since = started
        polls = 0
        while not self.should_stop():
            if self._pause_requested.is_set():
                # The acquisition stops while paused, so the capture shows what the user saw.
                self.scope.acquisition_enabled = False
                if not self._pause_if_requested():
                    return False
                self.scope.acquisition_enabled = True
                unchanged_since = perf_counter()  # The pause is no stall.
            polled = perf_counter()
            populations = self.scope.read_populations(numbers)
            now = perf_counter()
            polls += 1
            log.debug(
                f"Poll {polls}: {_format_populations(populations)} (min {self.pop_min}), "
                f"read in {now - polled:.2f} s."
            )
            short = {number: count for number, count in populations.items() if count < self.pop_min}
            if not short:
                log.info(
                    f"Populations filled in {now - started:.1f} s over {polls} polls: "
                    f"{_format_populations(populations)}."
                )
                return True
            # Only the short measurements count as progress: a filled one keeps growing while a
            # stuck one holds the acquisition.
            if short != previous_short:
                previous_short = short
                unchanged_since = now
            elif now - unchanged_since >= STALL_WARNING_INTERVAL:
                log.warning(
                    f"Populations short of {self.pop_min} unchanged for "
                    f"{now - unchanged_since:.0f} s: {_format_populations(short)}; acquisition "
                    "continues until they fill."
                )
                unchanged_since = now
            sleep(POLL_INTERVAL)
        return False


def cursor_levels(target: float, below: float, above: float) -> tuple[float, float]:
    """Return the cursor levels a percentage below and above a target voltage.

    Each percentage is taken of the magnitude of the target, so the low level always lies below
    the high one: a 5 V target with 4 % below and 5 % above gives 4.8 V and 5.25 V, and a -5 V
    target -5.2 V and -4.75 V.

    :param target: The target voltage in volts.
    :param below: The distance of the low level below the target, as a percentage of it.
    :param above: The distance of the high level above the target, as a percentage of it.
    :returns: The low and high levels in volts.
    """
    magnitude = abs(target)
    return target - magnitude * below / 100, target + magnitude * above / 100


def deviation_percent(value: float, mean: float) -> float:
    """Return how far a value lies from a mean, as a percentage of the mean.

    The overshoot of a rail is the deviation of its maximum and the undershoot that of its
    minimum: a maximum of 5.25 V and a minimum of 4.8 V about a mean of 5 V give 5 % and 4 %.
    The mean keeps its sign, so a negative rail gives negative percentages.

    :param value: The value, e.g. the maximum.
    :param mean: The mean the value is compared to.
    :returns: ``abs(value - mean) / mean * 100``, NaN when the mean is zero.
    """
    if mean == 0:
        return math.nan
    return abs(value - mean) / mean * 100


def _has_event(exc: ValueError, event: ScopeEvent) -> bool:
    """Return whether a refusal raised by the scope driver reports ``event``.

    The driver lists the events of a refusal as ``<code>, <message>``, so the code is matched
    whole: event 100 does not match a refusal reporting 2100.

    :param exc: The error the driver raised.
    :param event: The event looked for.
    :returns: Whether the error reports the event.
    """
    return re.search(rf"(?<!\d){int(event)}, ", str(exc)) is not None


def _measurement_numbers(names: object) -> list[int]:
    """Return the numbers of the measurements the scope lists.

    :param names: The measurement names read from the scope, e.g. ``["MEAS1", "MEAS3"]``.
    :returns: The numbers, e.g. ``[1, 3]``.
    :raises TypeError: If the driver returns anything but a list.
    """
    if not isinstance(names, list):
        raise TypeError(f"Measurement names must be a list, not {names!r}.")
    return [int(str(name).removeprefix("MEAS")) for name in names]


def _measurement_number(measurement: TektronixMSO456Measurement) -> int:
    """Return the number of a measurement, e.g. 3 for MEAS3.

    :param measurement: The measurement.
    :returns: Its number.
    :raises TypeError: If the measurement's id is not an int.
    """
    number = measurement.id
    if not isinstance(number, int):
        raise TypeError(f"Measurement id must be an int, not {number!r}.")
    return number


def _number(value: object, name: str) -> float:
    """Return a value read from the scope as a float.

    :param value: The value read.
    :param name: What the value is, for the error message.
    :returns: The value, NaN where the scope has no valid result.
    :raises TypeError: If the driver returns anything but a number.
    """
    if not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number, not {value!r}.")
    return float(value)


def _format_populations(populations: dict[int, int]) -> str:
    """Return populations by measurement number for the log, e.g. ``"MEAS1=312, MEAS3=0"``.

    :param populations: The populations by measurement number.
    :returns: The populations formatted in the order given.
    """
    return ", ".join(f"MEAS{number}={count}" for number, count in populations.items())


def _log_measurement(measurement: TektronixMSO456Measurement, pop_min: int) -> None:
    """Log a measurement's number, type, source and population limit.

    A limit enabled below ``pop_min`` is logged as a warning as well, since the population then
    stops short of the minimum and the acquisition never ends.

    :param measurement: The measurement.
    :param pop_min: The population minimum the measurement has to reach.
    :raises TypeError: If the driver returns anything but an int for the limit.
    """
    number = _measurement_number(measurement)
    kind = f"{measurement.type} of {measurement.source}"
    if measurement.uses_global_population_limit:
        log.info(f"Scope measurement MEAS{number}: {kind}, global population limit.")
        return
    if not measurement.population_limit_enabled:
        log.info(f"Scope measurement MEAS{number}: {kind}, no population limit.")
        return
    limit: object = measurement.population_limit
    if not isinstance(limit, int):
        raise TypeError(f"Population limit must be an int, not {limit!r}.")
    log.info(f"Scope measurement MEAS{number}: {kind}, population limit {limit}.")
    if limit < pop_min:
        log.warning(
            f"MEAS{number} stops at {limit} values, below the minimum of {pop_min}, so its "
            "population can never fill."
        )
