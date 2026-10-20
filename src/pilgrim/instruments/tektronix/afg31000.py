"""Driver for the Tektronix AFG31000 series of arbitrary function generators."""

from typing import Any, ClassVar

from pymeasure.adapters import Adapter
from pymeasure.instruments import Channel, Instrument
from pymeasure.instruments.generic_types import SCPIMixin
from pymeasure.instruments.validators import strict_discrete_set, strict_range

type Range = tuple[float, float]


class TektronixAFG31000Channel(Channel):
    """Represent a Tektronix AFG31000 series output channel.

    Each model subclasses this channel and defines ``<property>_values`` for every property
    named in ``model_ranges``; defining a subclass that misses one raises ``TypeError``.
    """

    # Required by PyMeasure's Child protocol, which ChannelCreator is typed against.
    _name: str
    _protected: bool
    _collection: str

    model_ranges: ClassVar[tuple[str, ...]] = (
        "frequency",
        "offset",
        "amplitude_high",
        "amplitude_low",
        "pulse_leading",
        "pulse_trailing",
    )
    frequency_values: ClassVar[Range]
    offset_values: ClassVar[Range]
    amplitude_high_values: ClassVar[Range]
    amplitude_low_values: ClassVar[Range]
    pulse_leading_values: ClassVar[Range]
    pulse_trailing_values: ClassVar[Range]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        missing = [f"{n}_values" for n in cls.model_ranges if not hasattr(cls, f"{n}_values")]
        if missing:
            raise TypeError(f"{cls.__name__} must define {', '.join(missing)}")

    shape = Channel.control(
        "SOUR{ch}:FUNC:SHAP?",
        "SOUR{ch}:FUNC:SHAP %s",
        """Control the output waveform shape (str strictly in SIN, SQU, PULS, RAMP).

        The shapes are sine, square, pulse, and ramp.
        """,
        validator=strict_discrete_set,
        values=["SIN", "SQU", "PULS", "RAMP"],
        cast=str,
    )

    frequency = Channel.control(
        "SOUR{ch}:FREQ?",
        "SOUR{ch}:FREQ %.12g",
        """Control the waveform frequency in Hz (float strictly in the model's range).

        The model's range is that of a sine wave. The other shapes have lower maxima, and the
        instrument rejects frequencies above them, raising ``ValueError``.
        """,
        validator=strict_range,
        dynamic=True,
        check_set_errors=True,
        cast=float,
    )

    amplitude = Channel.control(
        "SOUR{ch}:VOLT?",
        "SOUR{ch}:VOLT %g",
        """Control the amplitude in the unit set by ``amplitude_unit`` (float).

        The range depends on the unit, the waveform shape, and the load impedance setting, so
        only the instrument checks it; values outside it raise ``ValueError``.
        """,
        check_set_errors=True,
        cast=float,
    )

    amplitude_unit = Channel.control(
        "SOUR{ch}:VOLT:UNIT?",
        "SOUR{ch}:VOLT:UNIT %s",
        """Control the amplitude unit (str strictly in VPP, VRMS, DBM).

        DBM applies only to sine waves with a load impedance setting other than High-Z; the
        instrument rejects it otherwise, raising ``ValueError``. Changing the shape away from
        sine switches DBM back to VPP.
        """,
        validator=strict_discrete_set,
        values=["VPP", "VRMS", "DBM"],
        check_set_errors=True,
        cast=str,
    )

    offset = Channel.control(
        "SOUR{ch}:VOLT:OFFS?",
        "SOUR{ch}:VOLT:OFFS %g",
        """Control the offset voltage in Volts (float strictly in the model's range).

        The model's range applies to a High-Z load setting at the minimum amplitude. A larger
        amplitude or a lower load impedance setting narrows it, and the instrument rejects
        values outside it, raising ``ValueError``.
        """,
        validator=strict_range,
        dynamic=True,
        check_set_errors=True,
        cast=float,
    )

    amplitude_high = Channel.control(
        "SOUR{ch}:VOLT:HIGH?",
        "SOUR{ch}:VOLT:HIGH %g",
        """Control the upper voltage level in Volts (float strictly in the model's range).

        The model's range applies to a High-Z load setting. Lower load impedance settings
        narrow it, and the instrument rejects levels outside it, raising ``ValueError``.
        Setting the upper level below the lower level moves the lower level down with it.
        """,
        validator=strict_range,
        dynamic=True,
        check_set_errors=True,
        cast=float,
    )

    amplitude_low = Channel.control(
        "SOUR{ch}:VOLT:LOW?",
        "SOUR{ch}:VOLT:LOW %g",
        """Control the lower voltage level in Volts (float strictly in the model's range).

        The model's range applies to a High-Z load setting. Lower load impedance settings
        narrow it, and the instrument rejects levels outside it, raising ``ValueError``.
        Setting the lower level above the upper level moves the upper level up with it.
        """,
        validator=strict_range,
        dynamic=True,
        check_set_errors=True,
        cast=float,
    )

    pulse_dutycycle = Channel.control(
        "SOUR{ch}:PULS:DCYC?",
        "SOUR{ch}:PULS:DCYC %g",
        """Control the pulse wave duty cycle in percent (float strictly from 0.001 to 99.999).

        At short pulse periods the pulse width limits narrow this range, and the instrument
        rejects values outside it, raising ``ValueError``.
        """,
        validator=strict_range,
        values=[0.001, 99.999],
        check_set_errors=True,
        cast=float,
    )

    pulse_hold = Channel.control(
        "SOUR{ch}:PULS:HOLD?",
        "SOUR{ch}:PULS:HOLD %s",
        """Control whether the pulse width or duty cycle is held (str strictly in WIDT, DUTY).

        WIDT keeps the pulse width as the period changes, and DUTY keeps the duty cycle.
        """,
        validator=strict_discrete_set,
        values=["WIDT", "DUTY"],
        cast=str,
    )

    # The instrument takes edge times in seconds; the two edge properties expose microseconds.
    pulse_leading = Channel.control(
        "SOUR{ch}:PULS:TRAN:LEAD?",
        "SOUR{ch}:PULS:TRAN:LEAD %g",
        """Control the leading edge time in microseconds (float strictly in the model's range).

        The pulse period and width narrow this range, and the instrument rejects values
        outside it, raising ``ValueError``.
        """,
        validator=strict_range,
        dynamic=True,
        set_process=lambda v: 1e-6 * v,
        get_process=lambda v: 1e6 * v,
        check_set_errors=True,
        cast=float,
    )

    pulse_trailing = Channel.control(
        "SOUR{ch}:PULS:TRAN:TRA?",
        "SOUR{ch}:PULS:TRAN:TRA %g",
        """Control the trailing edge time in microseconds (float strictly in the model's range).

        The pulse period and width narrow this range, and the instrument rejects values
        outside it, raising ``ValueError``.
        """,
        validator=strict_range,
        dynamic=True,
        set_process=lambda v: 1e-6 * v,
        get_process=lambda v: 1e6 * v,
        check_set_errors=True,
        cast=float,
    )

    output_enabled = Channel.control(
        "OUTP{ch}:STAT?",
        "OUTP{ch}:STAT %d",
        """Control whether the output is on (bool).""",
        validator=strict_discrete_set,
        map_values=True,
        values={True: 1, False: 0},
    )


class TektronixAFG31000(SCPIMixin, Instrument):
    """Represent the Tektronix AFG31000 arbitrary function generator series.

    Individual models are represented by subclasses. Connecting clears the error queue, and
    a setting the instrument rejects raises ``ValueError``.

    :param adapter: VISA resource name or adapter instance.
    :param name: Name of the instrument.
    :param write_termination: Termination appended to every command; used only when
        ``adapter`` is a resource name.
    :param kwargs: Keyword arguments for the VISA adapter.
    """

    def __init__(
        self,
        adapter: Adapter | str,
        name: str = "Tektronix AFG31000",
        write_termination: str = "\n",
        **kwargs,
    ) -> None:
        super().__init__(adapter, name, write_termination=write_termination, **kwargs)
        self.clear()  # Power-up messages left in the error queue would fail the first setter.

    @property
    def next_error(self) -> list[float | str]:
        """Get the next error in the queue as a code and message (list)."""
        # Split once only, so commas inside the message do not break it apart.
        return self.values("SYST:ERR?", maxsplit=1)

    def check_set_errors(self) -> list:
        """Read the error queue after setting a property and raise if it holds any errors.

        :returns: An empty list, since any error raises instead.
        :raises ValueError: If the instrument reports an error, e.g. a value outside the range
            allowed by its current settings.
        """
        errors = self.check_errors()
        if errors:
            details = "; ".join(f"{int(code)}, {message}" for code, message in errors)
            raise ValueError(f"{self.name} rejected the setting: {details}")
        return errors
