"""Input fields of the ripple procedure's parameters."""

import logging
from typing import Any

from pymeasure.display.inputs import BooleanInput, IntegerInput, ScientificInput, StringInput
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.widgets import InputsWidget

from pilgrim.procedure import RippleProcedure
from pilgrim.procedure.parameters import FloatListParameter, IntegerListParameter

from .list_input import ListInput
from .visa_resource_input import VisaResourceInput

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: The parameters with an input field, in the order they are shown.
INPUTS = (
    "name",
    "scope_resource",
    "channel",
    "vin",
    "loads",
    "horizontal_divs",
    "bandwidths",
    "target",
    "use_cursor",
    "cursor_absolute",
    "cursor_hi_rel",
    "cursor_lo_rel",
    "cursor_hi_abs",
    "cursor_lo_abs",
    "pop_min",
    "scope_capture_dir",
)
#: The text of the ``cursor_absolute`` box, shorter than the parameter name since it shares its
#: line with the ``use_cursor`` box.
ABSOLUTE_TEXT = "Absolute"
#: The cursor levels shown while the ``cursor_absolute`` box is unchecked, or checked.
RELATIVE_CURSOR_INPUTS = ("cursor_hi_rel", "cursor_lo_rel")
ABSOLUTE_CURSOR_INPUTS = ("cursor_hi_abs", "cursor_lo_abs")


class RippleInputsWidget(InputsWidget):
    """Input fields of the ripple procedure's parameters, one per name in :data:`INPUTS`.

    The scope resource is chosen from the instruments VISA finds (:class:`VisaResourceInput`),
    the lists of loads, horizontal scales and bandwidths are typed with live checking
    (:class:`ListInput`), and the other parameters get PyMeasure's inputs for their types. The
    ``use_cursor`` box and the ``cursor_absolute`` box, labelled "Absolute", share one line.
    The cursor levels are shown only while the ``use_cursor`` box is checked, since the
    procedure ignores them otherwise: the absolute ones, ``cursor_hi_abs`` and
    ``cursor_lo_abs``, while the ``cursor_absolute`` box is checked, and the relative ones,
    ``cursor_hi_rel`` and ``cursor_lo_rel``, while it is not; the box itself is also hidden
    while the cursors are not used. Each input is an attribute named after its parameter, e.g.
    ``loads``.

    :param parent: The parent widget.
    :param inputs_in_scrollarea: Whether to put the inputs in a scroll area, so the widget can
        shrink below their height and a scroll bar reaches the rest.
    """

    # InputsWidget sets the input of each parameter as an attribute of its name, which a type
    # checker cannot follow.
    name: StringInput
    scope_resource: VisaResourceInput
    channel: IntegerInput
    vin: IntegerInput
    loads: ListInput
    horizontal_divs: ListInput
    bandwidths: ListInput
    target: ScientificInput
    use_cursor: BooleanInput
    cursor_absolute: BooleanInput
    cursor_hi_rel: ScientificInput
    cursor_lo_rel: ScientificInput
    cursor_hi_abs: ScientificInput
    cursor_lo_abs: ScientificInput
    pop_min: IntegerInput
    scope_capture_dir: StringInput

    def __init__(
        self, parent: QtWidgets.QWidget | None = None, inputs_in_scrollarea: bool = False
    ) -> None:
        super().__init__(
            RippleProcedure, INPUTS, parent=parent, inputs_in_scrollarea=inputs_in_scrollarea
        )

    def get_procedure(self) -> RippleProcedure:
        """Return a new ripple procedure with the values entered.

        :raises ValueError: If a list is not valid.
        """
        return super().get_procedure()

    def set_parameters(self, parameter_objects: dict[str, Any]) -> None:
        """Show the values of the parameters given, e.g. those of an earlier run.

        :param parameter_objects: The parameter objects by name, with one for every input.
        """
        super().set_parameters(parameter_objects)
        # PyMeasure resets the text of a check box to its parameter's name with the value.
        self.cursor_absolute.setText(ABSOLUTE_TEXT)

    def _setup_ui(self) -> None:
        # PyMeasure builds the input of a parameter from its ui_class when it has one, and hides
        # the inputs of a parameter grouped by others while their inputs are not in the state
        # given. Both are set on the parameters of this widget's own procedure instance, so the
        # procedure class itself stays free of GUI concerns.
        for name, parameter in self._procedure.parameter_objects().items():
            if name == "scope_resource":
                parameter.ui_class = VisaResourceInput
            elif name == "cursor_absolute":
                parameter.group_by = {"use_cursor": True}
            elif name in RELATIVE_CURSOR_INPUTS:
                parameter.group_by = {"use_cursor": True, "cursor_absolute": False}
            elif name in ABSOLUTE_CURSOR_INPUTS:
                parameter.group_by = {"use_cursor": True, "cursor_absolute": True}
            elif isinstance(parameter, FloatListParameter | IntegerListParameter):
                parameter.ui_class = ListInput
        super()._setup_ui()
        self.cursor_absolute.setText(ABSOLUTE_TEXT)

    def _layout(self, inputs_in_scrollarea: bool) -> None:
        super()._layout(inputs_in_scrollarea)
        # PyMeasure stacks every input in one column; the two cursor boxes are moved out of it
        # onto a line of their own, in the place of the first.
        parent = self.use_cursor.parentWidget()
        column = parent.layout() if parent is not None else None
        if not isinstance(column, QtWidgets.QBoxLayout):
            raise TypeError("The inputs are not stacked in a box layout.")
        index = column.indexOf(self.use_cursor)
        row = QtWidgets.QHBoxLayout()
        for box in (self.use_cursor, self.cursor_absolute):
            column.removeWidget(box)
            row.addWidget(box)
        row.addStretch()
        column.insertLayout(index, row)
