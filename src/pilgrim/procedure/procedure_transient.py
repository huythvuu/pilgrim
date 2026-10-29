"""Transient response measurement procedure."""

import csv
import logging
import math
from collections.abc import Iterator
from itertools import pairwise
from operator import itemgetter
from pathlib import Path
from time import sleep
from typing import ClassVar, NamedTuple

from pymeasure.experiment import Procedure

from pilgrim.instruments import (
    TektronixAFG31051,
    TektronixMSO58,
    TektronixMSO456Channel,
    TektronixMSO456Measurement,
)

from .parameters import (
    FloatListParameter,
    FloatParameter,
    FloatRangeListParameter,
    IntegerParameter,
    StringParameter,
)

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# How often the scope measurement populations are polled while waiting for them to fill.
POLL_INTERVAL = 0.1
# The width of the scope screen in divisions, fixed by the MSO 4/5/6 series.
HORIZONTAL_DIVISIONS = 10
# The directory on the scope's file system a capture is saved to before it is read.
SCOPE_CAPTURE_DIRECTORY = "C:/pilgrim"

# The conditions recorded with every row, ahead of the scope measurements.
LOAD_LOW_COLUMN = "Load low (V)"
LOAD_HIGH_COLUMN = "Load high (V)"
FREQUENCY_COLUMN = "Frequency (Hz)"
DUTY_COLUMN = "Duty (%)"
CONDITION_COLUMNS = [LOAD_LOW_COLUMN, LOAD_HIGH_COLUMN, FREQUENCY_COLUMN, DUTY_COLUMN]
# The columns only the results have: which extreme a row holds and the path of its capture.
EXTREME_COLUMN = "Extreme"
CAPTURE_COLUMN = "Capture"


class Signal(NamedTuple):
    """One signal the scope shows and measures."""

    #: The name of the signal in the data columns, e.g. ``"Vout"``.
    name: str
    #: The scope channel the signal is on, e.g. ``"CH1"``.
    source: str


class ScopeMeasurement(NamedTuple):
    """One measurement the scope takes during the sweeps."""

    #: The signal measured.
    signal: Signal
    #: The measurement type, e.g. ``"RISETIME"``.
    measurement_type: str
    #: The units of the measurement's values, e.g. ``"s"``.
    units: str

    def column(self, statistic: str) -> str:
        """Return the data column name of one statistic of this measurement.

        :param statistic: The statistic, a measurement property name such as
            ``"standard_deviation"``.
        :returns: The column name, e.g. ``"Iout RISETIME standard deviation (s)"``; the
            population, a count, carries no units.
        """
        name = f"{self.signal.name} {self.measurement_type} {statistic.replace('_', ' ')}"
        return name if statistic == "population" else f"{name} ({self.units})"


class Extreme(NamedTuple):
    """One row reported after the sweeps: the row holding an extreme of one data column."""

    #: The label of the row, e.g. ``"Highest Vmax"``.
    label: str
    #: The data column searched.
    column: str
    #: Whether the highest value of the column is sought (otherwise the lowest).
    highest: bool


def _measurement_columns(measurements: list[ScopeMeasurement], statistics: list[str]) -> list[str]:
    """Return the data columns of every statistic of every scope measurement, in order.

    :param measurements: The scope measurements.
    :param statistics: The statistics read from every measurement.
    :returns: The column names.
    """
    return [m.column(statistic) for m in measurements for statistic in statistics]


def _number(value: object, name: str) -> float:
    """Return a value read from an instrument as a float.

    :param value: The value read.
    :param name: What the value is, for the error message.
    :returns: The value.
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


class TransientProcedure(Procedure):
    """Measure the transient response of an output voltage to load steps pulsed by an AFG.

    The AFG drives the load with a pulse whose levels, frequency, duty cycle and edge times are
    parameters. The load is stepped through each load range, and at every step the pulse
    frequency is swept through the checkpoints. After every frequency change, the pulse edges
    the AFG may have moved are set back to equal times.
    The scope shows the output voltage on CH1 and the output current on CH2 with infinite
    persistence, triggers on CH2 at the midpoint of the current step, and scales its horizontal
    axis to the pulse frequency so that the chosen number of cycles fills the screen. It
    measures the output voltage's mean, maximum, minimum and peak-to-peak, and the output
    current's top, mean, rise time and fall time.
    At every frequency, the measurement statistics restart and are recorded once each
    measurement has collected the minimum population, together with the load, frequency and
    duty cycle, into a raw data file in the capture directory. After the sweeps, the rows with
    the highest maximum, lowest minimum and highest peak-to-peak output voltage are the
    results: their conditions are reproduced, a scope capture of each is saved to the capture
    directory, and its path is recorded with the row.
    """

    # The scope setup is fixed rather than parameters, since it defines the data columns, which
    # PyMeasure needs before a run.
    #: The output voltage, whose extremes are the results.
    VOUT: ClassVar[Signal] = Signal("Vout", "CH1")
    #: The output current, whose step the scope triggers on.
    IOUT: ClassVar[Signal] = Signal("Iout", "CH2")
    #: The measurements the scope takes, numbered from 1 in this order.
    SCOPE_MEASUREMENTS: ClassVar[list[ScopeMeasurement]] = [
        ScopeMeasurement(VOUT, "MEAN", "V"),
        ScopeMeasurement(VOUT, "MAXIMUM", "V"),
        ScopeMeasurement(VOUT, "MINIMUM", "V"),
        ScopeMeasurement(VOUT, "PK2PK", "V"),
        ScopeMeasurement(IOUT, "TOP", "V"),
        ScopeMeasurement(IOUT, "MEAN", "V"),
        ScopeMeasurement(IOUT, "RISETIME", "s"),
        ScopeMeasurement(IOUT, "FALLTIME", "s"),
    ]
    #: The statistics recorded from every measurement, as measurement property names.
    STATISTICS: ClassVar[list[str]] = [
        "mean",
        "minimum",
        "maximum",
        "standard_deviation",
        "population",
    ]
    #: The rows reported after the sweeps, each the row holding one extreme of the output
    #: voltage.
    EXTREMES: ClassVar[list[Extreme]] = [
        Extreme(
            "Highest Vmax",
            ScopeMeasurement(VOUT, "MAXIMUM", "V").column("maximum"),
            highest=True,
        ),
        Extreme(
            "Lowest Vmin",
            ScopeMeasurement(VOUT, "MINIMUM", "V").column("minimum"),
            highest=False,
        ),
        Extreme(
            "Highest peak-to-peak",
            ScopeMeasurement(VOUT, "PK2PK", "V").column("maximum"),
            highest=True,
        ),
    ]

    # The defaults are the bench instruments, so the procedure runs against them unless told
    # otherwise.
    name = StringParameter(
        "Procedure name",
        default="transient",
        description="Prefix of every output file name, also recorded with the results",
    )
    afg_resource = StringParameter(
        "AFG resource",
        default="",
        description="VISA resource name of the function generator",
    )
    scope_resource = StringParameter(
        "Scope resource",
        default="",
        description="VISA resource name of the oscilloscope",
    )
    capture_dest = StringParameter(
        "Capture destination",
        default="./results/captures",
        description="Directory the raw data file and the scope captures are saved to",
    )

    frequency = FloatParameter(
        "Pulse frequency",
        units="Hz",
        default=1e3,
        minimum=1e-3,
        maximum=1e6,
        description="Pulse frequency while the instruments are set up, before the sweeps",
    )
    dutycycle = FloatParameter("Duty cycle", units="%", default=50.0, minimum=0.001, maximum=99.999)
    high_level = FloatParameter("High level", units="V", default=0.5)
    low_level = FloatParameter("Low level", units="V", default=0.0)
    leading_edge = FloatParameter("Leading edge", units="us", default=1, minimum=1)
    trailing_edge = FloatParameter("Trailing edge", units="us", default=1, minimum=1)
    afg_delay = FloatParameter(
        "AFG delay",
        units="s",
        default=3.0,
        minimum=0.0,
        description="Time for the output to settle after it turns on, before the scope trigger "
        "level is taken from it",
    )

    checkpoints = FloatListParameter(
        "Checkpoint frequencies",
        units="Hz",
        default=[],
        description="Frequencies the sweep passes through, in order",
    )
    sweep_ratio = FloatParameter(
        "Sweep ratio",
        default=0.01,
        minimum=0.0001,
        maximum=1.0,
        description="Fraction of the range between neighbouring checkpoints to step by",
    )
    load_ranges = FloatRangeListParameter(
        "Load steps",
        minimum=0.0,
        maximum=1.0,
        default=[],
        description="Ranges from 0 (low level) to 1 (high level) to step the load between",
    )
    soak = FloatParameter(
        "Soak time",
        units="s",
        default=0.0,
        minimum=0.0,
        description="Settling time after every load and frequency change",
    )

    cycles_on_screen = IntegerParameter(
        "Cycles on screen",
        default=1,
        minimum=1,
        description="Pulse cycles the scope's horizontal scale fits on the screen",
    )
    pop_min = IntegerParameter(
        "Minimum population",
        default=300,
        minimum=1,
        description="Values every scope measurement collects before a row is recorded",
    )

    #: The columns of the raw data file, which holds every row measured in the sweeps.
    RAW_COLUMNS: ClassVar[list[str]] = [
        *CONDITION_COLUMNS,
        *_measurement_columns(SCOPE_MEASUREMENTS, STATISTICS),
    ]
    #: The columns of the results, one row per extreme in :attr:`EXTREMES`.
    DATA_COLUMNS: ClassVar[list[str]] = [EXTREME_COLUMN, *RAW_COLUMNS, CAPTURE_COLUMN]

    afg: TektronixAFG31051
    scope: TektronixMSO58
    #: The rows measured so far in the sweeps, as written to the raw data file.
    rows: list[dict[str, float]]

    def startup(self) -> None:
        """Connect to the scope and the AFG, configure them, and turn the stimulus on."""
        self.capture_directory.mkdir(parents=True, exist_ok=True)
        log.info("Setting up instruments.")
        self._set_up_scope()
        self._fit_scope_to_frequency(self.frequency)
        self._set_up_afg()
        # The midpoint comes from the current step, which only exists once the AFG output is on.
        self._set_scope_trigger_level()

    def execute(self) -> None:
        """Run the sweeps, save every row to the raw data file, then record the worst cases.

        The raw data file, ``<name>_raw_data.csv`` in the capture directory, receives every
        row as it is measured. The results hold one row per extreme in :attr:`EXTREMES`, taken
        from the raw data, with the path of a scope capture of its conditions, saved next to
        the raw data file as ``<name>_<load>_<duty>_<frequency>_<extreme>.png``. Stops without
        recording the worst cases when asked to.
        """
        self.rows = []
        raw_path = self.capture_directory / f"{self.name}_raw_data.csv"
        with raw_path.open("w", newline="", encoding="utf-8") as raw_file:
            writer = csv.DictWriter(raw_file, fieldnames=self.RAW_COLUMNS)
            writer.writeheader()
            for row in self._sweep():
                self.rows.append(row)
                writer.writerow(row)
                raw_file.flush()  # Keeps the rows measured so far if the run dies.
        log.info(f"Raw data saved to {raw_path}.")
        if self.should_stop():
            log.info("Transient measurement stopped before the worst cases were recorded.")
            return
        self._record_extremes()

    def shutdown(self) -> None:
        """Turn off the stimulus and leave the instruments in a safe state."""
        # Startup can fail before either instrument is connected, leaving its attribute unset.
        if getattr(self, "afg", None) is not None:
            self.afg.ch1.output_enabled = False
            self.afg.adapter.close()
        if getattr(self, "scope", None) is not None:
            self.scope.adapter.close()
        log.info("Ending transient measurement.")

    @property
    def capture_directory(self) -> Path:
        """Get the host directory the raw data file and the scope captures are saved to."""
        return Path(self.capture_dest)

    def sweep_frequencies(self) -> list[float]:
        """Return the frequencies in Hz that the sweep steps through.

        Between neighbouring checkpoints, the sweep steps by the sweep ratio of their difference,
        so a ratio of 0.01 steps by 9 Hz from 100 Hz to 1 kHz, then by 90 Hz to 10 kHz.
        When the ratio does not divide 1 evenly, the last step before each checkpoint is shorter.

        :returns: The sweep frequencies in Hz, ending on the last checkpoint, or an empty list
            without checkpoints.
        """
        if not self.checkpoints:
            return []
        # Without rounding, a ratio of 1 / 49 gives 49.00000000000001 here, and the extra step
        # would land on the next checkpoint and repeat it.
        steps = math.ceil(round(1 / self.sweep_ratio, 9))
        frequencies: list[float] = []
        for start, stop in pairwise(self.checkpoints):
            step = (stop - start) * self.sweep_ratio
            frequencies.extend(start + i * step for i in range(steps))
        frequencies.append(self.checkpoints[-1])
        return frequencies

    def calc_load_steps(self) -> list[tuple[float, float]]:
        """Return the load steps as ranges of voltages in V.

        Each load range fraction is placed between the low level, at 0, and the high level, at 1,
        so with levels of -250 mV and 250 mV the range (0.25, 0.75) becomes (-125 mV, 125 mV).

        :returns: A ``(low, high)`` voltage pair in V for each load range, in the same order.
        """
        low, high = self.low_level, self.high_level
        # Weighting both levels lands 0 and 1 exactly on them, which low + f * (high - low)
        # can miss by a rounding error.
        return [
            (low * (1 - start) + high * start, low * (1 - stop) + high * stop)
            for start, stop in self.load_ranges
        ]

    def horizontal_scale_for(self, frequency: float) -> float:
        """Return the scope's horizontal scale in s/div that fits the cycles on the screen.

        ``cycles_on_screen`` cycles of ``frequency`` span the :data:`HORIZONTAL_DIVISIONS`
        divisions of the screen, so 1 kHz gives 100 µs/div for one cycle.

        :param frequency: The pulse frequency in Hz.
        :returns: The horizontal scale in seconds per division.
        """
        return self.cycles_on_screen / (HORIZONTAL_DIVISIONS * frequency)

    @classmethod
    def find_extremes(cls, rows: list[dict[str, float]]) -> dict[str, dict[str, float]]:
        """Return the row holding each extreme in :attr:`EXTREMES`, by label.

        A row whose searched value is NaN, which the scope reports for a result it cannot
        compute, is passed over; a label no row has a value for is left out.

        :param rows: The rows of the raw data.
        :returns: The rows by extreme label, e.g. ``"Highest Vmax"``.
        """
        extremes: dict[str, dict[str, float]] = {}
        for label, column, highest in cls.EXTREMES:
            valid = [row for row in rows if not math.isnan(row[column])]
            if valid:
                pick = max if highest else min
                extremes[label] = pick(valid, key=itemgetter(column))
        return extremes

    def _sweep(self) -> Iterator[dict[str, float]]:
        """Step the load through each load range and sweep the frequency at every step.

        At every frequency, the scope statistics restart once the stimulus has settled and one
        row is measured when every measurement has collected ``pop_min`` values.

        :returns: Every row measured, as it is measured. Ends early when asked to stop, which
            leaves the current frequency unmeasured.
        """
        frequencies = self.sweep_frequencies()
        load_steps = self.calc_load_steps()
        total = len(load_steps) * len(frequencies)
        done = 0

        for low, high in load_steps:
            self._set_load(low, high)
            for frequency in frequencies:
                self._set_frequency(frequency)
                self._restart_statistics()
                if not self._wait_for_population():
                    return
                yield self._read_row(low, high, frequency)
                done += 1
                self.emit("progress", 100 * done / total)

    def _record_extremes(self) -> None:
        """Reproduce each extreme of the rows measured, capture it, and emit it as a result."""
        for label, row in self.find_extremes(self.rows).items():
            capture = self.capture_directory / (
                f"{self.name}_{row[LOAD_LOW_COLUMN]:g}V_{row[LOAD_HIGH_COLUMN]:g}V_"
                f"{row[DUTY_COLUMN]:g}D_{row[FREQUENCY_COLUMN]:.3e}Hz_"
                f"{label.lower().replace(' ', '_').replace('-', '_')}.png"
            )
            if not self._capture_extreme(row, capture):
                log.info("Transient measurement stopped before the worst cases were captured.")
                return
            log.info(
                f"{label} at {row[FREQUENCY_COLUMN]} Hz, load {row[LOAD_LOW_COLUMN]} V to "
                f"{row[LOAD_HIGH_COLUMN]} V, {row[DUTY_COLUMN]} % duty; captured to {capture}."
            )
            self.emit("results", {EXTREME_COLUMN: label, **row, CAPTURE_COLUMN: str(capture)})

    def _set_load(self, low: float, high: float) -> None:
        """Set the AFG levels to the load step and let the load settle for the soak time.

        :param low: The low level in V.
        :param high: The high level in V.
        """
        channel = self.afg.ch1
        # Either order is safe: the AFG moves the other level along when they would cross.
        channel.amplitude_low, channel.amplitude_high = low, high
        # The soak time is an assumed settling time for the load, not a measured one.
        sleep(self.soak)

    def _set_frequency(self, frequency: float) -> None:
        """Set the AFG frequency, keep its edges equal, fit the scope to it and soak.

        :param frequency: The pulse frequency in Hz.
        """
        self.afg.ch1.frequency = frequency
        self._balance_afg_edges()
        self._fit_scope_to_frequency(frequency)
        sleep(self.soak)

    def _read_row(self, low: float, high: float, frequency: float) -> dict[str, float]:
        """Read one row of the raw data: the conditions and every measurement statistic.

        :param low: The low level of the load step in V.
        :param high: The high level of the load step in V.
        :param frequency: The pulse frequency in Hz.
        :returns: The row by column name, in :attr:`RAW_COLUMNS` order.
        """
        return {
            LOAD_LOW_COLUMN: low,
            LOAD_HIGH_COLUMN: high,
            FREQUENCY_COLUMN: frequency,
            # Read back, since the AFG may move the duty cycle along with the frequency.
            DUTY_COLUMN: _number(self.afg.ch1.pulse_dutycycle, "Duty cycle"),
            **self._read_measurements(),
        }

    def _capture_extreme(self, row: dict[str, float], path: Path) -> bool:
        """Reproduce the conditions of ``row`` and save a scope capture of them to ``path``.

        The persistence is reset so that the capture shows these conditions only, and the
        capture is taken once every measurement has collected ``pop_min`` values again.

        :param row: A row of the raw data.
        :param path: The host path of the capture.
        :returns: Whether the capture was saved; False when asked to stop first.
        """
        self._set_load(row[LOAD_LOW_COLUMN], row[LOAD_HIGH_COLUMN])
        self._set_frequency(row[FREQUENCY_COLUMN])
        self.scope.reset_persistence()
        self._restart_statistics()
        if not self._wait_for_population():
            return False
        self._save_capture(path)
        return True

    def _set_up_afg(self) -> None:
        """Connect to the AFG, configure the pulse, turn the output on and let it settle."""
        self.afg = TektronixAFG31051(self.afg_resource)
        channel = self.afg.ch1
        # The output may still be on from an earlier session; keep it off while the pulse changes.
        channel.output_enabled = False
        channel.shape = "PULS"
        channel.frequency = self.frequency
        channel.pulse_dutycycle = self.dutycycle
        channel.amplitude_high = self.high_level
        channel.amplitude_low = self.low_level
        channel.pulse_leading = self.leading_edge
        channel.pulse_trailing = self.trailing_edge
        channel.output_enabled = True
        sleep(self.afg_delay)

    def _balance_afg_edges(self) -> None:
        """Set the AFG's leading and trailing edge times to their average when they differ.

        A change of frequency narrows the range the AFG allows for the edge times, and the AFG
        moves an edge that falls outside it on its own, which can leave the two edges unequal.
        The longer edge is shortened first, so that the sum of the two never grows beyond what
        the AFG allowed.
        """
        channel = self.afg.ch1
        leading = _number(channel.pulse_leading, "Leading edge")
        trailing = _number(channel.pulse_trailing, "Trailing edge")
        if math.isclose(leading, trailing):
            return
        edge = (leading + trailing) / 2
        if leading > trailing:
            channel.pulse_leading = edge
            channel.pulse_trailing = edge
        else:
            channel.pulse_trailing = edge
            channel.pulse_leading = edge
        log.info(
            f"AFG edges of {leading} us and {trailing} us were unequal; both set to {edge} us."
        )

    def _set_up_scope(self) -> None:
        """Connect to the scope, show the output voltage and current, and add the measurements.

        Replaces every measurement on the scope, including those added from its front panel,
        with :attr:`SCOPE_MEASUREMENTS`, triggers on an edge of the output current and turns on
        infinite persistence.
        """
        self.scope = TektronixMSO58(self.scope_resource)
        scope = self.scope
        for signal in (self.VOUT, self.IOUT):
            channel: TektronixMSO456Channel = getattr(scope, signal.source.lower())
            channel.display_enabled = True
            channel.global_display_enabled = True

        scope.delete_all_measurements()
        for number, spec in enumerate(self.SCOPE_MEASUREMENTS, start=1):
            measurement = scope.add_measurement(number)
            measurement.type = spec.measurement_type
            measurement.source = spec.signal.source

        scope.trigger_type = "EDGE"
        scope.trigger_source = self.IOUT.source
        scope.persistence = "INFPERSIST"
        try:
            scope.make_directory(SCOPE_CAPTURE_DIRECTORY)
        except ValueError as exc:
            if "257" not in str(exc):  # Anything but a directory that exists already.
                raise

    def _set_scope_trigger_level(self) -> None:
        """Set the scope trigger level to the midpoint of the current step and restart the display.

        Call once the AFG output is on, since the midpoint is taken from the signal present.
        """
        scope = self.scope
        # A previous run leaves acquisition stopped, and the midpoint needs a live signal.
        scope.acquisition_enabled = True
        scope.set_trigger_level_to_midpoint()
        # The scope silently drops set commands sent within about 2 s of SETLevel.
        _ = scope.complete
        level = getattr(scope, self.IOUT.source.lower()).trigger_level
        log.info(f"Scope trigger level on {self.IOUT.source} set to {level} V.")
        # Untriggered waveforms from before the level was set would otherwise stay in the
        # infinite persistence and the measurement statistics.
        scope.reset_persistence()
        scope.clear_acquisitions()

    def _fit_scope_to_frequency(self, frequency: float) -> None:
        """Set the scope's horizontal scale to fit the cycles of ``frequency`` on the screen.

        The scope rounds the scale to one of its steps, so the scale that applies is logged.

        :param frequency: The pulse frequency in Hz.
        """
        self.scope.horizontal_scale = self.horizontal_scale_for(frequency)
        log.debug(f"Scope horizontal scale set to {self.scope.horizontal_scale} s/div.")

    def _restart_statistics(self) -> None:
        """Clear the scope's acquisitions and start acquiring, so the statistics start afresh."""
        # Starting acquisition carries the statistics on, so they are cleared first.
        self.scope.clear_acquisitions()
        _ = self.scope.complete  # The scope clears in the background.
        self.scope.acquisition_enabled = True

    def _wait_for_population(self) -> bool:
        """Wait until every scope measurement has collected ``pop_min`` values, then stop.

        Acquisition stops once the populations fill or the procedure is asked to stop, so the
        statistics and the screen hold still while they are read. A per-cycle measurement type
        adds several values per acquisition, so a population can end above ``pop_min``.

        :returns: Whether the populations filled; False when the procedure was asked to stop
            first.
        """
        measurements = list(self.scope.measurements.values())
        filled = False
        while not self.should_stop():
            if all(_population(measurement) >= self.pop_min for measurement in measurements):
                filled = True
                break
            sleep(POLL_INTERVAL)
        self.scope.acquisition_enabled = False
        _ = self.scope.complete  # Lets the stop take effect before the statistics are read.
        return filled

    def _read_measurements(self) -> dict[str, float]:
        """Read every statistic in :attr:`STATISTICS` of every scope measurement.

        :returns: The values by data column name, in :attr:`RAW_COLUMNS` order.
        """
        readings: dict[str, float] = {}
        for number, spec in enumerate(self.SCOPE_MEASUREMENTS, start=1):
            measurement = self.scope.measurements[number]
            for statistic in self.STATISTICS:
                readings[spec.column(statistic)] = getattr(measurement, statistic)
        return readings

    def _save_capture(self, path: Path) -> None:
        """Save a capture of the scope screen to ``path`` on the host.

        The scope saves the image in :data:`SCOPE_CAPTURE_DIRECTORY` on its own file system,
        from where it is read and then deleted.

        :param path: The host path of the capture, whose name selects the image format.
        :raises ValueError: If the scope could not save the image, detected once the read of
            the file times out.
        """
        scope_path = f"{SCOPE_CAPTURE_DIRECTORY}/{path.name}"
        self.scope.save_image(scope_path)
        _ = self.scope.complete  # The scope saves in the background.
        self.scope.check_errors()  # A failed save queues its error only once complete.
        path.write_bytes(self.scope.read_file(scope_path))
        self.scope.delete_file(scope_path)
