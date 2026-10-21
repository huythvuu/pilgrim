"""Driver for the Tektronix MSO4/5/6 series of mixed signal oscilloscopes."""

import logging
import re
from typing import NoReturn

from pymeasure.adapters import Adapter
from pymeasure.instruments import Channel, Instrument
from pymeasure.instruments.generic_types import SCPIMixin

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# Event codes 0 and 1 mean the queue holds nothing to report; 500 to 699 are warnings, which the
# instrument reports without interrupting the command.
NO_EVENT_CODES = (0, 1)
WARNING_CODES = range(500, 700)
# Messages contain commas, so the reply cannot be split on them; a message ends at the quote that
# precedes the next event or the end of the reply, which keeps quotes inside a message.
_EVENT_PATTERN = re.compile(r'(\d+),"(.*?)"(?=,\d+,"|$)')


class TektronixMSO456Channel(Channel):
    """Represent an analog channel of the Tektronix MSO4/5/6 series oscilloscopes."""

    # Required by PyMeasure's Child protocol, which ChannelCreator is typed against.
    _name: str
    _protected: bool
    _collection: str


class TektronixMSO456(SCPIMixin, Instrument):
    """Represent the Tektronix MSO4/5/6 series of mixed signal oscilloscopes.

    Individual models are represented by subclasses. Connecting switches response headers off and
    verbose keywords on, which persist on the instrument, and clears the event queue.
    A setting the instrument rejects raises ``ValueError``, while warnings are only logged.

    :param adapter: VISA resource name or adapter instance.
    :param name: Name of the instrument.
    :param write_termination: Termination appended to every command; used only when
        ``adapter`` is a resource name.
    :param kwargs: Keyword arguments for the VISA adapter.
    """

    def __init__(
        self,
        adapter: Adapter | str,
        name: str = "Tektronix MSO456",
        write_termination: str = "\n",
        **kwargs,
    ) -> None:
        super().__init__(adapter, name, write_termination=write_termination, **kwargs)
        # Replies are parsed as bare, full-length keywords; both states survive *RST.
        self.write("HEADer OFF")
        self.write("VERBose ON")
        self.clear()  # Power-up messages left in the event queue would fail the first setter.

    @property
    def next_error(self) -> NoReturn:
        """Get the next error; unsupported, as the instrument has no ``SYST:ERR?`` query.

        :raises NotImplementedError: Always; use :meth:`check_errors`, which reads the whole
            event queue, instead.
        """
        raise NotImplementedError(
            f"{self.name} has no SYST:ERR? query; use check_errors() to read the event queue."
        )

    def check_errors(self) -> list[tuple[int, str]]:
        """Read every event from the event queue and log it.

        Errors are logged at error level and warnings (codes 500 to 699) at warning level.

        :returns: The ``(code, message)`` pairs read, in queue order; empty if there are none.
        """
        self.ask("*ESR?")  # Reading the status register releases the queued events.
        reply = self.ask("ALLEv?").strip()
        events = [(int(code), message) for code, message in _EVENT_PATTERN.findall(reply)]
        events = [(code, message) for code, message in events if code not in NO_EVENT_CODES]
        for code, message in events:
            level = logging.WARNING if code in WARNING_CODES else logging.ERROR
            log.log(level, f"{self.name}: {code}, {message}")
        return events

    def check_set_errors(self) -> list[tuple[int, str]]:
        """Read the event queue after setting a property and raise if it holds any errors.

        :returns: The warnings read from the queue, since any error raises instead.
        :raises ValueError: If the instrument reports an error, e.g. a value outside the range
            allowed by its current settings.
        """
        events = self.check_errors()
        errors = [(code, message) for code, message in events if code not in WARNING_CODES]
        if errors:
            details = "; ".join(f"{code}, {message}" for code, message in errors)
            raise ValueError(f"{self.name} rejected the setting: {details}")
        return events
