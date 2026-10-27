"""Input of the VISA resource name of the scope, listing the instruments VISA finds."""

import logging
from collections.abc import Callable, Sequence

import pyvisa
from pymeasure.display.inputs import Input
from pymeasure.display.Qt import QtWidgets
from pymeasure.experiment import Parameter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


def list_visa_resources() -> list[str]:
    """Return the resource names of the instruments VISA finds, sorted.

    :returns: The names; empty when no VISA library is installed or the listing fails, which
        is logged as a warning.
    """
    try:
        return sorted(pyvisa.ResourceManager().list_resources())
    except (OSError, ValueError, pyvisa.Error) as error:
        log.warning(f"Could not list the VISA resources: {error}")
        return []


class VisaResourceInput(Input, QtWidgets.QComboBox):
    """Editable drop-down of VISA resource names, connected to a string parameter.

    Opening the drop-down lists the instruments VISA finds at that moment, and any resource
    name can be typed as well, such as an alias from NI MAX.

    :param parameter: The parameter holding the resource name.
    :param list_resources: Returns the resource names to list; :func:`list_visa_resources` by
        default.
    :param parent: The parent widget.
    """

    def __init__(
        self,
        parameter: Parameter,
        list_resources: Callable[[], Sequence[str]] = list_visa_resources,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parameter=parameter, parent=parent)
        self._list_resources = list_resources

    def set_parameter(self, parameter: Parameter) -> None:
        """Connect the parameter and show its value as the editable text.

        :param parameter: The parameter holding the resource name.
        """
        # The text must be editable before the value is set, or an empty list swallows it.
        self.setEditable(True)
        self.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        super().set_parameter(parameter)
        self.setToolTip(f"{self.toolTip()}\nOpen the list to see the instruments VISA finds.")

    def showPopup(self) -> None:
        """Fill the drop-down with the instruments VISA finds, keeping the text, then open it."""
        text = self.currentText()
        self.clear()
        self.addItems(list(self._list_resources()))
        self.setCurrentText(text)
        super().showPopup()

    def setValue(self, value: str) -> None:
        """Show a resource name as the text.

        :param value: The resource name.
        """
        self.setCurrentText(value)

    def setSuffix(self, value: str) -> None:
        """Ignore the units suffix, since a resource name has no units.

        :param value: The suffix PyMeasure would show.
        """

    def value(self) -> str:
        """Return the resource name typed or chosen, without surrounding whitespace."""
        return self.currentText().strip()
