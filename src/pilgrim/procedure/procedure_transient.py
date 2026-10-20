"""Transient response measurement procedure."""

import logging
import math
from itertools import pairwise
from time import sleep
from typing import ClassVar

from pymeasure.experiment import Procedure

from pilgrim.instruments import TektronixAFG31051

from .parameters import FloatListParameter, FloatParameter, FloatRangeListParameter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class TransientProcedure(Procedure):
    """Measure the transient response of a load to a pulsed stimulus from an AFG.

    The load is stepped through each load range, and at every step the pulse frequency is
    swept through the checkpoints.
    The user confirms the stimulus settings before the output turns on.
    """

    frequency = FloatParameter(
        "Pulse frequency", units="Hz", default=1e3, minimum=1e-3, maximum=1e6
    )
    dutycycle = FloatParameter("Duty cycle", units="%", default=50.0, minimum=0.001, maximum=99.999)
    high_level = FloatParameter("High level", units="V", default=0.5)
    low_level = FloatParameter("Low level", units="V", default=0.0)
    leading_edge = FloatParameter("Leading edge", units="us", default=1, minimum=1)
    trailing_edge = FloatParameter("Trailing edge", units="us", default=1, minimum=1)
    afg_delay = FloatParameter("AFG delay", units="s", default=3.0, minimum=0.0)
    checkpoints = FloatListParameter("Checkpoint frequencies", units="Hz", default=[])
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

    DATA_COLUMNS: ClassVar[list[str]] = ["Repetition", "Time (s)", "Voltage (V)"]

    afg: TektronixAFG31051

    def startup(self) -> None:
        """Connect to the AFG and configure the stimulus.

        :raises KeyboardInterrupt: If the user declines to continue at a confirmation prompt.
        """
        log.info("Setting up instruments.")
        self._set_up_afg(self.afg_delay)

    def execute(self) -> None:
        """Step the load through each load range and sweep the frequency at every step."""
        sweep_frequencies = self.sweep_frequencies()
        load_steps = self.calc_load_steps()
        channel = self.afg.ch1

        for low, high in load_steps:
            # Either order is safe: the AFG moves the other level along when they would cross.
            channel.amplitude_low, channel.amplitude_high = low, high
            # 1 s is an assumed settling time for the load, not a measured one.
            sleep(1)
            for frequency in sweep_frequencies:
                channel.frequency = frequency
                sleep(1)

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

    def shutdown(self) -> None:
        """Turn off the stimulus and leave the instruments in a safe state."""
        if self.afg is not None:
            self.afg.ch1.output_enabled = False
            self.afg.adapter.close()
        log.info("Ending transient measurement.")

    def _set_up_afg(self, delay: float) -> None:
        """Connect to the AFG, configure the pulse, and turn the output on once confirmed.

        :param delay: Seconds to let the output settle before asking for a final check.
        :raises KeyboardInterrupt: If the user declines to continue at a confirmation prompt.
        """
        # Hardcoded to the bench AFG, so the procedure only runs against that unit.
        self.afg = TektronixAFG31051("USB0::0x0699::0x0357::SGVJ018478::INSTR")
        channel = self.afg.ch1
        # The output may still be on from an earlier session; keep it off until confirmed.
        channel.output_enabled = False
        channel.shape = "PULS"
        channel.frequency = self.frequency
        channel.pulse_dutycycle = self.dutycycle
        channel.amplitude_high = self.high_level
        channel.amplitude_low = self.low_level
        channel.pulse_leading = self.leading_edge
        channel.pulse_trailing = self.trailing_edge

        print("Function generator will turn on with:")
        print(f"\tFrequency: {self.frequency} Hz")
        print(f"\tDuty cycle: {self.dutycycle} %")
        print(f"\tHigh level: {self.high_level} V")
        print(f"\tLow level: {self.low_level} V")
        print(f"\tLeading edge: {self.leading_edge} us")
        print(f"\tTrailing edge: {self.trailing_edge} us")
        pause_confirm()
        channel.output_enabled = True

        print("Continuing... Settling output, please wait.")
        sleep(delay)

        print("Check the output on the oscilloscope before proceeding.")
        pause_confirm()


def pause_confirm() -> None:
    """Pause the procedure and wait for user confirmation to continue.

    :raises KeyboardInterrupt: If the user declines, which the worker treats as an abort and
        follows with :meth:`TransientProcedure.shutdown`.
    """
    choice = input("Procedure paused. Continue? (y/n) ")
    if choice.lower() != "y":
        print("Modify parameters as needed and run again.")
        raise KeyboardInterrupt()
