"""Procedure parameters that type check as their values when read from a procedure."""

import re
from tokenize import TokenError
from typing import TYPE_CHECKING, Any, Self, overload

import numpy as np
import pint
from pymeasure import experiment
from pymeasure.units import ureg


class _TypedParameter[T]:
    # Procedure.__init__ shadows each class-level parameter with its value on the instance, which
    # a type checker cannot follow; this descriptor signature tells it so without runtime effect.
    if TYPE_CHECKING:

        @overload
        def __get__(self, instance: None, owner: type[Any]) -> Self: ...
        @overload
        def __get__(self, instance: experiment.Procedure, owner: type[Any]) -> T: ...
        def __get__(self, instance: experiment.Procedure | None, owner: type[Any]) -> Self | T: ...

        # PyMeasure leaves Parameter.value untyped. Accepting any input while reading as T
        # also stops a type checker from narrowing the value to whatever was assigned.
        @property
        def value(self) -> T: ...
        @value.setter
        def value(self, value: object) -> None: ...


class IntegerParameter(_TypedParameter[int], experiment.IntegerParameter):
    """Integer procedure parameter that reads as ``int`` on a procedure instance."""


class FloatParameter(_TypedParameter[float], experiment.FloatParameter):
    """Float procedure parameter that reads as ``float`` on a procedure instance."""


class BooleanParameter(_TypedParameter[bool], experiment.BooleanParameter):
    """Boolean procedure parameter that reads as ``bool`` on a procedure instance."""


class StringParameter(_TypedParameter[str], experiment.Parameter):
    """String procedure parameter that reads as ``str`` on a procedure instance.

    Only strings are accepted, including the empty string, so a number is rejected rather than
    converted; the GUI shows it as a text input.

    :param name: The parameter name
    :param kwargs: Passed on to :class:`pymeasure.experiment.Parameter`, such as ``default``
    """

    def convert(self, value: Any) -> str:
        """Return the value unchanged if it is a string.

        :param value: The string.
        :returns: The same string.
        :raises ValueError: If the value is not a string.
        """
        if not isinstance(value, str):
            raise ValueError(f"{self.name} must be a string, not {type(value).__name__}.")
        return value


class _BoundedListParameter(experiment.VectorParameter):
    # Unit conversion and bounds shared by the list parameters below; convert() is theirs.

    def __init__(
        self,
        name: str,
        units: str | None = None,
        minimum: float = -1e9,
        maximum: float = 1e9,
        **kwargs: Any,
    ) -> None:
        self.minimum = minimum
        self.maximum = maximum
        super().__init__(name, units=units, **kwargs)
        # convert() ignores the inherited fixed length, so keep it out of the help text.
        self._help_fields.remove(("length is", "_length"))
        self._help_fields.extend(["minimum", "maximum"])

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__}(name={self.name},value={self._value},units={self.units},"
            f"minimum={self.minimum},maximum={self.maximum})>"
        )

    def _list_contents(self, value: str) -> str:
        """Return the text between the square brackets of a list string, without its units.

        :param value: The list as a string, optionally followed by ``units``.
        :returns: The stripped contents of the brackets, empty for an empty list.
        :raises ValueError: If the value is not enclosed in square brackets.
        """
        text = value.strip()
        if self.units is not None:
            text = text.removesuffix(f" {self.units}").rstrip()
        if not (text.startswith("[") and text.endswith("]")):
            raise ValueError(f"{self.name} must be a list in square brackets, not {value!r}.")
        return text[1:-1].strip()

    if TYPE_CHECKING:
        # PyMeasure leaves VectorParameter.convert() untyped, so a type checker infers
        # list[float] from its body, which a list of ranges would contradict.
        def convert(self, value: Any) -> list[Any]: ...

    def _convert_element(self, element: Any) -> float:
        """Convert one element to a float in ``units`` and check it against the bounds.

        :param element: A number, Pint quantity, or string such as ``"1 kHz"``.
        :returns: The element as a float in ``units``.
        :raises ValueError: If the element is not a number in compatible units or lies outside
            ``minimum`` to ``maximum``.
        """
        try:
            quantity = ureg.Quantity(element.strip() if isinstance(element, str) else element)
            if quantity.units == ureg.dimensionless:
                number = float(quantity.magnitude)
            else:
                number = float(quantity.m_as(self.units or ureg.dimensionless))
        # Pint tokenizes strings with Python's tokenizer, which raises TokenError, not a
        # ValueError, on an unclosed parenthesis such as "(100".
        except (pint.PintError, TokenError, TypeError, ValueError) as error:
            raise ValueError(
                f"{self.name} element {element!r} is not a number in {self.units}."
            ) from error
        if not self.minimum <= number <= self.maximum:
            raise ValueError(
                f"{self.name} element {number:g} is outside {self.minimum:g} to {self.maximum:g}."
            )
        return number


class FloatListParameter(_TypedParameter[list[float]], _BoundedListParameter):
    """Float list procedure parameter of any length, even zero, that reads as ``list[float]``.

    Elements may carry units, as Pint quantities or strings such as ``"1 kHz"``; plain numbers
    are taken to be in ``units`` already.
    As a string, the value is a bracketed list such as ``"[100 Hz, 1 kHz]"`` or
    ``"[100, 1000] Hz"``.

    :param name: The parameter name
    :param units: The units of measure of each element
    :param minimum: The minimum allowed value of each element (default: -1e9)
    :param maximum: The maximum allowed value of each element (default: 1e9)
    :param kwargs: Passed on to :class:`pymeasure.experiment.Parameter`, such as ``default``
    """

    def convert(self, value: Any) -> list[float]:
        """Convert a list, or its string form, to a list of floats in ``units``.

        :param value: A list, tuple, or array of numbers, quantities, or strings, or a string
            listing them in square brackets.
        :returns: The elements as floats in ``units``.
        :raises ValueError: If the value is not a list, or an element is not a number in
            compatible units or lies outside ``minimum`` to ``maximum``.
        """
        if isinstance(value, str):
            contents = self._list_contents(value)
            elements = contents.split(",") if contents else []
        elif isinstance(value, list | tuple | np.ndarray):
            elements = value
        else:
            raise ValueError(f"{self.name} must be a list, not {type(value).__name__}.")
        return [self._convert_element(element) for element in elements]


class FloatRangeListParameter(_TypedParameter[list[tuple[float, float]]], _BoundedListParameter):
    """Float range list procedure parameter that reads as ``list[tuple[float, float]]``.

    Each range is a ``(start, stop)`` pair with ``start`` no greater than ``stop``; ranges may
    overlap, and the list can have any length, including zero.
    Bounds may carry units, as Pint quantities or strings such as ``"1 kHz"``; plain numbers
    are taken to be in ``units`` already.
    As a string, the value is ``"[(0, 0.25), (0.5, 1)]"`` or, with units,
    ``"[(100, 1000), (1 kHz, 10 kHz)] Hz"``.

    :param name: The parameter name
    :param units: The units of measure of each range bound
    :param minimum: The minimum allowed value of each range bound (default: -1e9)
    :param maximum: The maximum allowed value of each range bound (default: 1e9)
    :param kwargs: Passed on to :class:`pymeasure.experiment.Parameter`, such as ``default``
    """

    def convert(self, value: Any) -> list[tuple[float, float]]:
        """Convert a list of ranges, or its string form, to a list of float pairs in ``units``.

        :param value: A list, tuple, or array of ``(start, stop)`` pairs of numbers, quantities,
            or strings, or a string listing them in square brackets.
        :returns: The ranges as ``(start, stop)`` pairs of floats in ``units``.
        :raises ValueError: If the value is not a list of pairs, a bound is not a number in
            compatible units or lies outside ``minimum`` to ``maximum``, or a range starts after
            it stops.
        """
        if isinstance(value, str):
            contents = self._list_contents(value)
            if not contents:
                return []
            if not (contents.startswith("(") and contents.endswith(")")):
                raise ValueError(
                    f"{self.name} must list ranges in parentheses, such as [(0, 1)], not {value!r}."
                )
            ranges = [pair.split(",") for pair in re.split(r"\)\s*,\s*\(", contents[1:-1])]
        elif isinstance(value, list | tuple | np.ndarray):
            ranges = value
        else:
            raise ValueError(f"{self.name} must be a list, not {type(value).__name__}.")
        return [self._convert_range(bounds) for bounds in ranges]

    def _convert_range(self, bounds: Any) -> tuple[float, float]:
        """Convert one ``(start, stop)`` pair to floats in ``units`` and check its order.

        :param bounds: A list, tuple, or array of two elements, each a number, quantity, or
            string.
        :returns: The range as a ``(start, stop)`` pair of floats in ``units``.
        :raises ValueError: If the bounds are not a pair, a bound is not a valid element, or
            ``start`` is greater than ``stop``.
        """
        # Sequence patterns match lists and tuples but not strings or arrays, so convert arrays.
        match bounds.tolist() if isinstance(bounds, np.ndarray) else bounds:
            case [first, second]:
                start, stop = self._convert_element(first), self._convert_element(second)
            case _:
                raise ValueError(f"{self.name} range {bounds!r} is not a (start, stop) pair.")
        if start > stop:
            raise ValueError(f"{self.name} range ({start:g}, {stop:g}) starts after it stops.")
        return start, stop
