"""Driver for the Tektronix MSO4/5/6 series of mixed signal oscilloscopes."""

import logging
import math
import re
from functools import partial
from typing import TYPE_CHECKING, NoReturn

from pymeasure.adapters import Adapter
from pymeasure.instruments import Channel, Instrument
from pymeasure.instruments.generic_types import SCPIMixin
from pymeasure.instruments.validators import (
    strict_discrete_range,
    strict_discrete_set,
    strict_range,
)

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

    display_enabled = Channel.control(
        "DISplay:WAVEView1:CH{ch}:STATE?",
        "DISplay:WAVEView1:CH{ch}:STATE %d",
        """Control whether the channel is shown in the waveform view (bool).

        Enabling adds the channel to the waveform view, which ``global_display_enabled``
        requires.
        """,
        validator=strict_discrete_set,
        map_values=True,
        values={True: 1, False: 0},
        preprocess_reply=_boolean_reply,
        check_set_errors=True,
    )

    global_display_enabled = Channel.control(
        "DISplay:GLObal:CH{ch}:STATE?",
        "DISplay:GLObal:CH{ch}:STATE %d",
        """Control whether the channel is displayed globally, in every view (bool).

        Only takes effect once the channel has been added to the waveform view with
        ``display_enabled``.
        """,
        validator=strict_discrete_set,
        map_values=True,
        values={True: 1, False: 0},
        preprocess_reply=_boolean_reply,
        check_set_errors=True,
    )

    trigger_level = Channel.control(
        "TRIGger:A:LEVel:CH{ch}?",
        "TRIGger:A:LEVel:CH{ch} %.12g",
        """Control the A trigger level on this channel in volts (float).

        Applies to the edge, pulse width, runt and transition triggers while this channel is
        the trigger source. The instrument clamps a level outside the range it allows silently,
        without reporting an error, so read the property back to learn the level that applies.
        """,
        check_set_errors=True,
    )


class TektronixMSO456(SCPIMixin, Instrument):
    """Represent the Tektronix MSO4/5/6 series of mixed signal oscilloscopes.

    Individual models are represented by subclasses, which add the analog channels beyond
    ``ch1`` to ``ch4``. Connecting switches response headers off and verbose keywords on, which
    persist on the instrument, and clears the event queue.
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
        # The edge trigger accepts every analog channel of the model, so the set is derived from
        # the channels the class declared, which the subclasses extend.
        self.trigger_source_values = [f"CH{n}" for n in self.channels] + ["LINE", "AUXILIARY"]

    # Every model has these four analog channels; subclasses add the rest.
    # Instantiation replaces the creator with the channel itself, which pyright cannot follow.
    if TYPE_CHECKING:
        ch1: TektronixMSO456Channel
        ch2: TektronixMSO456Channel
        ch3: TektronixMSO456Channel
        ch4: TektronixMSO456Channel
    else:
        ch1 = Instrument.ChannelCreator(TektronixMSO456Channel, 1)
        ch2 = Instrument.ChannelCreator(TektronixMSO456Channel, 2)
        ch3 = Instrument.ChannelCreator(TektronixMSO456Channel, 3)
        ch4 = Instrument.ChannelCreator(TektronixMSO456Channel, 4)

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

    trigger_type = Instrument.control(
        "TRIGger:A:TYPe?",
        "TRIGger:A:TYPe %s",
        """Control the A trigger type (str strictly in EDGE, WIDTH, TIMEOUT, RUNT, WINDOW,
        LOGIC, SETHOLD, TRANSITION, BUS).

        Only the edge trigger is configurable through this driver: ``trigger_source``,
        ``trigger_slope`` and ``trigger_coupling`` set it up, and each channel's
        ``trigger_level`` sets its level.
        """,
        validator=strict_discrete_set,
        values=[
            "EDGE",
            "WIDTH",
            "TIMEOUT",
            "RUNT",
            "WINDOW",
            "LOGIC",
            "SETHOLD",
            "TRANSITION",
            "BUS",
        ],
        check_set_errors=True,
        cast=str,
    )

    trigger_source = Instrument.control(
        "TRIGger:A:EDGE:SOUrce?",
        "TRIGger:A:EDGE:SOUrce %s",
        """Control the edge trigger source (str strictly in CH1 to CH<n>, LINE, AUXILIARY).

        The channels are the analog channels of the model, CH1 to CH4 on every model and up to
        CH8 on the MSO58B. LINE is the AC line voltage. AUXILIARY is the auxiliary input, which
        only models with one accept. Digital channel sources cannot be set, but are read back.
        """,
        validator=strict_discrete_set,
        values=["CH1", "CH2", "CH3", "CH4", "LINE", "AUXILIARY"],
        check_set_errors=True,
        cast=str,
        dynamic=True,
    )

    trigger_slope = Instrument.control(
        "TRIGger:A:EDGE:SLOpe?",
        "TRIGger:A:EDGE:SLOpe %s",
        """Control the edge trigger slope (str strictly in RISE, FALL, EITHER).""",
        validator=strict_discrete_set,
        values=["RISE", "FALL", "EITHER"],
        check_set_errors=True,
        cast=str,
    )

    trigger_coupling = Instrument.control(
        "TRIGger:A:EDGE:COUPling?",
        "TRIGger:A:EDGE:COUPling %s",
        """Control the edge trigger coupling (str strictly in DC, HFREJ, LFREJ, NOISEREJ).

        DC passes the whole signal to the trigger circuit. HFREJ attenuates above 50 kHz and
        LFREJ below 80 kHz. NOISEREJ increases the trigger hysteresis, which triggers more
        stably on a noisy signal but needs a larger amplitude.
        """,
        validator=strict_discrete_set,
        values=["DC", "HFREJ", "LFREJ", "NOISEREJ"],
        check_set_errors=True,
        cast=str,
    )

    trigger_mode = Instrument.control(
        "TRIGger:A:MODe?",
        "TRIGger:A:MODe %s",
        """Control the A trigger mode (str strictly in AUTO, NORMAL).

        AUTO generates a trigger when none is detected within a time period; NORMAL waits for a
        valid trigger event. Selecting AUTO reverts ``stop_after`` to RUNSTOP, and selecting
        SEQUENCE there switches this to NORMAL.
        """,
        validator=strict_discrete_set,
        values=["AUTO", "NORMAL"],
        check_set_errors=True,
        cast=str,
    )

    trigger_holdoff_type = Instrument.control(
        "TRIGger:A:HOLDoff:BY?",
        "TRIGger:A:HOLDoff:BY %s",
        """Control how the A trigger holdoff is determined (str strictly in TIME, RANDOM).

        TIME holds off for ``trigger_holdoff_time``; RANDOM holds off for a time the instrument
        chooses at random.
        """,
        validator=strict_discrete_set,
        values=["TIME", "RANDOM"],
        check_set_errors=True,
        cast=str,
    )

    trigger_holdoff_time = Instrument.control(
        "TRIGger:A:HOLDoff:TIMe?",
        "TRIGger:A:HOLDoff:TIMe %.12g",
        """Control the A trigger holdoff time in seconds (float strictly from 0 to 10).

        Applies while ``trigger_holdoff_type`` is TIME.
        """,
        validator=strict_range,
        values=[0, 10],
        check_set_errors=True,
    )

    trigger_state = Instrument.measurement(
        "TRIGger:STATE?",
        """Get the state of the trigger system (str, one of ARMED, AUTO, READY, SAVE, TRIGGER).

        ARMED: acquiring pretrigger data. AUTO: in AUTO mode, acquiring without a trigger.
        READY: pretrigger data acquired, waiting for a trigger; only now does
        :meth:`force_trigger` work. SAVE: stopped, not acquiring. TRIGGER: triggered, acquiring
        the post-trigger data.
        """,
        cast=str,
    )

    def force_trigger(self) -> None:
        """Force a trigger event, like the front-panel Force button.

        The instrument ignores it unless ``trigger_state`` is READY, so completing a single
        sequence that waits in NORMAL mode means polling the state and forcing once it is READY.
        """
        self.write("TRIGger FORCe")

    def set_trigger_level_to_midpoint(self) -> None:
        """Set the A trigger level to the midpoint of the trigger source signal's range.

        Equivalent to pushing the front-panel Level knob. The instrument drops set commands sent
        within about 2 s afterwards without reporting an error, so wait with ``complete`` before
        sending the next one.
        """
        self.write("TRIGger:A SETLevel")

    persistence = Instrument.control(
        "DISplay:PERSistence?",
        "DISplay:PERSistence %s",
        """Control the display persistence (str strictly in OFF, AUTO, INFPERSIST, VARPERSIST).

        OFF shows only the latest acquisition. INFPERSIST accumulates every acquired waveform
        point until :meth:`reset_persistence`, which keeps rare transients visible. VARPERSIST
        lets the points fade over ``persistence_decay_time``, and AUTO lets the instrument
        choose the decay time.
        """,
        validator=strict_discrete_set,
        values=["OFF", "AUTO", "INFPERSIST", "VARPERSIST"],
        check_set_errors=True,
        cast=str,
    )

    # The manual's lower limit of 0.5 s is wrong: the instrument accepts down to 0.05 s (bench).
    persistence_decay_time = Instrument.control(
        "DISplay:VARpersist?",
        "DISplay:VARpersist %.12g",
        """Control the persistence decay time in seconds (float strictly from 0.05 to 100).

        The approximate time a freshly acquired waveform point takes to fade while
        ``persistence`` is VARPERSIST. The instrument rounds the value to the nearest 0.05 s
        without reporting an error, so read the property back to learn the time that applies.
        """,
        validator=strict_range,
        values=[0.05, 100],
        check_set_errors=True,
    )

    def reset_persistence(self) -> None:
        """Clear the accumulated persistence data, leaving ``persistence`` unchanged."""
        self.write("DISplay:PERSistence:RESET")
