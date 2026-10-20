"""Driver for the Tektronix AFG31051 arbitrary function generator."""

from typing import TYPE_CHECKING

from pymeasure.adapters import Adapter
from pymeasure.instruments import Instrument

from .afg31000 import TektronixAFG31000, TektronixAFG31000Channel


class TektronixAFG31051Channel(TektronixAFG31000Channel):
    """Represent the Tektronix AFG31051 output channel.

    The ranges are the datasheet limits into High-Z, where a sine wave reaches 50 MHz, square
    and pulse 40 MHz, and ramp 800 kHz; pulse frequency starts at 1 mHz.
    Into 50 Ohm, the offset and level ranges are halved.
    """

    frequency_values = (1e-6, 50e6)
    # The levels span -10 V to 10 V but must stay 2 mV apart, and the offset must leave room for
    # half the 2 mVpp minimum amplitude.
    offset_values = (-9.999, 9.999)
    amplitude_high_values = (-9.998, 10.0)
    amplitude_low_values = (-10.0, 9.998)
    # In microseconds; 625 s is 0.625 times the longest pulse period of 1000 s.
    pulse_leading_values = (0.006, 625e6)
    pulse_trailing_values = (0.006, 625e6)


class TektronixAFG31051(TektronixAFG31000):
    """Represent the Tektronix AFG31051 single-channel arbitrary function generator.

    Takes the same parameters as :class:`TektronixAFG31000`, with ``name`` defaulting to
    "Tektronix AFG31051".
    """

    def __init__(self, adapter: Adapter | str, name: str = "Tektronix AFG31051", **kwargs) -> None:
        super().__init__(adapter, name, **kwargs)

    # Instantiation replaces the creator with the channel itself, which pyright cannot follow.
    if TYPE_CHECKING:
        ch1: TektronixAFG31051Channel
    else:
        ch1 = Instrument.ChannelCreator(TektronixAFG31051Channel, 1)
