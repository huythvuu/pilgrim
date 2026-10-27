"""Output voltage ripple capture procedure."""

import logging
from enum import IntEnum
from pathlib import Path
from time import sleep
from typing import ClassVar

from pymeasure.experiment import Procedure

from pilgrim.instruments import TektronixMSO58, TektronixMSO456Measurement

from .parameters import FloatListParameter, IntegerListParameter, IntegerParameter, StringParameter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# How often the scope measurement populations are polled while waiting for them to fill.
POLL_INTERVAL = 0.1


class ScopeEvent(IntEnum):
    """Event codes the scope reports that the procedure handles."""

    #: A save, e.g. of a capture, could not be completed.
    SAVE_FAILED = 200
    #: A file or directory path does not exist.
    PATH_NOT_FOUND = 256


VIN_COLUMN = "Vin (V)"
LOAD_COLUMN = "Load"
HORIZONTAL_DIV_COLUMN = "Horizontal Div (s)"
BANDWIDTH_COLUMN = "Bandwidth (Hz)"
PK2PK_MAXIMUM_COLUMN = "Peak-to-Peak overall max (V)"
# The type of the scope measurement whose maximum over all acquisitions is recorded.
PEAK_TO_PEAK = "PK2PK"

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
    """Capture the output voltage ripple on an MSO58 oscilloscope."""

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
        description="Scope channel"
    )
    vin = IntegerParameter(
        "Vin",
        default=0,
        description="Nominal input voltage"
    )
    loads = IntegerListParameter(
        "Loads",
        default=[0, 50, 100],
        minimum = 0,
        maximum = 100,
        description="Load values to check"
    )
    horizontal_divs = FloatListParameter(
        "Horizontal divs",
        units="s",
        default=[1e-6, 1e-3],  # 1 us, 1 ms
        description="Horizontal div values to check"
    )
    bandwidths = FloatListParameter(
        "Bandwidths",
        units="Hz",
        default=[20e6, 250e6, 1e9],
        description="Bandwith values to check"
    )
    pop_min = IntegerParameter(
        "Sample population minimum",
        default=300,
        minimum=0,
        description="Takes captures until at least the population minimum"
    )
    scope_capture_dir = StringParameter(
        "Scope capture directory",
        default="C:/Temp/RippleNoise/"
    )
    local_capture_dir = StringParameter(
        "Local capture directory",
        default="./results/captures/",
        description="Directory on PC to save captures"
    )

    DATA_COLUMNS: ClassVar[list[str]] = [
        VIN_COLUMN,
        LOAD_COLUMN,
        HORIZONTAL_DIV_COLUMN,
        BANDWIDTH_COLUMN,
        PK2PK_MAXIMUM_COLUMN,
    ]

    scope: TektronixMSO58

    def startup(self) -> None:
        """Connect to the scope, stop acquisition, and reset the measurement populations."""
        log.info("Connecting to the scope.")
        self.scope = TektronixMSO58(self.scope_resource)
        self.scope.acquisition_enabled = False
        self.scope.clear_acquisitions()
        _ = self.scope.complete  # The scope clears in the background.

    def execute(self) -> None:
        """Acquire at every combination until each scope measurement has ``pop_min`` values.

        The measurements are the ones set up on the scope, e.g. from its front panel. Before the
        first combination and whenever the load changes, the procedure blocks until the user
        has set the load and pressed Enter on standard input. At every combination, the scope's
        horizontal scale and the bandwidth limit of ``channel`` are set, the statistics restart,
        acquisition runs until every measurement has collected ``pop_min`` values, and then
        stops. The scope rounds both settings to ones it supports.
        A capture of the screen is then saved on the scope in ``scope_capture_dir`` under
        :meth:`capture_file_name`, and moved to ``captures`` in ``local_capture_dir`` on this
        computer, so the scope keeps none of the captures; both directories are created first
        if they do not exist. Every combination records one row of :attr:`DATA_COLUMNS`, with
        the maximum over all acquisitions of the first PK2PK measurement of ``channel`` that
        the scope lists. Stops early when asked to.

        :raises ValueError: If the scope has no measurements or no PK2PK measurement of
            ``channel``, or a capture could not be saved.
        """
        log.info("Starting ripple measurement.")
        measurements = self._attach_measurements()
        if not measurements:
            raise ValueError("The scope has no measurements; add at least one on the scope.")
        peak_to_peak = self._peak_to_peak_measurement(measurements)
        scope_directory = self._make_scope_capture_dir()
        local_directory = Path(self.local_capture_dir) / "captures"
        local_directory.mkdir(parents=True, exist_ok=True)
        channel = self.scope.channels[self.channel]
        combinations = self.combinations()
        previous_load: int | None = None
        for done, (load, horizontal_div, bandwidth) in enumerate(combinations, start=1):
            if load != previous_load:
                # No instrument here controls the load, so the user sets it.
                input(f"Set the load to {load}, then press Enter to continue. ")
                previous_load = load
            self.scope.horizontal_scale = horizontal_div
            channel.bandwidth = bandwidth
            log.info(f"Acquiring at load {load}, {self.scope.horizontal_scale} s/div, {channel.bandwidth} Hz.")  # noqa: E501
            # Starting acquisition carries the statistics on, so they are cleared first.
            self.scope.clear_acquisitions()
            _ = self.scope.complete  # The scope clears in the background.
            self.scope.acquisition_enabled = True
            filled = self._wait_for_population(measurements)
            self.scope.acquisition_enabled = False
            if not filled:
                log.info("Ripple measurement stopped.")
                return
            _ = self.scope.complete  # Lets the stop take effect before the maximum is read.
            maximum = _number(peak_to_peak.maximum, f"{PEAK_TO_PEAK} maximum")
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
                    PK2PK_MAXIMUM_COLUMN: maximum,
                },
            )
            self.emit("progress", 100 * done / len(combinations))

    def shutdown(self) -> None:
        """Close the connection to the scope."""
        # Startup can fail before the scope is connected, leaving the attribute unset.
        if getattr(self, "scope", None) is not None:
            self.scope.adapter.close()
        log.info("Ending ripple measurement.")

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
                if str(ScopeEvent.PATH_NOT_FOUND) not in str(exc):
                    raise
                log.info(f"Creating {path} on the scope.")
                self.scope.make_directory(path)
        return directory

    def _save_capture(self, path: str) -> None:
        """Save a capture of the scope screen to ``path`` on the scope.

        :param path: The path of the capture on the scope, whose extension selects the format.
        :raises ValueError: If the scope could not save the capture.
        """
        self.scope.save_image(path)
        _ = self.scope.complete  # The scope saves in the background.
        # A failed save queues its event only once complete, and raises nothing by itself.
        events = self.scope.check_errors()
        if any(code == ScopeEvent.SAVE_FAILED for code, _ in events):
            raise ValueError(f"The scope could not save the capture {path}.")
        log.info(f"Capture saved to {path} on the scope.")

    def _cut_capture(self, scope_path: str, local_path: Path) -> None:
        """Move a capture from the scope to this computer.

        The capture is deleted from the scope only once it has been written here, so a failed
        transfer leaves it on the scope.

        :param scope_path: The path of the capture on the scope.
        :param local_path: The path to write the capture to, replacing any file there.
        """
        local_path.write_bytes(self.scope.read_file(scope_path))
        self.scope.delete_file(scope_path)
        log.info(f"Capture moved to {local_path}.")

    def _peak_to_peak_measurement(
        self, measurements: list[TektronixMSO456Measurement]
    ) -> TektronixMSO456Measurement:
        """Return the first PK2PK measurement of ``channel``, whose maximum is recorded.

        :param measurements: The measurements on the scope.
        :returns: The measurement.
        :raises ValueError: If no measurement is a PK2PK of the channel.
        """
        source = f"CH{self.channel}"
        for measurement in measurements:
            if measurement.type == PEAK_TO_PEAK and measurement.source == source:
                return measurement
        raise ValueError(f"The scope has no {PEAK_TO_PEAK} measurement of {source}; add one.")

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

    def _wait_for_population(self, measurements: list[TektronixMSO456Measurement]) -> bool:
        """Wait until every measurement has collected ``pop_min`` values.

        :param measurements: The measurements on the scope.
        :returns: Whether the populations filled; False when the procedure was asked to stop
            first.
        """
        while not self.should_stop():
            if all(_population(measurement) >= self.pop_min for measurement in measurements):
                return True
            sleep(POLL_INTERVAL)
        return False


def _measurement_numbers(names: object) -> list[int]:
    """Return the numbers of the measurements the scope lists.

    :param names: The measurement names read from the scope, e.g. ``["MEAS1", "MEAS3"]``.
    :returns: The numbers, e.g. ``[1, 3]``.
    :raises TypeError: If the driver returns anything but a list.
    """
    if not isinstance(names, list):
        raise TypeError(f"Measurement names must be a list, not {names!r}.")
    return [int(str(name).removeprefix("MEAS")) for name in names]


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


def _population(measurement: TektronixMSO456Measurement) -> int:
    """Return the population of a scope measurement.

    :param measurement: The measurement.
    :returns: The number of values its statistics hold.
    :raises TypeError: If the driver returns anything but an int.
    """
    population: object = measurement.population
    if not isinstance(population, int):
        raise TypeError(f"Population must be an int, not {population!r}.")
    return population
