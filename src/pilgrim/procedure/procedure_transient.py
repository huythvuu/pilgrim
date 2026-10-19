"""Transient response measurement procedure."""

import logging
from time import sleep
from typing import ClassVar

from pymeasure.experiment import Procedure

from pilgrim.tektronix.afg31051 import TektronixAFG31051

from .parameters import FloatParameter, IntegerParameter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class TransientProcedure(Procedure):
    frequency = FloatParameter(
        "Pulse frequency", units="Hz", default=1e3, minimum=1e-3, maximum=1e6
    )
    dutycycle = FloatParameter("Duty cycle", units="%", default=50.0, minimum=0.001, maximum=99.999)
    high_level = FloatParameter("High level", units="V", default=0.5)
    low_level = FloatParameter("Low level", units="V", default=0.0)
    leading_edge = FloatParameter("Leading edge", units="us", default=1, minimum=1)
    trailing_edge = FloatParameter("Trailing edge", units="us", default=1, minimum=1)
    afg_delay = FloatParameter("AFG delay", units="s", default=10.0, minimum=0.0)
    repetitions = IntegerParameter("Repetitions", default=10, minimum=1)
    delay = FloatParameter("Delay between repetitions", units="s", default=0.1, minimum=0.0)

    DATA_COLUMNS: ClassVar[list[str]] = ["Repetition", "Time (s)", "Voltage (V)"]

    afg: TektronixAFG31051 | None = None

    def startup(self) -> None:
        """Connect to the instruments and configure the stimulus."""
        log.info("Setting up instruments.")
        self._set_up_afg(self.afg_delay)

    def execute(self) -> None:
        pass

    def shutdown(self) -> None:
        """Turn off the stimulus and leave the instruments in a safe state."""
        if self.afg is not None:
            self.afg.ch1.output_enabled = False
            self.afg.adapter.close()
        log.info("Ending transient measurement.")

    def _set_up_afg(self, delay: float) -> None:
        """Configure the AFG with the specified delay."""
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
        self.pause_confirm()
        channel.output_enabled = True

        print("Continuing... Settling output, please wait.")
        sleep(delay)

        print("Check the output on the oscilloscope before proceeding.")
        self.pause_confirm()

    def pause_confirm(self) -> None:
        """Pause the procedure and wait for user confirmation to continue.

        :raises KeyboardInterrupt: If the user declines, which the worker treats as an abort
            and follows with :meth:`shutdown`.
        """
        choice = input("Procedure paused. Continue? (y/n) ")
        if choice.lower() != "y":
            print("Modify parameters as needed and run again.")
            raise KeyboardInterrupt()
