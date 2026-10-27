"""Text input of the list parameters of the ripple procedure, checked as it is typed."""

import logging
from collections.abc import Sequence

from pymeasure.display.inputs import VectorInput
from pymeasure.display.Qt import QtWidgets
from pymeasure.units import ureg

from pilgrim.procedure.parameters import FloatListParameter, IntegerListParameter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# Pint writes the micro prefix with a Greek mu, which is hard to type; "u" means the same to it.
_MICRO_SIGNS = ("μ", "µ")


def format_element(value: float, units: str | None) -> str:
    """Return one list element with an SI prefix and the units, e.g. ``"20 MHz"`` or ``"1 us"``.

    :param value: The element, in ``units``.
    :param units: The units of the list parameter; without them the plain number is returned.
    :returns: The element to 12 significant digits.
    """
    if units is None:
        return f"{value:.12g}"
    text = f"{ureg.Quantity(value, units).to_compact():~.12g}"
    for sign in _MICRO_SIGNS:
        text = text.replace(sign, "u")
    return text


class ListInput(VectorInput):
    """Text box for a list parameter, such as ``[20 MHz, 250 MHz, 1 GHz]``, checked as typed.

    Values are shown with SI prefixes and the parameter's units to 12 significant digits, and
    may be typed in any form the parameter converts, such as ``[20e6, 250e6, 1e9]`` or
    ``[1 us, 1 ms]``. While the text is not a valid list, an error icon whose tooltip gives the
    reason appears in the box, and :attr:`error` holds that reason.

    :param parameter: The list parameter.
    :param parent: The parent widget.
    """

    def __init__(
        self,
        parameter: FloatListParameter | IntegerListParameter,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parameter, parent=parent)
        self._error: str | None = None
        self.textChanged.connect(self._check)
        self._check(self.text())

    @property
    def error(self) -> str | None:
        """Get why the text is not a valid list, or None while it is valid."""
        return self._error

    def set_parameter(self, parameter: FloatListParameter | IntegerListParameter) -> None:
        """Connect the list parameter and show its value.

        :param parameter: The list parameter.
        """
        # The mixin keeps the parameter untyped; this reference gives the checks its type.
        self._list_parameter = parameter
        super().set_parameter(parameter)

    def setValue(self, value: Sequence[float]) -> None:
        """Show a list with SI prefixes and the parameter's units, e.g. ``[1 us, 1 ms]``.

        :param value: The elements, in the parameter's units.
        """
        units = self._list_parameter.units
        self.setText("[" + ", ".join(format_element(element, units) for element in value) + "]")

    def _check(self, text: str) -> None:
        try:
            self._list_parameter.convert(text)
        except ValueError as error:
            self._show_error(str(error))
        else:
            self._clear_error()

    def _show_error(self, reason: str) -> None:
        # The error icon is the only action of the box.
        self._error = reason
        if not self.actions():
            icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxCritical)
            self.addAction(icon, QtWidgets.QLineEdit.ActionPosition.TrailingPosition)
        self.actions()[0].setToolTip(reason)

    def _clear_error(self) -> None:
        self._error = None
        for action in self.actions():
            self.removeAction(action)
