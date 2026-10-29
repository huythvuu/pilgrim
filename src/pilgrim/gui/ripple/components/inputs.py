"""Input fields of the ripple procedure's parameters."""

import logging

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
    "cursor_hi",
    "cursor_lo",
    "pop_min",
    "scope_capture_dir",
)


class RippleInputsWidget(InputsWidget):
    """Input fields of the ripple procedure's parameters, one per name in :data:`INPUTS`.

    The scope resource is chosen from the instruments VISA finds (:class:`VisaResourceInput`),
    the lists of loads, horizontal scales and bandwidths are typed with live checking
    (:class:`ListInput`), and the other parameters get PyMeasure's inputs for their types. The
    cursor levels, ``cursor_hi`` and ``cursor_lo``, are shown only while the ``use_cursor`` box
    is checked, since the procedure ignores them otherwise. Each input is an attribute named
    after its parameter, e.g. ``loads``.

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
    cursor_hi: ScientificInput
    cursor_lo: ScientificInput
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

    def _setup_ui(self) -> None:
        # PyMeasure builds the input of a parameter from its ui_class when it has one, and hides
        # the inputs of a parameter grouped by another while that one's input is off. Both are
        # set on the parameters of this widget's own procedure instance, so the procedure class
        # itself stays free of GUI concerns.
        for name, parameter in self._procedure.parameter_objects().items():
            if name == "scope_resource":
                parameter.ui_class = VisaResourceInput
            elif name in ("cursor_hi", "cursor_lo"):
                parameter.group_by = {"use_cursor": True}
            elif isinstance(parameter, FloatListParameter | IntegerListParameter):
                parameter.ui_class = ListInput
        super()._setup_ui()
