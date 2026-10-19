from pymeasure.adapters import Adapter
from pymeasure.instruments import Instrument

from pilgrim.tektronix.afg31000 import TektronixAFG31000, TektronixAFG31000Channel


class TektronixAFG31051Channel(TektronixAFG31000Channel):
    """A class representing the Tektronix AFG31051 output channel.

    Model ranges, from the AFG31000 Series datasheet:

    - frequency: 1 uHz to 50 MHz (sine; square and pulse reach 40 MHz, ramp 800 kHz, and
      pulse starts at 1 mHz)
    - offset: -10 V to 10 V into High-Z, less half the amplitude (2 mVpp minimum)
    - upper and lower levels: -10 V to 10 V into High-Z, at least 2 mV apart
    - leading and trailing edge times: 6 ns to 625 s, i.e. 0.625 times the longest pulse
      period of 1000 s

    Into 50 Ohm, the offset and level ranges are halved.
    """

    frequency_values = (1e-6, 50e6)
    offset_values = (-9.999, 9.999)
    amplitude_high_values = (-9.998, 10.0)
    amplitude_low_values = (-10.0, 9.998)
    pulse_leading_values = (0.006, 625e6)  # microseconds
    pulse_trailing_values = (0.006, 625e6)  # microseconds


class TektronixAFG31051(TektronixAFG31000):
    """A class representing the Tektronix AFG31051 single-channel arbitrary function generator.

    Takes the same parameters as :class:`TektronixAFG31000`.
    """

    def __init__(self, adapter: Adapter | str, name: str = "Tektronix AFG31051", **kwargs) -> None:
        super().__init__(adapter, name, **kwargs)

    ch1 = Instrument.ChannelCreator(TektronixAFG31051Channel, 1)
