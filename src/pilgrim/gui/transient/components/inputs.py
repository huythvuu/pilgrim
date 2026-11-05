"""Input fields of the transient procedure's parameters."""

from pymeasure.display.inputs import BooleanInput, IntegerInput, ScientificInput, StringInput
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.widgets import InputsWidget

from pilgrim.gui.components import VisaResourceInput
from pilgrim.procedure import TransientProcedure

#: The parameters with an input field, in the order they are shown.
INPUTS = (
    "name",
    "afg_resource",
    "scope_resource",
    "vin",
    "duty_cycle",
    "vout_channel",
    "iout_channel",
    "safe_mode",
    "use_cursor",
    "cursor_hi_abs",
    "cursor_lo_abs",
    "pop_min",
    "scope_capture_dir",
)
#: The parameters holding VISA resource names.
RESOURCE_INPUTS = ("afg_resource", "scope_resource")


class TransientInputsWidget(InputsWidget):
    """Input fields of the transient procedure's parameters, one per name in :data:`INPUTS`.

    The AFG and scope resources are chosen from the instruments VISA finds
    (:class:`VisaResourceInput`), and the other parameters get PyMeasure's inputs for their
    types. The cursor levels stay shown while the ``use_cursor`` box is unchecked, since the low
    one is also the trigger level of the inputs that trigger on Vout. Each input is an attribute
    named after its parameter, e.g. ``safe_mode``.

    :param parent: The parent widget.
    :param inputs_in_scrollarea: Whether to put the inputs in a scroll area, so the widget can
        shrink below their height and a scroll bar reaches the rest.
    """

    # InputsWidget sets the input of each parameter as an attribute of its name, which a type
    # checker cannot follow.
    name: StringInput
    afg_resource: VisaResourceInput
    scope_resource: VisaResourceInput
    vin: ScientificInput
    duty_cycle: IntegerInput
    vout_channel: IntegerInput
    iout_channel: IntegerInput
    safe_mode: BooleanInput
    use_cursor: BooleanInput
    cursor_hi_abs: ScientificInput
    cursor_lo_abs: ScientificInput
    pop_min: IntegerInput
    scope_capture_dir: StringInput

    def __init__(
        self, parent: QtWidgets.QWidget | None = None, inputs_in_scrollarea: bool = False
    ) -> None:
        super().__init__(
            TransientProcedure, INPUTS, parent=parent, inputs_in_scrollarea=inputs_in_scrollarea
        )

    def get_procedure(self) -> TransientProcedure:
        """Return a new transient procedure with the values entered."""
        return super().get_procedure()

    def _setup_ui(self) -> None:
        # PyMeasure builds the input of a parameter from its ui_class when it has one, set here on
        # the parameters of this widget's own procedure instance, so the procedure class itself
        # stays free of GUI concerns.
        for name, parameter in self._procedure.parameter_objects().items():
            if name in RESOURCE_INPUTS:
                parameter.ui_class = VisaResourceInput
        super()._setup_ui()
