"""Driver for the Tektronix MSO58B mixed signal oscilloscope."""

from typing import TYPE_CHECKING

from pymeasure.adapters import Adapter
from pymeasure.instruments import Instrument

from .mso456 import TektronixMSO456, TektronixMSO456Channel


class TektronixMSO58B(TektronixMSO456):
    """Represent the Tektronix MSO58B mixed signal oscilloscope with eight analog channels.

    Takes the same parameters as :class:`TektronixMSO456`, with ``name`` defaulting to
    "Tektronix MSO58B".
    """

    def __init__(self, adapter: Adapter | str, name: str = "Tektronix MSO58B", **kwargs) -> None:
        super().__init__(adapter, name, **kwargs)

    # Instantiation replaces the creator with the channel itself, which pyright cannot follow.
    if TYPE_CHECKING:
        ch5: TektronixMSO456Channel
        ch6: TektronixMSO456Channel
        ch7: TektronixMSO456Channel
        ch8: TektronixMSO456Channel
    else:
        ch5 = Instrument.ChannelCreator(TektronixMSO456Channel, 5)
        ch6 = Instrument.ChannelCreator(TektronixMSO456Channel, 6)
        ch7 = Instrument.ChannelCreator(TektronixMSO456Channel, 7)
        ch8 = Instrument.ChannelCreator(TektronixMSO456Channel, 8)
