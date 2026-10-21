"""Driver for the Tektronix MSO4/5/6 series of mixed signal oscilloscopes."""

import logging
import math
import re
from functools import partial
from typing import NoReturn

from pymeasure.adapters import Adapter
from pymeasure.instruments import Channel, Instrument
from pymeasure.instruments.generic_types import SCPIMixin
from pymeasure.instruments.validators import strict_discrete_range, strict_discrete_set

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# Event codes 0 and 1 mean the queue holds nothing to report; 500 to 699 are warnings, which the
# instrument reports without interrupting the command.
NO_EVENT_CODES = (0, 1)
WARNING_CODES = range(500, 700)
# Messages contain commas, so the reply cannot be split on them; a message ends at the quote that
# precedes the next event or the end of the reply, which keeps quotes inside a message.
_EVENT_PATTERN = re.compile(r'(\d+),"(.*?)"(?=,\d+,"|$)')
# Boolean queries reply 0 or 1 with HEADer OFF; the keyword forms the manual lists are accepted
# as well.
_BOOLEAN_REPLIES = {"OFF": "0", "ON": "1"}


def _boolean_reply(reply: str) -> str:
    """Translate the keyword form of a boolean reply to its digit form."""
    return _BOOLEAN_REPLIES.get(reply, reply)


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

    acquisition_enabled = Instrument.control(
        "ACQuire:STATE?",
        "ACQuire:STATE %d",
        """Control whether the instrument is acquiring (bool).

        Enabling starts a new acquisition and resets ``acquisition_count``: a single sequence if
        ``stop_after`` is SEQUENCE, otherwise a continuous one. It does not reset the
        all-acquisitions measurement statistics; :meth:`clear_acquisitions` does. A single
        sequence finishes only once its acquisitions have been triggered, so waiting for it with
        ``complete`` blocks until then.
        """,
        validator=strict_discrete_set,
        map_values=True,
        values={True: 1, False: 0},
        preprocess_reply=_boolean_reply,
        check_set_errors=True,
    )

    stop_after = Instrument.control(
        "ACQuire:STOPAfter?",
        "ACQuire:STOPAfter %s",
        """Control when acquisition stops (str strictly in RUNSTOP, SEQUENCE).

        RUNSTOP acquires continuously until ``acquisition_enabled`` is set to False. SEQUENCE
        stops after ``sequence_count`` acquisitions; selecting it switches the trigger mode to
        NORMAL, and switching the trigger mode back to AUTO reverts this to RUNSTOP.
        """,
        validator=strict_discrete_set,
        values=["RUNSTOP", "SEQUENCE"],
        check_set_errors=True,
        cast=str,
    )

    sequence_count = Instrument.control(
        "ACQuire:SEQuence:NUMSEQuence?",
        "ACQuire:SEQuence:NUMSEQuence %d",
        """Control the number of acquisitions in a single sequence (int strictly 1 or more).

        Applies when ``stop_after`` is SEQUENCE: the instrument stops after exactly this many
        acquisitions, and ``complete`` waits for all of them.
        """,
        validator=partial(strict_discrete_range, step=1),
        values=[1, math.inf],
        check_set_errors=True,
        cast=int,
    )

    acquisition_count = Instrument.measurement(
        "ACQuire:NUMACq?",
        """Get the number of acquisitions since acquisition was last enabled (int).""",
        cast=int,
    )

    def clear_acquisitions(self) -> None:
        """Clear the acquisitions, waveforms and measurement statistics.

        The instrument clears in the background, so wait with ``complete`` before reading
        results. While acquisition is disabled, the waveforms stay empty afterwards, so
        measurements report no valid result and queue 540 warnings until acquisition runs again.
        """
        self.write("CLEAR")
