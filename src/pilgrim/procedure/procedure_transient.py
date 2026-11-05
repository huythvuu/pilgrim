"""Transient response measurement procedure."""

import logging
import math
import re
import threading
from collections.abc import Callable
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, ClassVar, NamedTuple, Self

import numpy as np
from numpy.typing import NDArray
from pymeasure.experiment import Procedure

from pilgrim.instruments import TektronixAFG31051, TektronixMSO58, TektronixMSO456Measurement

from .parameters import BooleanParameter, FloatParameter, IntegerParameter, StringParameter
from .procedure_ripple import (
    DEFAULT_RESULTS_DIRECTORY,
    ScopeEvent,
    capture_hyperlink,
    format_with_si_prefix,
)

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# How often the scope measurement populations are polled while waiting for them to fill, and how
# often a paused procedure checks whether it was asked to stop.
POLL_INTERVAL = 0.1
# How long the populations still short of the minimum may stay unchanged, in seconds, before a
# warning names them; a measurement that collects no values would otherwise hold the acquisition
# silently.
STALL_WARNING_INTERVAL = 10.0
# The VISA timeout of the scope in ms; the scope can take seconds to answer while it applies a
# setting or saves a capture, longer than the VISA default.
SCOPE_TIMEOUT = 10_000
#: The prompt of a pause the user asked for through :meth:`TransientProcedure.request_pause`.
PAUSE_PROMPT = "Paused as requested."

VIN_COLUMN = "Vin (V)"
LOAD_STEP_COLUMN = "Load Step"
FREQUENCY_COLUMN = "Frequency (kHz)"
VMEAN_COLUMN = "Mean Vmean (mV)"
VMAX_COLUMN = "Max Vmax (mV)"
VMIN_COLUMN = "Min Vmin (mV)"
PK2PK_COLUMN = "Mean Vpk2pk (mV)"
OVERSHOOT_COLUMN = "Overshoot (%)"
UNDERSHOOT_COLUMN = "Undershoot (%)"
RISE_TIME_COLUMN = "Mean Rise Time (us)"
FALL_TIME_COLUMN = "Mean Fall Time (us)"
CAPTURE_COLUMN = "Capture"
#: The decimal places the results read from the scope and derived from them are rounded to.
RESULT_DECIMALS = 3
#: The signals the scope measures; the channel of each is a parameter of the procedure.
VOUT = "Vout"
IOUT = "Iout"


class Result(NamedTuple):
    """A results column and the scope measurement statistic recorded in it."""

    #: The column of :attr:`TransientProcedure.DATA_COLUMNS`.
    column: str
    #: The signal the measurement is taken on, :data:`VOUT` or :data:`IOUT`.
    signal: str
    #: The type of the measurement the statistic comes from.
    measurement_type: str
    #: The statistic of that measurement over all acquisitions, as the measurement interface
    #: names it.
    statistic: str
    #: The factor from the volts or seconds the scope reads to the unit of the column.
    scale: float


#: The results read from the scope at every input; the overshoot and undershoot columns are
#: computed from them.
RESULTS = [
    Result(VMEAN_COLUMN, VOUT, "MEAN", "mean", 1e3),
    Result(VMAX_COLUMN, VOUT, "MAXIMUM", "maximum", 1e3),
    Result(VMIN_COLUMN, VOUT, "MINIMUM", "minimum", 1e3),
    Result(PK2PK_COLUMN, VOUT, "PK2PK", "mean", 1e3),
    Result(RISE_TIME_COLUMN, IOUT, "RISETIME", "mean", 1e6),
    Result(FALL_TIME_COLUMN, IOUT, "FALLTIME", "mean", 1e6),
]
#: The ``(signal, measurement type)`` pairs the scope must measure; startup adds the missing ones.
REQUIRED_MEASUREMENTS = list(dict.fromkeys((r.signal, r.measurement_type) for r in RESULTS))


class Input(NamedTuple):
    """One input of the measurement, a row of :attr:`TransientProcedure.INPUTS`."""

    #: The pulse frequency in kHz.
    frequency: float
    #: The high level of the pulse in mV.
    high: float
    #: The low level of the pulse in mV.
    low: float
    #: The leading edge time of the pulse in us.
    leading: float
    #: The trailing edge time of the pulse in us.
    trailing: float
    #: The horizontal scale of the scope in us per division.
    horizontal_div: float
    #: The horizontal position of the trigger point in percent of the screen width.
    horizontal_position: float
    #: Whether the scope triggers on the falling edge of Vout, at the low cursor level, rather
    #: than on the rising edge of Iout.
    falling_edge: bool
    #: The trigger level on Iout in mV, for an input that triggers on its rising edge.
    trigger_level: float

    @classmethod
    def from_row(cls, row: NDArray[np.float64]) -> Self:
        """Return the input a row of :attr:`TransientProcedure.INPUTS` holds.

        :param row: One value per field, in field order; the edge is 0 for a rising edge on
            Iout and 1 for a falling edge on Vout.
        :returns: The input.
        :raises ValueError: If the row does not hold one value per field.
        """
        if len(row) != len(cls._fields):
            raise ValueError(
                f"An input needs {len(cls._fields)} values, not {len(row)}: {row.tolist()}."
            )
        frequency, high, low, leading, trailing, div, position, edge, level = (
            float(v) for v in row
        )
        return cls(frequency, high, low, leading, trailing, div, position, bool(edge), level)

    @property
    def load_step(self) -> str:
        """Get the load step as the pulse levels, e.g. ``"85 to 148 mV"``."""
        return f"{self.low:g} to {self.high:g} mV"

    @property
    def frequency_text(self) -> str:
        """Get the pulse frequency with an SI prefix, e.g. ``"1kHz"``."""
        return format_with_si_prefix(self.frequency * 1e3, "Hz")

    def describe(self) -> str:
        """Return the load step and frequency for a log or a prompt.

        :returns: The description, e.g. ``"85 to 148 mV at 1kHz"``.
        """
        return f"{self.load_step} at {self.frequency_text}"


def overshoot_percent(maximum: float, mean: float) -> float:
    """Return how far a rail's maximum lies above its mean, as a percentage of the mean.

    A maximum of 5.5 V on a mean of 5 V gives 10 %. The mean keeps its sign, so the same
    distance above a mean of -5 V gives -10 %.

    :param maximum: The highest voltage in volts.
    :param mean: The mean voltage in volts.
    :returns: ``(maximum - mean) / mean * 100``, NaN when the mean is zero.
    """
    if mean == 0:
        return math.nan
    return (maximum - mean) / mean * 100


def undershoot_percent(minimum: float, mean: float) -> float:
    """Return how far a rail's minimum lies below its mean, as a percentage of the mean.

    A minimum of 4 V on a mean of 5 V gives -20 %: a dip below a positive mean is negative. The
    mean keeps its sign, so the same dip below a mean of -5 V gives 20 %.

    :param minimum: The lowest voltage in volts.
    :param mean: The mean voltage in volts.
    :returns: ``(minimum - mean) / mean * 100``, NaN when the mean is zero.
    """
    if mean == 0:
        return math.nan
    return (minimum - mean) / mean * 100


class TransientProcedure(Procedure):
    """Measure the transient response of a rail: an AFG31051 function generator steps the load
    with a pulse, and an MSO58 oscilloscope measures the output voltage, Vout, and the load
    current, Iout, at every input of :attr:`INPUTS`.

    Startup stops the scope, makes sure it measures the mean, maximum, minimum and peak-to-peak
    of ``vout_channel`` and the rise and fall times of ``iout_channel``, adding the measurements
    it lacks to those already set up, e.g. from the front panel, see
    :meth:`add_missing_measurements`, and, when ``use_cursor`` is set, shows the horizontal bar
    cursors on ``vout_channel`` at ``cursor_lo_abs`` and ``cursor_hi_abs`` volts, see
    :meth:`show_cursors`; otherwise it hides them. The AFG is set to a pulse with its output
    off, see :meth:`set_up_afg`.

    Execute goes through :attr:`INPUTS` in order, see :meth:`execute`. With ``safe_mode`` set,
    the procedure starts the acquisition and pauses once the instruments are set for an input,
    so the user can adjust the scope against the live trace: it hands a prompt to
    :attr:`pause_handler` and waits until :meth:`resume` is called, which clears the statistics
    taken meanwhile, or until it is asked to stop. By default the prompt is printed and Enter
    resumes, as on a command line; a GUI replaces the handler with one that shows the prompt
    and resumes from a button. The user can also ask for a pause at any time through
    :meth:`request_pause`, which the procedure honours at its next step with the prompt
    ``PAUSE_PROMPT``.
    """

    name = StringParameter(
        "Procedure name",
        default="transient",
        description="Name of the run, recorded with the results",
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
    vin = FloatParameter("Vin", units="V", default=0, description="Nominal input voltage")
    duty_cycle = IntegerParameter(
        "Duty cycle",
        units="%",
        default=50,
        minimum=1,
        maximum=99,
        description="Duty cycle of the load pulse at every input",
    )
    vout_channel = IntegerParameter(
        "Vout channel",
        default=1,
        minimum=1,
        maximum=8,
        description="Scope channel of the output voltage",
    )
    iout_channel = IntegerParameter(
        "Iout channel",
        default=2,
        minimum=1,
        maximum=8,
        description="Scope channel of the load current",
    )
    safe_mode = BooleanParameter(
        "Run in safe mode",
        default=True,
        description="Pause after each input change, with a live trace, to adjust the scope",
    )
    use_cursor = BooleanParameter(
        "Use cursors",
        default=True,
        description="Show the cursors about the target on the scope, or hide them",
    )
    cursor_hi_abs = FloatParameter(
        "Absolute cursor high",
        units="V",
        default=0,
        description="High cursor level given as a voltage",
    )
    cursor_lo_abs = FloatParameter(
        "Absolute cursor low",
        units="V",
        default=0,
        description="Low cursor level given as a voltage",
    )
    pop_min = IntegerParameter(
        "Sample population minimum",
        default=300,
        minimum=0,
        description="Takes captures until at least the population minimum",
    )
    scope_capture_dir = StringParameter(
        "Scope capture directory",
        default="C:/Temp/Transient/",
        description="Directory on the scope the captures are saved to before they are moved",
    )

    DATA_COLUMNS: ClassVar[list[str]] = [
        VIN_COLUMN,
        LOAD_STEP_COLUMN,
        FREQUENCY_COLUMN,
        VMEAN_COLUMN,
        VMAX_COLUMN,
        VMIN_COLUMN,
        PK2PK_COLUMN,
        OVERSHOOT_COLUMN,
        UNDERSHOOT_COLUMN,
        RISE_TIME_COLUMN,
        FALL_TIME_COLUMN,
        CAPTURE_COLUMN,
    ]

    INPUTS: ClassVar[NDArray[np.float64]] = np.array(
        [
            # (freq kHz, high mV, low mV, leading us, trailing us, hdiv us, hpos %, edge, tlevel mV)
            # edge is a bool: 0 for rising edge, 1 for falling edge
            # edge also doubles as a decision for 1: Vout channel param or 0: Iout channel param
            # specifically, for 1 kHz inputs, the trigger is set to the Vout channel and falling
            [1, 148, 85, 2.580, 2.380, 100, 30, 1, 72],  # 50-100 load @ 1 kHz
            [100, 149, 84, 2.580, 2.380, 4, 50, 0, 72],  # 50-100 load @ 100 kHz
            [500, 150, 80, 0.400, 0.400, 1, 50, 0, 72],  # 50-100 load @ 500 kHz
            [1000, 159, 69, 0.200, 0.200, 0.4, 50, 0, 72],  # 50-100 load @ 1 MHz
            [1, 113, 49, 2.580, 2.180, 100, 30, 1, 108],  # 25-75 load @ 1 kHz
            [100, 113, 49, 2.380, 2.180, 4, 50, 0, 108],  # 25-75 load @ 100 kHz
            [500, 120, 39, 0.400, 0.400, 1, 50, 0, 108],  # 25-75 load @ 500 kHz
            [1000, 134, 21, 0.200, 0.200, 0.4, 50, 0, 108],  # 25-75 load @ 1 MHz
            [1, 76, 11, 2.680, 1.280, 100, 30, 1, 72],  # 0-50 load @ 1 kHz
            [100, 77, 5, 2.880, 1.780, 4, 50, 0, 72],  # 0-50 load @ 100 kHz
            [500, 97, -17, 0.400, 0.400, 1, 50, 0, 72],  # 0-50 load @ 500 kHz
            [1000, 132, -52, 0.200, 0.200, 0.4, 50, 0, 72],  # 0-50 load @ 1 MHz
            [1, 150, 9, 6.760, 5.260, 100, 30, 1, 144],  # 0-100 @ 1 kHz
            [100, 151, 0, 3.290, 2.290, 4, 50, 0, 144],  # 0-100 @ 100 kHz
            [500, 166, -34, 0.400, 0.400, 1, 50, 0, 144],  # 0-100 @ 500 kHz
            [1000, 208, -85, 0.200, 0.200, 0.4, 50, 0, 144],  # 0-100 load @ 1 MHz
        ]
    )

    afg: TektronixAFG31051
    scope: TektronixMSO58

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._resumed = threading.Event()
        self._pause_requested = threading.Event()
        #: Receives the prompt each time the procedure pauses; see :meth:`pause`.
        self.pause_handler: Callable[[str], None] = self.prompt_on_console
        #: The directory on this computer the results file of the run goes to; the captures are
        #: moved to :attr:`capture_directory` inside it. The runner sets it, as a window does to
        #: the directory it creates when it queues a run.
        self.results_directory: Path = Path(DEFAULT_RESULTS_DIRECTORY)

    @property
    def capture_directory(self) -> Path:
        """Get the directory on this computer the captures are moved to.

        It is ``captures`` inside :attr:`results_directory`, so the captures sit next to the
        results file and the log of the run.
        """
        return self.results_directory / "captures"

    def inputs(self) -> list[Input]:
        """Return the inputs of the measurement, in the order of :attr:`INPUTS`.

        :returns: The inputs.
        :raises ValueError: If a row of :attr:`INPUTS` does not hold one value per field of
            :class:`Input`.
        """
        return [Input.from_row(row) for row in self.INPUTS]

    def startup(self) -> None:
        """Connect to the scope and the AFG and set them up for the inputs.

        The scope's acquisition is stopped, the measurements it lacks are added, see
        :meth:`add_missing_measurements`, and the cursors are shown on ``vout_channel`` when
        ``use_cursor`` is set, see :meth:`show_cursors`, or hidden otherwise, so a capture
        shows none left from an earlier run. The AFG is set to a pulse with its output off,
        see :meth:`set_up_afg`.

        :raises ValueError: If ``use_cursor`` is set and a cursor level lies outside the
            voltages the scope shows of ``vout_channel``.
        """
        log.info("Connecting to the scope.")
        self.scope = TektronixMSO58(self.scope_resource, timeout=SCOPE_TIMEOUT)
        state = "acquiring" if self.scope.acquisition_enabled else "stopped"
        log.info(f"Connected to {self.scope.id}, found {state}.")
        self.scope.acquisition_enabled = False
        self.add_missing_measurements()
        if self.use_cursor:
            self.show_cursors()
        else:
            self.hide_cursors()
        log.info("Connecting to the AFG.")
        self.afg = TektronixAFG31051(self.afg_resource)
        log.info(f"Connected to {self.afg.id}.")
        self.set_up_afg()

    def execute(self) -> None:
        """Measure at every input of :attr:`INPUTS` until each scope measurement has ``pop_min``
        values.

        Each input sets the AFG pulse, see :meth:`set_afg`, and the scope's horizontal scale,
        position and trigger, see :meth:`set_scope`, then clears the statistics and starts the
        acquisition. With ``safe_mode`` set, the procedure pauses there for the user to adjust
        the scope against the live trace, see :meth:`pause`, and clears the statistics again
        once resumed. It acquires until the measurements are populated, stops, saves a screen
        capture to :attr:`capture_directory` and records one row of :attr:`DATA_COLUMNS`. Stops
        early when asked to.

        :raises ValueError: If the scope lacks a required measurement of ``vout_channel`` or
            ``iout_channel``, or a capture could not be saved.
        """
        measurements = self._attach_measurements()
        if not measurements:
            raise ValueError("The scope has no measurements; add at least one on the scope.")
        for measurement in measurements:
            _log_measurement(measurement, self.pop_min)
        recorded = {
            result.column: self._measurement_of(
                measurements, result.signal, result.measurement_type
            )
            for result in RESULTS
        }
        numbers = [_measurement_number(measurement) for measurement in measurements]
        scope_directory = self._make_scope_capture_dir()
        local_directory = self.capture_directory
        local_directory.mkdir(parents=True, exist_ok=True)
        inputs = self.inputs()
        log.info(f"Starting transient measurement over {len(inputs)} inputs.")
        for done, current in enumerate(inputs, start=1):
            if not self._pause_if_requested():
                log.info("Transient measurement stopped.")
                return
            log.info(f"Input {done} of {len(inputs)}: {current.describe()}.")
            self.set_afg(current)
            self.set_scope(current)
            # Starting acquisition carries the statistics on, so they are cleared first.
            self.scope.clear_acquisitions()
            _ = self.scope.complete  # The scope clears in the background.
            self.scope.acquisition_enabled = True
            if self.safe_mode:
                prompt = (
                    f"Input {done} of {len(inputs)} set: {current.describe()}. Adjust the "
                    "scope if required."
                )
                # The acquisition runs meanwhile, so the user adjusts the scope against the
                # live trace; what the statistics took in with the settings in flux is cleared.
                if not self.pause(prompt):
                    self.scope.acquisition_enabled = False
                    log.info("Transient measurement stopped.")
                    return
                self.scope.clear_acquisitions()
                _ = self.scope.complete  # The scope clears in the background.
            filled = self._wait_for_population(numbers)
            self.scope.acquisition_enabled = False
            if not filled:
                log.info("Transient measurement stopped.")
                return
            _ = self.scope.complete  # Lets the stop take effect before the results are read.
            # Acquisitions in flight when the populations filled still add values, so the
            # populations the results come from are read once acquisition has stopped.
            populations = self.scope.read_populations(numbers)
            log.info(f"Acquisition stopped at {_format_populations(populations)}.")
            results = self._read_results(recorded, populations)
            file_name = self.capture_file_name(current)
            scope_path = f"{scope_directory}/{file_name}"
            self._save_capture(scope_path)
            local_path = local_directory / file_name
            self._cut_capture(scope_path, local_path)
            self.emit(
                "results",
                {
                    VIN_COLUMN: self.vin,
                    LOAD_STEP_COLUMN: current.load_step,
                    FREQUENCY_COLUMN: current.frequency,
                    **results,
                    CAPTURE_COLUMN: capture_hyperlink(
                        local_path.relative_to(self.results_directory)
                    ),
                },
            )
            self.emit("progress", 100 * done / len(inputs))

    def shutdown(self) -> None:
        """Turn the AFG output off and close the connections to the AFG and the scope."""
        # Startup can fail before either instrument is connected, leaving its attribute unset.
        if getattr(self, "afg", None) is not None:
            self.afg.ch1.output_enabled = False
            self.afg.adapter.close()
        if getattr(self, "scope", None) is not None:
            self.scope.adapter.close()
        log.info("Ending transient measurement.")

    def pause(self, prompt: str) -> bool:
        """Pause until :meth:`resume` is called, or the procedure is asked to stop.

        The prompt is logged and passed to :attr:`pause_handler`, which must see to it that
        :meth:`resume` gets called, e.g. by reading a line from the console or by enabling a
        button.

        :param prompt: What the user is to do before the procedure continues, e.g.
            ``"Adjust the scope if required."``.
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
        before the next input. A request made while the procedure is paused is satisfied by
        that pause.
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

    def set_up_afg(self) -> None:
        """Set the AFG to a pulse that holds its duty cycle, with the output off.

        The output stays off until the first input is set, see :meth:`set_afg`, so the load is
        not driven with the pulse of an earlier run meanwhile. Holding the duty cycle keeps it,
        rather than the pulse width, as the frequency changes from input to input.
        """
        channel = self.afg.ch1
        channel.output_enabled = False
        channel.shape = "PULS"
        channel.pulse_hold = "DUTY"
        log.info("AFG set to a pulse holding its duty cycle, output off.")

    def set_afg(self, current: Input) -> None:
        """Set the AFG pulse to an input, at ``duty_cycle``, and turn the output on.

        The frequency goes first and the duty cycle next, since together they set the pulse
        width that bounds the edge times, and the AFG moves an edge outside the bounds on its
        own; the levels follow, in either order, since the AFG moves the other level along when
        they would cross; the edges come last.

        :param current: The input.
        :raises ValueError: If the AFG rejects a setting, e.g. an edge time too long for the
            pulse width.
        """
        channel = self.afg.ch1
        channel.frequency = current.frequency * 1e3
        channel.pulse_dutycycle = self.duty_cycle
        channel.amplitude_high = current.high / 1e3
        channel.amplitude_low = current.low / 1e3
        channel.pulse_leading = current.leading
        channel.pulse_trailing = current.trailing
        channel.output_enabled = True
        log.info(
            f"AFG pulse set to {current.describe()}, {self.duty_cycle} % duty cycle, edges "
            f"{current.leading:g} us and {current.trailing:g} us, output on."
        )

    def set_scope(self, current: Input) -> None:
        """Set the scope's horizontal scale and position and its edge trigger to an input.

        An input with ``falling_edge`` triggers on the falling edge of ``vout_channel`` at the
        low cursor level, ``cursor_lo_abs``, shown or not; any other input triggers on the
        rising edge of ``iout_channel`` at the input's ``trigger_level``. The scope rounds the
        horizontal scale to one of its steps silently, so the scale that applies is logged.

        :param current: The input.
        """
        scope = self.scope
        scope.horizontal_scale = current.horizontal_div / 1e6
        scope.horizontal_position = current.horizontal_position
        scope.trigger_type = "EDGE"
        if current.falling_edge:
            number, slope, level = self.vout_channel, "FALL", self.cursor_lo_abs
        else:
            number, slope, level = self.iout_channel, "RISE", current.trigger_level / 1e3
        scope.trigger_source = f"CH{number}"
        scope.trigger_slope = slope
        scope.channels[number].trigger_level = level
        edge = "falling" if current.falling_edge else "rising"
        log.info(
            f"Scope set to {scope.horizontal_scale} s/div at {current.horizontal_position:g} %, "
            f"triggering on the {edge} edge of CH{number} at {level} V."
        )

    def add_missing_measurements(self) -> list[TektronixMSO456Measurement]:
        """Add the measurements in :data:`REQUIRED_MEASUREMENTS` that the scope lacks.

        The measurements already on the scope, e.g. from its front panel, are kept, and a type a
        channel already has is not added again. Each missing one is added under the next number
        above the highest in use, with its type and source set, and logged.

        :returns: The measurements added, in the order of :data:`REQUIRED_MEASUREMENTS`; empty
            when the scope has them all.
        :raises ValueError: If the scope refuses a measurement.
        """
        measurements = self._attach_measurements()
        present = {(measurement.source, measurement.type) for measurement in measurements}
        missing = [
            (signal, kind)
            for signal, kind in REQUIRED_MEASUREMENTS
            if (self._source_of(signal), kind) not in present
        ]
        if not missing:
            log.info("The scope has every measurement required.")
            return []
        number = max((_measurement_number(measurement) for measurement in measurements), default=0)
        added: list[TektronixMSO456Measurement] = []
        for signal, kind in missing:
            source = self._source_of(signal)
            number += 1
            measurement = self.scope.add_measurement(number)
            measurement.type = kind
            measurement.source = source
            log.info(f"Added scope measurement MEAS{number}: {kind} of {source} ({signal}).")
            added.append(measurement)
        return added

    def show_cursors(self) -> None:
        """Show the horizontal bar cursors on ``vout_channel`` at ``cursor_lo_abs`` and
        ``cursor_hi_abs``.

        Cursor A marks the low level and cursor B the high one. The scope shows the cursors, on
        ``vout_channel``, as soon as the levels are set, and logs them.

        :raises ValueError: If a level lies outside the voltages the scope shows of
            ``vout_channel``; change the channel's scale, position or offset on the scope first.
        """
        channel = self.scope.channels[self.vout_channel]
        self.scope.cursor.set_levels(channel, self.cursor_lo_abs, self.cursor_hi_abs)
        log.info(
            f"Cursors shown on CH{self.vout_channel} at {self.cursor_lo_abs} V and "
            f"{self.cursor_hi_abs} V."
        )

    def hide_cursors(self) -> None:
        """Hide the cursors on the scope, wherever they are, and log it."""
        self.scope.cursor.enabled = False
        log.info("Cursors hidden.")

    def capture_file_name(self, current: Input) -> str:
        """Return the file name of the capture taken at one input.

        :param current: The input.
        :returns: The file name, e.g. ``"12Vin transient 85to148mV 1kHz.png"``, with ``vin``
            and the levels written without trailing zeros.
        """
        levels = f"{current.low:g}to{current.high:g}mV"
        return f"{self.vin:g}Vin {self.name} {levels} {current.frequency_text}.png"

    def _source_of(self, signal: str) -> str:
        """Return the scope channel of a signal, e.g. ``"CH1"`` for :data:`VOUT`."""
        number = self.vout_channel if signal == VOUT else self.iout_channel
        return f"CH{number}"

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
        self, measurements: list[TektronixMSO456Measurement], signal: str, measurement_type: str
    ) -> TektronixMSO456Measurement:
        """Return the first measurement of a signal's channel of a type, e.g. MEAN of Vout.

        :param measurements: The measurements on the scope.
        :param signal: :data:`VOUT` or :data:`IOUT`.
        :param measurement_type: The type looked for.
        :returns: The measurement.
        :raises ValueError: If no measurement of the channel has the type.
        """
        source = self._source_of(signal)
        for measurement in measurements:
            if measurement.type == measurement_type and measurement.source == source:
                return measurement
        raise ValueError(f"The scope has no {measurement_type} measurement of {source} ({signal}).")

    def _read_results(
        self, recorded: dict[str, TektronixMSO456Measurement], populations: dict[int, int]
    ) -> dict[str, float]:
        """Read the statistics of :data:`RESULTS` from the scope, derive the rest, and log them.

        The overshoot and undershoot are Max Vmax and Min Vmin relative to Mean Vmean, all read
        for this row, as percentages of that mean, see :func:`overshoot_percent` and
        :func:`undershoot_percent`; they are derived from the values as read, in volts. Every
        value is then scaled to the unit of its column and rounded to :data:`RESULT_DECIMALS`
        decimal places.

        :param recorded: The measurements by results column.
        :param populations: The populations by measurement number the statistics come from.
        :returns: The values by column.
        """
        values: dict[str, float] = {}
        read: list[str] = []
        for result in RESULTS:
            measurement = recorded[result.column]
            name = f"{result.signal} {result.measurement_type} {result.statistic}"
            value = _number(getattr(measurement, result.statistic), name)
            values[result.column] = value
            population = populations[_measurement_number(measurement)]
            read.append(f"{name} {value} from {population} values")
        log.info(f"{', '.join(read)}, over {self.scope.acquisition_count} acquisitions.")
        overshoot = overshoot_percent(values[VMAX_COLUMN], values[VMEAN_COLUMN])
        undershoot = undershoot_percent(values[VMIN_COLUMN], values[VMEAN_COLUMN])
        log.info(f"Overshoot {overshoot} %, undershoot {undershoot} %.")
        results = {result.column: values[result.column] * result.scale for result in RESULTS}
        results[OVERSHOOT_COLUMN] = overshoot
        results[UNDERSHOOT_COLUMN] = undershoot
        return {column: round(value, RESULT_DECIMALS) for column, value in results.items()}

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
