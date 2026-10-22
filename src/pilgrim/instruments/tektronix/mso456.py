"""Driver for the Tektronix MSO4/5/6 series of mixed signal oscilloscopes."""

import logging
import math
import re
from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING, ClassVar, NoReturn
from urllib.parse import quote, unquote

from pymeasure.adapters import Adapter
from pymeasure.instruments import Channel, Instrument
from pymeasure.instruments.common_base import DynamicProperty
from pymeasure.instruments.generic_types import SCPIMixin
from pymeasure.instruments.validators import (
    strict_discrete_range,
    strict_discrete_set,
    strict_range,
)
from pyvisa import VisaIOError
from pyvisa.constants import StatusCode

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


# A measurement result the instrument cannot compute reads as 9.91E+37, the IEEE 488.2 value for
# "not a number".
INVALID_RESULT = 9.91e37
# A measurement can take a math or reference waveform as its source; their number is not fixed.
_WAVEFORM_SOURCE = re.compile(r"(MATH|REF)[1-9]\d*")


def _nan_if_invalid(value: float) -> float:
    """Translate the instrument's invalid-result value to NaN."""
    return math.nan if value >= INVALID_RESULT else value


def _strip_quotes(reply: str) -> str:
    """Strip the double quotes that delimit a string reply."""
    return reply.removeprefix('"').removesuffix('"')


# A results table is named TABLE<n>: the instrument refuses any other name (event 151) and
# TABLE0 (event 131).
_TABLE_NAME = re.compile(r"TABLE[1-9]\d*")


def _validate_table_name(name: str) -> None:
    """Raise ``ValueError`` unless ``name`` is a results table name, ``TABLE<n>`` with n >= 1.

    :param name: The name to validate.
    """
    if not isinstance(name, str) or not _TABLE_NAME.fullmatch(name):
        raise ValueError(f"Results table name must be TABLE<n> with n of 1 or more, not {name!r}.")


def _quote_path(path: str) -> str:
    """Return ``path`` as the quoted string argument of a file system command.

    :param path: The file or directory path.
    :returns: The path in double quotes.
    :raises ValueError: If the path is not a string, is empty or holds a double quote, which
        would end the quoted string.
    """
    if not isinstance(path, str) or not path or '"' in path:
        raise ValueError(f"Path must be a non-empty string without double quotes, not {path!r}.")
    return f'"{path}"'


# A directory listing is a comma separated list of quoted names, and a name may hold a comma.
_QUOTED_NAME = re.compile(r'"([^"]*)"')


def _directory_names(listing: str) -> list[str]:
    """Parse the names in a directory listing, which is ``""`` for an empty directory.

    :param listing: The raw reply to ``FILESystem:DIR?``.
    :returns: The names, in the order listed.
    """
    return [name for name in _QUOTED_NAME.findall(listing) if name]


def _measurement_source(value: str, values: Sequence[str]) -> str:
    """Validate a measurement source: an analog channel in ``values`` or any MATH<n> or REF<n>.

    :param value: The source to validate.
    :param values: The analog channels of the instrument, e.g. ``["CH1", "CH2"]``.
    :returns: ``value`` unchanged.
    :raises ValueError: If the value is neither a listed channel nor a math or reference
        waveform.
    """
    if value in values or (isinstance(value, str) and _WAVEFORM_SOURCE.fullmatch(value)):
        return value
    raise ValueError(f"Value of {value} is not in {list(values)} or of the form MATH<n> or REF<n>")


# The global reference levels are set per edge and level, with the edge keyword followed by the
# level keyword, e.g. RISEHigh.
_EDGES = {"RISE": "rising", "FALL": "falling"}
_LEVELS = {"High": "high", "Low": "low", "Mid": "mid"}


def _percent_reference_level(edge: str, level: str) -> property | DynamicProperty:
    """Create the control for one global reference level given as a percentage.

    :param edge: ``RISE`` or ``FALL``, the edge the level applies to.
    :param level: ``High``, ``Low`` or ``Mid``, which of the levels of that edge it is.
    :returns: The property.
    """
    command = f"MEASUrement:REFLevels:PERCent:{edge}{level}"
    return Instrument.control(
        f"{command}?",
        f"{command} %.12g",
        f"""Control the {_LEVELS[level]} reference level of the {_EDGES[edge]} edge as a
        percentage of the waveform amplitude, from the base to the top (float strictly from 0 to
        100).

        Applies while ``reference_level_method`` is PERCENT. ``reference_level_percent_preset``
        sets all six percentages at once; select CUSTOM there to set them individually. The
        instrument clamps a value it does not accept silently, without reporting an error, so
        read the property back to learn the percentage that applies.
        """,
        validator=strict_range,
        values=[0, 100],
        check_set_errors=True,
    )


def _absolute_reference_level(edge: str, level: str) -> property | DynamicProperty:
    """Create the control for one global reference level given as an absolute value.

    :param edge: ``RISE`` or ``FALL``, the edge the level applies to.
    :param level: ``High``, ``Low`` or ``Mid``, which of the levels of that edge it is.
    :returns: The property.
    """
    command = f"MEASUrement:REFLevels:ABSolute:{edge}{level}"
    return Instrument.control(
        f"{command}?",
        f"{command} %.12g",
        f"""Control the {_LEVELS[level]} reference level of the {_EDGES[edge]} edge in volts
        (float).

        Applies while ``reference_level_method`` is ABSOLUTE. The value is in the vertical units
        of the measurement source, volts for an analog channel.
        """,
        check_set_errors=True,
    )


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


class TektronixMSO456Measurement(Channel):
    """Represent one measurement, ``MEAS<n>``, of the Tektronix MSO4/5/6 series oscilloscopes.

    Instances are created with :meth:`TektronixMSO456.add_measurement` and removed with
    :meth:`TektronixMSO456.delete_measurement`; ``id`` is the measurement number. The type a
    new measurement starts with is not fixed (the bench showed PERIOD and AMPLITUDE), so set
    ``type`` before use.

    Every acquisition adds values to the statistics: one for a whole-record type such as MEAN or
    MAXIMUM, and one per edge or cycle in the record for a per-cycle type such as RISETIME or
    FREQUENCY. The ``current_*`` results cover the latest acquisition only; the others
    accumulate over all acquisitions until :meth:`TektronixMSO456.clear_acquisitions` or a new
    single sequence resets them. A result the instrument cannot compute, e.g. an edge time on a
    signal of too low amplitude, reads as NaN and queues a measurement error (22xx) or warning
    (54x), which :meth:`TektronixMSO456.check_errors` reads.
    """

    # Required by PyMeasure's Child protocol, which add_child is typed against.
    _name: str
    _protected: bool
    _collection: str
    # Set by add_measurement to the analog channels of the model.
    source_values: list[str]

    #: The measurement types every model provides without an option (manual pages 945 to 951);
    #: ``type`` accepts these. The types that need two sources (DELAY, HOLD, PHASE, SETUP, SKEW)
    #: use a second source this driver does not configure.
    TYPES: ClassVar[list[str]] = [
        "ACRMS",
        "AMPLITUDE",
        "AREA",
        "BASE",
        "BURSTWIDTH",
        "DELAY",
        "FALLSLEWRATE",
        "FALLTIME",
        "FREQUENCY",
        "HIGHTIME",
        "HOLD",
        "LOWTIME",
        "MAXIMUM",
        "MEAN",
        "MINIMUM",
        "NDUTY",
        "NOVERSHOOT",
        "NPERIOD",
        "NWIDTH",
        "PDUTY",
        "PERIOD",
        "PHASE",
        "PK2PK",
        "POVERSHOOT",
        "PWIDTH",
        "RISESLEWRATE",
        "RISETIME",
        "RMS",
        "SETUP",
        "SKEW",
        "TIMEOUTSIDELEVEL",
        "TOP",
    ]

    type = Channel.control(
        "MEASUrement:MEAS{ch}:TYPe?",
        "MEASUrement:MEAS{ch}:TYPe %s",
        """Control the measurement type (str strictly in :attr:`TYPES`, e.g. MEAN, MINIMUM,
        MAXIMUM, TOP, BASE, PK2PK, RISETIME, FALLTIME).

        Per-cycle types such as RISETIME, FALLTIME, PERIOD and FREQUENCY are taken on every
        edge or cycle in the record, so their ``population`` grows by several values per
        acquisition, while MEAN, MINIMUM, MAXIMUM, TOP, BASE and PK2PK add one value per
        acquisition. FREQUENCY, PERIOD and PDUTY need two rising edges in the record, otherwise
        they read as NaN with a 540 warning.
        """,
        validator=strict_discrete_set,
        values=TYPES,
        check_set_errors=True,
        cast=str,
    )

    source = Channel.control(
        "MEASUrement:MEAS{ch}:SOURCE?",
        "MEASUrement:MEAS{ch}:SOURCE %s",
        """Control the waveform the measurement is taken on (str strictly in CH1 to CH<n>,
        MATH<n>, REF<n>).

        The analog channels are those of the model, CH1 to CH4 on every model and up to CH8 on
        the MSO58. A math or reference waveform must exist on the instrument, which this driver
        cannot check, so the instrument rejects a missing one.
        """,
        validator=_measurement_source,
        values=["CH1", "CH2", "CH3", "CH4"],
        check_set_errors=True,
        cast=str,
        dynamic=True,
    )

    label = Channel.control(
        "MEASUrement:MEAS{ch}:LABel?",
        'MEASUrement:MEAS{ch}:LABel "%s"',
        """Control the label shown on the measurement badge and in the results table (str).

        Any text round-trips, including quotes, commas, percent signs and non-ASCII characters;
        the instrument stores it percent-encoded.
        """,
        set_process=partial(quote, safe=""),
        preprocess_reply=_strip_quotes,
        get_process=unquote,
        check_set_errors=True,
        cast=str,
    )

    current_mean = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:CURRentacq:MEAN?",
        """Measure the mean of the values from the latest acquisition (float, NaN if invalid).

        For a whole-record type this is the single value of the acquisition. Reads as NaN once
        ``population_limit`` is reached.
        """,
        get_process=_nan_if_invalid,
    )

    current_minimum = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:CURRentacq:MINimum?",
        """Measure the minimum of the values from the latest acquisition (float, NaN if invalid).

        Reads as NaN once ``population_limit`` is reached.
        """,
        get_process=_nan_if_invalid,
    )

    current_maximum = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:CURRentacq:MAXimum?",
        """Measure the maximum of the values from the latest acquisition (float, NaN if invalid).

        Reads as NaN once ``population_limit`` is reached.
        """,
        get_process=_nan_if_invalid,
    )

    current_peak_to_peak = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:CURRentacq:PK2PK?",
        """Measure the spread of the values from the latest acquisition (float, NaN if invalid).

        Zero for a whole-record type, which has a single value per acquisition. Reads as NaN
        once ``population_limit`` is reached.
        """,
        get_process=_nan_if_invalid,
    )

    current_population = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:CURRentacq:POPUlation?",
        """Measure the number of values from the latest acquisition (int).

        One for a whole-record type, one per edge or cycle for a per-cycle type, and 0 once
        ``population_limit`` is reached.
        """,
        cast=int,
    )

    current_standard_deviation = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:CURRentacq:STDDev?",
        """Measure the standard deviation of the values from the latest acquisition (float, NaN
        if invalid).

        Zero for a whole-record type, which has a single value per acquisition. Reads as NaN
        once ``population_limit`` is reached.
        """,
        get_process=_nan_if_invalid,
    )

    mean = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:ALLAcqs:MEAN?",
        """Measure the mean of the values accumulated over all acquisitions (float, NaN if
        invalid).""",
        get_process=_nan_if_invalid,
    )

    minimum = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:ALLAcqs:MINimum?",
        """Measure the minimum of the values accumulated over all acquisitions (float, NaN if
        invalid).""",
        get_process=_nan_if_invalid,
    )

    maximum = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:ALLAcqs:MAXimum?",
        """Measure the maximum of the values accumulated over all acquisitions (float, NaN if
        invalid).""",
        get_process=_nan_if_invalid,
    )

    peak_to_peak = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:ALLAcqs:PK2PK?",
        """Measure the spread of the values accumulated over all acquisitions (float, NaN if
        invalid).""",
        get_process=_nan_if_invalid,
    )

    population = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:ALLAcqs:POPUlation?",
        """Measure the number of values accumulated over all acquisitions (int).

        Grows by one per acquisition for a whole-record type and by one per edge or cycle for a
        per-cycle type, and stops at ``population_limit`` while the limit is enabled.
        """,
        cast=int,
    )

    standard_deviation = Channel.measurement(
        "MEASUrement:MEAS{ch}:RESUlts:ALLAcqs:STDDev?",
        """Measure the standard deviation of the values accumulated over all acquisitions (float,
        NaN if invalid).""",
        get_process=_nan_if_invalid,
    )

    population_limit_enabled = Channel.control(
        "MEASUrement:MEAS{ch}:POPUlation:LIMIT:STATE?",
        "MEASUrement:MEAS{ch}:POPUlation:LIMIT:STATE %d",
        """Control whether the statistics stop after ``population_limit`` values (bool).

        Acquisition carries on at the limit, but the all-acquisitions statistics freeze and the
        ``current_*`` results read as NaN with a population of 0. Disabling the limit resumes
        the statistics where they stopped. Applies only while ``uses_global_population_limit``
        is False.
        """,
        validator=strict_discrete_set,
        map_values=True,
        values={True: 1, False: 0},
        preprocess_reply=_boolean_reply,
        check_set_errors=True,
    )

    population_limit = Channel.control(
        "MEASUrement:MEAS{ch}:POPUlation:LIMIT:VALue?",
        "MEASUrement:MEAS{ch}:POPUlation:LIMIT:VALue %d",
        """Control the number of values after which the statistics stop (int strictly 1 or
        more).

        Applies while ``population_limit_enabled`` is True. Counts values, not acquisitions, so
        a per-cycle type reaches it after fewer acquisitions than a whole-record type.
        """,
        validator=partial(strict_discrete_range, step=1),
        values=[1, math.inf],
        check_set_errors=True,
        cast=int,
    )

    uses_global_population_limit = Channel.control(
        "MEASUrement:MEAS{ch}:POPUlation:GLOBal?",
        "MEASUrement:MEAS{ch}:POPUlation:GLOBal %d",
        """Control whether the instrument's global population limit applies to this measurement
        instead of ``population_limit_enabled`` and ``population_limit`` (bool).

        A new measurement defaults to False. The global limit is not configurable through this
        driver.
        """,
        validator=strict_discrete_set,
        map_values=True,
        values={True: 1, False: 0},
        preprocess_reply=_boolean_reply,
        check_set_errors=True,
    )

    badge_statistics_enabled = Channel.control(
        "MEASUrement:MEAS{ch}:DISPlaystat:ENABle?",
        "MEASUrement:MEAS{ch}:DISPlaystat:ENABle %d",
        """Control whether the measurement badge on the display shows the statistics (bool).

        Affects only the display; the statistics are computed and readable regardless.
        """,
        validator=strict_discrete_set,
        map_values=True,
        values={True: 1, False: 0},
        preprocess_reply=_boolean_reply,
        check_set_errors=True,
    )


class TektronixMSO456(SCPIMixin, Instrument):
    """Represent the Tektronix MSO4/5/6 series of mixed signal oscilloscopes.

    Individual models are represented by subclasses, which add the analog channels beyond
    ``ch1`` to ``ch4``. Measurements are added at runtime with :meth:`add_measurement` and
    collected in ``measurements``. Connecting switches response headers off and verbose keywords
    on, which persist on the instrument, and clears the event queue.
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
        #: The measurements added with :meth:`add_measurement`, by number.
        self.measurements: dict[int, TektronixMSO456Measurement] = {}

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
        return self._raise_on_errors("the setting")

    def _raise_on_errors(self, subject: str) -> list[tuple[int, str]]:
        """Read the event queue and raise ``ValueError`` naming ``subject`` if it holds errors.

        :param subject: What the instrument rejected, e.g. "the setting".
        :returns: The warnings read from the queue, since any error raises instead.
        :raises ValueError: If the instrument reports an error.
        """
        events = self.check_errors()
        errors = [(code, message) for code, message in events if code not in WARNING_CODES]
        if errors:
            details = "; ".join(f"{code}, {message}" for code, message in errors)
            raise ValueError(f"{self.name} rejected {subject}: {details}")
        return events

    acquisition_enabled = Instrument.control(
        "ACQuire:STATE?",
        "ACQuire:STATE %d",
        """Control whether the instrument is acquiring (bool).

        Enabling starts a new acquisition: a single sequence if ``stop_after`` is SEQUENCE,
        which restarts ``acquisition_count``, otherwise a continuous one, which carries the
        count on. It does not reset the all-acquisitions measurement statistics;
        :meth:`clear_acquisitions` does. A single sequence finishes only once its acquisitions
        have been triggered, so waiting for it with ``complete`` blocks until then.
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
        """Get the number of acquisitions since the count was last reset (int).

        A single sequence restarts the count, so it equals ``sequence_count`` once the sequence
        has finished. :meth:`clear_acquisitions` resets it a moment after it completes. Enabling
        acquisition in RUNSTOP mode carries the count on, although the manual says it restarts.
        """,
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
        CH8 on the MSO58. LINE is the AC line voltage. AUXILIARY is the auxiliary input, which
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

    measurement_names = Instrument.measurement(
        "MEASUrement:LIST?",
        """Get the names of the measurements defined on the instrument (list of str, e.g.
        ``["MEAS1", "MEAS2"]``), including any added from its user interface.""",
        cast=str,
        get_process=lambda name: [] if name == "NONE" else [name],
    )

    def add_measurement(self, number: int) -> TektronixMSO456Measurement:
        """Add measurement ``number`` to the instrument and return its interface.

        The measurement starts with a type the instrument chooses, so set its ``type``. It is
        also available as ``measurements[number]`` and as the attribute ``meas_<number>``. Do
        not create measurement interfaces in any other way: querying a measurement number the
        instrument does not have creates that measurement silently.

        :param number: The measurement number (int, 1 or more), shown on its badge.
        :returns: The measurement.
        :raises ValueError: If the number is below 1, or the instrument refuses the measurement,
            e.g. because it exists already (event 100) or because the instrument is busy (event
            221, seen once on the bench right after a reset).
        """
        if not isinstance(number, int) or number < 1:
            raise ValueError(f"Measurement number must be an int of 1 or more, not {number!r}.")
        self.write(f'MEASUrement:ADDNew "MEAS{number}"')
        self._raise_on_errors(f"adding measurement {number}")
        measurement = self.add_child(
            TektronixMSO456Measurement, number, collection="measurements", prefix="meas_"
        )
        # The source accepts every analog channel of the model, which the subclasses extend.
        measurement.source_values = [f"CH{n}" for n in self.channels]
        return measurement

    def delete_measurement(self, measurement: TektronixMSO456Measurement) -> None:
        """Delete a measurement from the instrument and remove its interface.

        The interface is only removed once the instrument confirms the deletion.

        :param measurement: A measurement returned by :meth:`add_measurement`.
        :raises ValueError: If the instrument refuses the deletion, e.g. because the measurement
            no longer exists (event 100).
        """
        self.write(f'MEASUrement:DELete "MEAS{measurement.id}"')
        self._raise_on_errors(f"deleting measurement {measurement.id}")
        self.remove_child(measurement)

    def delete_all_measurements(self) -> None:
        """Delete every measurement from the instrument and remove every interface.

        Also deletes measurements added from the instrument's user interface. The interfaces
        are only removed once the instrument confirms the deletion.

        :raises ValueError: If the instrument refuses the deletion.
        """
        self.write("MEASUrement:DELETEALL")
        self._raise_on_errors("deleting every measurement")
        for measurement in list(self.measurements.values()):
            self.remove_child(measurement)

    def add_results_table(self, name: str) -> None:
        """Add a measurement results table to the display.

        The table lists every measurement defined on the instrument with its statistics,
        including those added later, so nothing assigns measurements to it. The instrument
        provides no query for the tables it has, so keep track of the names added.

        :param name: The table name, ``TABLE<n>`` with n of 1 or more, e.g. ``"TABLE1"``.
        :raises ValueError: If the name has another form, or the instrument refuses the table,
            e.g. because it exists already (event 100).
        """
        _validate_table_name(name)
        self.write(f'MEASTABle:ADDNew "{name}"')
        self._raise_on_errors(f"adding results table {name}")

    def delete_results_table(self, name: str) -> None:
        """Delete a measurement results table from the display, keeping the measurements.

        :param name: The table name, ``TABLE<n>`` with n of 1 or more, e.g. ``"TABLE1"``.
        :raises ValueError: If the name has another form, or the instrument refuses the
            deletion, e.g. because the table does not exist (event 100).
        """
        _validate_table_name(name)
        self.write(f'MEASTABle:DELETE "{name}"')
        self._raise_on_errors(f"deleting results table {name}")

    reference_level_method = Instrument.control(
        "MEASUrement:REFLevels:METHod?",
        "MEASUrement:REFLevels:METHod %s",
        """Control how the global reference levels are given (str strictly in PERCENT,
        ABSOLUTE).

        The reference levels mark where the edge measurements are taken: rise and fall time are
        measured between the low and high levels, while period, frequency and pulse width use
        the mid level. PERCENT takes the levels from the six ``reference_level_percent_*``
        properties, relative to the base and top of the waveform, so they follow its amplitude;
        ABSOLUTE takes them from the six ``reference_level_absolute_*`` properties in volts. The
        global levels apply to every measurement that uses them rather than its own, a
        per-measurement choice this driver does not configure.
        """,
        validator=strict_discrete_set,
        values=["PERCENT", "ABSOLUTE"],
        check_set_errors=True,
        cast=str,
    )

    reference_level_mode = Instrument.control(
        "MEASUrement:REFLevels:MODE?",
        "MEASUrement:REFLevels:MODE %s",
        """Control how often the global reference levels are calculated from the waveform (str
        strictly in LATCH, CONTINUOUS).

        LATCH calculates them on the first acquisition after a statistics reset only, so they
        stay fixed while the statistics accumulate; CONTINUOUS recalculates them on every
        acquisition.
        """,
        validator=strict_discrete_set,
        values=["LATCH", "CONTINUOUS"],
        check_set_errors=True,
        cast=str,
    )

    reference_level_scope = Instrument.control(
        "MEASUrement:REFLevels:TYPE?",
        "MEASUrement:REFLevels:TYPE %s",
        """Control whether the global reference levels are shared by every source or set per
        source (str strictly in GLOBAL, PERSOURCE).

        GLOBAL applies the levels set through this driver to the measurements on every source.
        PERSOURCE gives each source its own levels, which this driver does not configure.
        """,
        validator=strict_discrete_set,
        values=["GLOBAL", "PERSOURCE"],
        check_set_errors=True,
        cast=str,
    )

    reference_level_percent_preset = Instrument.control(
        "MEASUrement:REFLevels:PERCent:TYPE?",
        "MEASUrement:REFLevels:PERCent:TYPE %s",
        """Control the preset for the global reference levels given as percentages (str strictly
        in TENNINETY, TWENTYEIGHTY, CUSTOM).

        TENNINETY sets the low, mid and high levels of both edges to 10, 50 and 90 percent,
        TWENTYEIGHTY to 20, 50 and 80 percent. CUSTOM allows the six
        ``reference_level_percent_*`` properties to be set to other values. Applies while
        ``reference_level_method`` is PERCENT.
        """,
        validator=strict_discrete_set,
        values=["TENNINETY", "TWENTYEIGHTY", "CUSTOM"],
        check_set_errors=True,
        cast=str,
    )

    reference_level_percent_rise_high = _percent_reference_level("RISE", "High")
    reference_level_percent_rise_mid = _percent_reference_level("RISE", "Mid")
    reference_level_percent_rise_low = _percent_reference_level("RISE", "Low")
    reference_level_percent_fall_high = _percent_reference_level("FALL", "High")
    reference_level_percent_fall_mid = _percent_reference_level("FALL", "Mid")
    reference_level_percent_fall_low = _percent_reference_level("FALL", "Low")
    reference_level_absolute_rise_high = _absolute_reference_level("RISE", "High")
    reference_level_absolute_rise_mid = _absolute_reference_level("RISE", "Mid")
    reference_level_absolute_rise_low = _absolute_reference_level("RISE", "Low")
    reference_level_absolute_fall_high = _absolute_reference_level("FALL", "High")
    reference_level_absolute_fall_mid = _absolute_reference_level("FALL", "Mid")
    reference_level_absolute_fall_low = _absolute_reference_level("FALL", "Low")

    cycle_statistics_enabled = Instrument.control(
        "MEASUrement:STATIstics:CYCLEMode?",
        "MEASUrement:STATIstics:CYCLEMode %d",
        """Control whether cycle-to-cycle statistics are tracked for every measurement (bool).

        Enabling adds the cycle-to-cycle columns to the results table. It affects the values
        acquired and measured after it is enabled, not the statistics accumulated before.
        """,
        validator=strict_discrete_set,
        map_values=True,
        values={True: 1, False: 0},
        preprocess_reply=_boolean_reply,
        check_set_errors=True,
    )

    image_color_mode = Instrument.control(
        "SAVe:IMAGe:COMPosition?",
        "SAVe:IMAGe:COMPosition %s",
        """Control the colors of the screen images :meth:`save_image` saves (str strictly in
        NORMAL, INVERTED).

        NORMAL saves the colors as displayed, INVERTED saves them inverted.
        """,
        validator=strict_discrete_set,
        values=["NORMAL", "INVERTED"],
        check_set_errors=True,
        cast=str,
    )

    #: The extensions :meth:`save_image` accepts, in any case; each selects the image format.
    IMAGE_EXTENSIONS: ClassVar[tuple[str, ...]] = (".png", ".bmp", ".jpg", ".jpeg")

    def save_image(self, path: str) -> None:
        """Save a capture of the whole screen to an image file on the instrument.

        The extension selects the format, PNG, Windows bitmap or JPEG (see
        :attr:`IMAGE_EXTENSIONS`). The instrument writes nothing for another extension without
        reporting an error, so such a path is rejected here. ``image_color_mode`` selects the
        colors, and an existing file is overwritten.

        The instrument saves in the background, so wait with ``complete`` before reading the
        file with :meth:`read_file`. A save that fails, e.g. into a directory that does not
        exist, queues event 200 only once it has finished, so call :meth:`check_errors` after
        ``complete`` to detect it.

        :param path: The file path, absolute like ``"C:/shots/dut1.png"`` or relative to
            ``working_directory``.
        :raises ValueError: If the path is empty or holds a double quote, or its extension is
            not one of :attr:`IMAGE_EXTENSIONS`.
        """
        quoted = _quote_path(path)
        if not path.lower().endswith(self.IMAGE_EXTENSIONS):
            extensions = ", ".join(self.IMAGE_EXTENSIONS)
            raise ValueError(f"Image path must end in one of {extensions}, not {path!r}.")
        self.write(f"SAVe:IMAGe {quoted}")

    working_directory = Instrument.control(
        "FILESystem:CWD?",
        "FILESystem:CWD %s",
        """Control the directory on the instrument that relative paths in the file commands and
        :meth:`save_image` refer to (str).

        ``C:`` is the internal drive, ``E:``, ``F:`` and ``G:`` are the front USB ports and
        ``H:`` and ``I:`` the rear ones. Forward slashes separate the directories, as in
        ``"C:/pilgrim_test"``, and the path reads back without a trailing slash. A directory
        that does not exist is rejected with event 256, raising ``ValueError``.
        """,
        set_process=_quote_path,
        preprocess_reply=_strip_quotes,
        maxsplit=0,  # A path may hold a comma, so the reply is not split.
        check_set_errors=True,
        cast=str,
    )

    directory_listing = Instrument.measurement(
        "FILESystem:DIR?",
        """Get the names of the files and subdirectories in ``working_directory`` (list of str),
        empty for an empty directory.""",
        maxsplit=0,  # A name may hold a comma, so the reply is parsed as a whole.
        get_process=_directory_names,
        cast=str,
    )

    def make_directory(self, path: str) -> None:
        """Create a directory on the instrument's file system.

        :param path: The directory path, absolute like ``"C:/pilgrim_test"`` or relative to
            ``working_directory``.
        :raises ValueError: If the path is empty or holds a double quote, or the instrument
            refuses the directory, e.g. because it exists already (event 257).
        """
        self.write(f"FILESystem:MKDir {_quote_path(path)}")
        self._raise_on_errors(f"creating directory {path}")

    def read_file(self, path: str) -> bytes:
        """Read a file from the instrument's file system and return its contents.

        The adapter's read termination has to stay disabled, which is the default: with it
        enabled, the instrument ends the transfer at the first newline in the file and then
        blocks until a device clear.

        :param path: The file path, absolute like ``"C:/shots/dut1.png"`` or relative to
            ``working_directory``.
        :returns: The contents of the file.
        :raises ValueError: If the path is empty or holds a double quote, or the instrument
            refuses the file, e.g. because it does not exist (event 256) or is a directory
            (event 257). The instrument sends nothing then, so this is detected only once the
            adapter's read times out.
        """
        self.write(f"FILESystem:READFile {_quote_path(path)}")
        try:
            # Reads up to the end of the message in one go; without break_on_termchar the VISA
            # adapter reads byte by byte until it times out.
            reply = self.read_bytes(-1, break_on_termchar=True)
        except VisaIOError as exc:
            if exc.error_code != StatusCode.error_timeout:
                raise
            self._raise_on_errors(f"reading file {path}")
            raise
        # The instrument sends the file bytes followed by one newline as a single message.
        return reply.removesuffix(b"\n")

    def delete_file(self, path: str) -> None:
        """Delete a file or an empty directory from the instrument's file system.

        :param path: The path, absolute like ``"C:/shots/dut1.png"`` or relative to
            ``working_directory``.
        :raises ValueError: If the path is empty or holds a double quote, or the instrument
            refuses the deletion, e.g. because the path does not exist (event 256) or is a
            directory that is not empty (event 250).
        """
        self.write(f"FILESystem:DELEte {_quote_path(path)}")
        self._raise_on_errors(f"deleting {path}")
