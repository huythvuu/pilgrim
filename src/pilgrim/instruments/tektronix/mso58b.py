"""Driver for the Tektronix MSO58B mixed signal oscilloscope."""

from pymeasure.adapters import Adapter

from .mso456 import TektronixMSO456


class TektronixMSO58B(TektronixMSO456):
    """Represent the Tektronix MSO58B mixed signal oscilloscope.

    Takes the same parameters as :class:`TektronixMSO456`, with ``name`` defaulting to
    "Tektronix MSO58B".
    """

    def __init__(self, adapter: Adapter | str, name: str = "Tektronix MSO58B", **kwargs) -> None:
        super().__init__(adapter, name, **kwargs)
