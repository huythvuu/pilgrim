"""Output voltage ripple capture procedure."""

import logging
import math
from itertools import product
from pathlib import Path
from time import sleep
from typing import ClassVar

from pymeasure.experiment import Procedure

from pilgrim.instruments import (
    TektronixMSO58,
    TektronixMSO456Channel,
    TektronixMSO456Measurement,
)

from .parameters import (
    FloatListParameter,
    FloatParameter,
    IntegerParameter,
    StringParameter,
)

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# How often the scope measurement populations are polled while waiting for them to fill.
POLL_INTERVAL = 0.1
# The answers the prompt after the last combination accepts, in any case.
CONTINUE_ANSWER = "continue"
END_ANSWER = "end"

NAME_COLUMN = "Name"
LOAD_COLUMN = "Load"
HORIZONTAL_DIV_COLUMN = "Horizontal division (s)"
BANDWIDTH_COLUMN = "Bandwidth (Hz)"
APPLIED_HORIZONTAL_DIV_COLUMN = "Applied horizontal division (s)"
APPLIED_BANDWIDTH_COLUMN = "Applied bandwidth (Hz)"
PK2PK_MAXIMUM_COLUMN = "PK2PK maximum (V)"
CAPTURE_COLUMN = "Capture"
# The type of the scope measurement whose maximum is recorded with each capture.
PEAK_TO_PEAK = "PK2PK"

# The SI prefixes of the file names, from the largest down; "u" stands for micro.
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
# A whole number this large moves to the next larger SI prefix.
_PREFIX_STEP = 1000


def whole_number_with_prefix(value: float, unit: str) -> str:
    """Return a positive value as a whole number with the SI prefix that keeps it below 1000.

    The value is rounded half up in the largest prefix it reaches 1 in, so 250e6 Hz gives
    ``"250MHz"``, 2.5e-6 s gives ``"3us"``, and 0.9996e-3 s, which rounds to 1000 us, gives
    ``"1ms"``.

    :param value: The value in ``unit``.
    :param unit: The unit symbol, e.g. ``"s"`` or ``"Hz"``.
    :returns: The value with its prefix and unit.
    """
    last = len(_SI_PREFIXES) - 1
    index = next((i for i, (scale, _) in enumerate(_SI_PREFIXES) if value >= scale), last)
    whole = math.floor(value / _SI_PREFIXES[index][0] + 0.5)
    if whole >= _PREFIX_STEP and index > 0:
        index -= 1
        whole = math.floor(value / _SI_PREFIXES[index][0] + 0.5)
    return f"{whole}{_SI_PREFIXES[index][1]}{unit}"


def capture_file_name(
    vin: float, name: str, load: float, horizontal_div: float, bandwidth: float
) -> str:
    """Return the file name of the capture taken at one combination.

    :param vin: The input voltage in V.
    :param name: The procedure name.
    :param load: The load.
    :param horizontal_div: The horizontal scale in seconds per division, named as a whole
        number with an SI prefix (see :func:`whole_number_with_prefix`).
    :param bandwidth: The bandwidth limit in Hz, named like ``horizontal_div``.
    :returns: The file name, e.g. ``"12Vin ripple 0Load 1us 20MHz.png"``.
    """
    time = whole_number_with_prefix(horizontal_div, "s")
    frequency = whole_number_with_prefix(bandwidth, "Hz")
    return f"{vin:g}Vin {name} {load:g}Load {time} {frequency}.png"


class RippleProcedure(Procedure):
    """Capture the output voltage ripple at every combination of load, horizontal scale and
    bandwidth.

    The procedure is run from a command line, since it prompts on standard input. Startup only
    connects to the scope; the user sets it up, e.g. the vertical scale, trigger, persistence
    and measurements. Execution waits for the user to confirm that the scope is ready, then
    goes through :meth:`combinations`: the bandwidth limit of the channel changes first, then
    the horizontal scale, then the load. No instrument here controls the load, so the user
    sets each one and confirms it at a prompt.
    At every combination, the persistence and the measurement statistics restart, and once
    every measurement on the scope has collected ``pop_min`` values, a capture of the screen is
    saved to the local capture directory under :func:`capture_file_name`, e.g.
    ``12Vin ripple 0.5Load 1us 20MHz.png``. The file names hold the requested values; the scope
    rounds the horizontal scale and bandwidth to settings it supports, so the applied values
    are recorded with each capture.
    The scope needs a PK2PK measurement of the channel, whose maximum over all acquisitions is
    recorded with each capture; the first one the scope lists is used. Acquisition stops while
    the maximum is read and the capture is saved, so both show the same statistics, and the
    captures show the scope as stopped.
    After the last combination, the user can make adjustments and let them settle, then types
    ``continue`` to capture every combination again under the same name, replacing the earlier
    captures, or ``end`` to finish. The scope keeps the last combination's bandwidth limit and
    horizontal scale.
    """

    name = StringParameter(
        "Procedure name",
        default="ripple",
        description="Part of every capture file name, also recorded with the results",
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
        description="Scope channel that measures the output voltage, whose bandwidth is stepped",
    )
    vin = FloatParameter(
        "Input voltage",
        units="V",
        default=0,
        description="Nominal input voltage, which starts every capture file name",
    )
    loads = FloatListParameter(
        "Loads",
        default=[0],
        minimum=0,
        maximum=100,
        description="Loads to capture at, in order; the user sets each one when prompted",
    )
    horizontal_divs = FloatListParameter(
        "Horizontal divisions",
        units="s",
        default=[],
        minimum=1e-12,
        maximum=1e3,
        description="Horizontal scales in seconds per division to capture at, in order",
    )
    bandwidths = FloatListParameter(
        "Bandwidths",
        units="Hz",
        default=[],
        minimum=1.0,
        maximum=1e12,
        description="Bandwidth limits of the channel to capture at, in order",
    )
    pop_min = IntegerParameter(
        "Minimum population",
        default=300,
        minimum=1,
        description="Values every measurement on the scope collects before a capture is taken",
    )
    scope_capture_directory = StringParameter(
        "Scope capture directory",
        default="E:/pilgrim/",
        description="Directory on the scope the captures are saved to before they are read",
    )
    local_capture_directory = StringParameter(
        "Local capture directory",
        default="./results/captures",
        description="Directory on this computer the scope captures are saved to",
    )

    DATA_COLUMNS: ClassVar[list[str]] = [
        NAME_COLUMN,
        LOAD_COLUMN,
        HORIZONTAL_DIV_COLUMN,
        BANDWIDTH_COLUMN,
        APPLIED_HORIZONTAL_DIV_COLUMN,
        APPLIED_BANDWIDTH_COLUMN,
        PK2PK_MAXIMUM_COLUMN,
        CAPTURE_COLUMN,
    ]

    scope: TektronixMSO58

    def startup(self) -> None:
        """Check the combinations and connect to the scope.

        :raises ValueError: If ``loads``, ``horizontal_divs`` or ``bandwidths`` is empty, or two
            combinations would share a capture file name, so that one capture would replace
            the other.
        """
        if not self.loads or not self.horizontal_divs or not self.bandwidths:
            raise ValueError(
                "Loads, horizontal divisions and bandwidths need at least one value each."
            )
        names = [
            capture_file_name(self.vin, self.name, *combination)
            for combination in self.combinations()
        ]
        if duplicates := sorted({name for name in names if names.count(name) > 1}):
            raise ValueError(f"Combinations would share capture file names: {duplicates}.")
        Path(self.local_capture_directory).mkdir(parents=True, exist_ok=True)
        log.info("Connecting to the scope.")
        self.scope = TektronixMSO58(self.scope_resource)

    def execute(self) -> None:
        """Wait for the user, then capture every combination until the user ends the run."""
        input("Set up the scope, then press Enter to start the captures. ")
        self._make_scope_capture_directory()
        while True:
            if not self._capture_combinations():
                log.info("Ripple captures stopped.")
                return

    def shutdown(self) -> None:
        """Close the connection to the scope."""
        # Startup can fail before the scope is connected, leaving the attribute unset.
        if getattr(self, "scope", None) is not None:
            self.scope.adapter.close()
        log.info("Ending ripple captures.")

    def combinations(self) -> list[tuple[float, float, float]]:
        """Return the combinations in the order they are captured.

        The bandwidth changes first, then the horizontal scale, then the load, so loads of
        ``[0, 1]``, horizontal scales of ``[1e-6, 1e-3]`` and bandwidths of ``[20e6, 1e9]`` start
        with ``(0, 1e-6, 20e6)``, ``(0, 1e-6, 1e9)``, ``(0, 1e-3, 20e6)``.

        :returns: The ``(load, horizontal division, bandwidth)`` combinations.
        """
        return list(product(self.loads, self.horizontal_divs, self.bandwidths))

    def _capture_combinations(self) -> bool:
        """Capture every combination, prompting the user to set each load.

        :returns: Whether every combination was captured; False when asked to stop first.
        :raises ValueError: If the scope has no PK2PK measurement of the channel, checked before
            the first load is set.
        """
        channel: TektronixMSO456Channel = getattr(self.scope, f"ch{self.channel}")
        measurements = self._attach_measurements()
        peak_to_peak = self._peak_to_peak_measurement(measurements)
        targets = self._population_targets(measurements)
        total = len(self.combinations())
        done = 0
        for load in self.loads:
            input(f"Set the load to {load:g}, then press Enter. ")
            for horizontal_div in self.horizontal_divs:
                self.scope.horizontal_scale = horizontal_div
                applied_horizontal_div = float(self.scope.horizontal_scale)
                _warn_if_rounded("Horizontal division", horizontal_div, applied_horizontal_div, "s")
                for bandwidth in self.bandwidths:
                    channel.bandwidth = bandwidth
                    applied_bandwidth = float(channel.bandwidth)
                    _warn_if_rounded("Bandwidth", bandwidth, applied_bandwidth, "Hz")
                    # Waveforms and statistics of the previous combination would show in the
                    # capture.
                    self.scope.reset_persistence()
                    self._restart_statistics()
                    if not self._wait_for_population(targets):
                        return False
                    path = Path(self.local_capture_directory) / capture_file_name(
                        self.vin, self.name, load, horizontal_div, bandwidth
                    )
                    maximum = self._capture(peak_to_peak, path)
                    log.info(
                        f"Captured load {load:g}, {horizontal_div} s/div, {bandwidth} Hz with a "
                        f"{PEAK_TO_PEAK} maximum of {maximum} V to {path}."
                    )
                    self.emit(
                        "results",
                        {
                            NAME_COLUMN: self.name,
                            LOAD_COLUMN: load,
                            HORIZONTAL_DIV_COLUMN: horizontal_div,
                            BANDWIDTH_COLUMN: bandwidth,
                            APPLIED_HORIZONTAL_DIV_COLUMN: applied_horizontal_div,
                            APPLIED_BANDWIDTH_COLUMN: applied_bandwidth,
                            PK2PK_MAXIMUM_COLUMN: maximum,
                            CAPTURE_COLUMN: str(path),
                        },
                    )
                    done += 1
                    self.emit("progress", 100 * done / total)
        return True

    def _peak_to_peak_measurement(
        self, measurements: list[TektronixMSO456Measurement]
    ) -> TektronixMSO456Measurement:
        """Return the first PK2PK measurement of the channel, whose maximum is recorded.

        :param measurements: The measurements on the scope.
        :returns: The measurement.
        :raises ValueError: If no measurement is a PK2PK of the channel.
        """
        source = f"CH{self.channel}"
        for measurement in measurements:
            if measurement.type == PEAK_TO_PEAK and measurement.source == source:
                return measurement
        raise ValueError(f"The scope has no {PEAK_TO_PEAK} measurement of {source}; add one.")

    def _population_targets(
        self, measurements: list[TektronixMSO456Measurement]
    ) -> list[tuple[TektronixMSO456Measurement, int]]:
        """Return each measurement with the population to wait for.

        A measurement whose own population limit is below ``pop_min`` stops there, so its limit
        is the target.

        :param measurements: The measurements on the scope.
        :returns: The measurements with their target populations.
        """
        targets: list[tuple[TektronixMSO456Measurement, int]] = []
        for measurement in measurements:
            target = self.pop_min
            if measurement.uses_global_population_limit:
                log.warning(
                    f"MEAS{measurement.id} uses the global population limit, which cannot be "
                    f"read; the wait for {self.pop_min} values never ends if the limit is lower."
                )
            elif measurement.population_limit_enabled:
                target = min(target, _integer(measurement.population_limit, "Population limit"))
            targets.append((measurement, target))
        return targets

    def _attach_measurements(self) -> list[TektronixMSO456Measurement]:
        """Return interfaces to the measurements on the scope, in the order the scope lists them.

        The interfaces follow the scope, which the user may have changed since the last call:
        one is added for each new measurement and removed for each deleted one.

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

    @property
    def _scope_directory(self) -> str:
        # The scope's paths are joined with "/", so a trailing one would be doubled.
        return self.scope_capture_directory.rstrip("/")

    def _make_scope_capture_directory(self) -> None:
        """Create ``scope_capture_directory`` on the scope unless it exists already."""
        try:
            self.scope.make_directory(self._scope_directory)
        except ValueError as exc:
            if "257" not in str(exc):  # Anything but a directory that exists already.
                raise

    def _restart_statistics(self) -> None:
        """Clear the scope's acquisitions, so the measurement statistics start afresh."""
        self.scope.clear_acquisitions()
        _ = self.scope.complete  # The scope clears in the background.

    def _wait_for_population(self, targets: list[tuple[TektronixMSO456Measurement, int]]) -> bool:
        """Wait until every measurement has collected its target population.

        :param targets: The measurements with their target populations.
        :returns: Whether the populations filled; False when the procedure was asked to stop
            first.
        """
        while not self.should_stop():
            if all(
                _integer(measurement.population, "Population") >= target
                for measurement, target in targets
            ):
                return True
            sleep(POLL_INTERVAL)
        return False

    def _capture(self, peak_to_peak: TektronixMSO456Measurement, path: Path) -> float:
        """Save a capture to ``path`` and return the peak-to-peak maximum it shows.

        Acquisition stops first, so the screen and the statistics hold still while the maximum
        is read and the capture is saved. It restarts afterwards, also when the capture fails.

        :param peak_to_peak: The PK2PK measurement.
        :param path: The path of the capture on this computer.
        :returns: The maximum of the peak-to-peak values over all acquisitions in V, NaN if
            the scope has no valid value.
        """
        self.scope.acquisition_enabled = False
        _ = self.scope.complete  # Lets the stop take effect before the statistics are read.
        try:
            maximum = _number(peak_to_peak.maximum, f"{PEAK_TO_PEAK} maximum")
            self._save_capture(path)
        finally:
            self.scope.acquisition_enabled = True
        return maximum

    def _save_capture(self, path: Path) -> None:
        """Save a capture of the scope screen to ``path`` on this computer.

        The scope saves the image in ``scope_capture_directory`` on its own file system, from
        where it is read and then deleted.

        :param path: The path of the capture, whose name selects the image format.
        :raises ValueError: If the scope could not save the image.
        """
        scope_path = f"{self._scope_directory}/{path.name}"
        self.scope.save_image(scope_path)
        _ = self.scope.complete  # The scope saves in the background.
        self.scope.check_errors()  # A failed save queues its error only once complete.
        path.write_bytes(self.scope.read_file(scope_path))
        self.scope.delete_file(scope_path)


def _warn_if_rounded(setting: str, requested: float, applied: float, units: str) -> None:
    """Log a warning when the scope applied a setting other than the one requested.

    :param setting: What the setting is, for the message.
    :param requested: The value sent.
    :param applied: The value read back.
    :param units: The units of both values.
    """
    if not math.isclose(requested, applied, rel_tol=1e-6):
        log.warning(f"{setting} of {requested} {units} requested, the scope applied {applied}.")


def _number(value: object, name: str) -> float:
    """Return a value read from the scope as a float.

    :param value: The value read.
    :param name: What the value is, for the error message.
    :returns: The value.
    :raises TypeError: If the driver returns anything but a number.
    """
    if not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number, not {value!r}.")
    return float(value)


def _integer(value: object, name: str) -> int:
    """Return a value read from the scope as an int.

    :param value: The value read.
    :param name: What the value is, for the error message.
    :returns: The value.
    :raises TypeError: If the driver returns anything but an int.
    """
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, not {value!r}.")
    return value


def _measurement_numbers(names: object) -> list[int]:
    """Return the numbers of the measurements the scope lists.

    :param names: The measurement names read from the scope, e.g. ``["MEAS1", "MEAS3"]``.
    :returns: The numbers, e.g. ``[1, 3]``.
    :raises TypeError: If the driver returns anything but a list.
    """
    if not isinstance(names, list):
        raise TypeError(f"Measurement names must be a list, not {names!r}.")
    return [int(str(name).removeprefix("MEAS")) for name in names]
