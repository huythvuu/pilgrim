"""Procedure parameters that type check as their values when read from a procedure."""

from typing import TYPE_CHECKING, Any, Self, overload

from pymeasure import experiment


class _TypedParameter[T]:
    # Procedure.__init__ shadows each class-level parameter with its value on the instance, which
    # a type checker cannot follow; this descriptor signature tells it so without runtime effect.
    if TYPE_CHECKING:

        @overload
        def __get__(self, instance: None, owner: type[Any]) -> Self: ...
        @overload
        def __get__(self, instance: experiment.Procedure, owner: type[Any]) -> T: ...
        def __get__(self, instance: experiment.Procedure | None, owner: type[Any]) -> Self | T: ...


class IntegerParameter(_TypedParameter[int], experiment.IntegerParameter):
    """Integer procedure parameter that reads as ``int`` on a procedure instance."""


class FloatParameter(_TypedParameter[float], experiment.FloatParameter):
    """Float procedure parameter that reads as ``float`` on a procedure instance."""


class BooleanParameter(_TypedParameter[bool], experiment.BooleanParameter):
    """Boolean procedure parameter that reads as ``bool`` on a procedure instance."""
