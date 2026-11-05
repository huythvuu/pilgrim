"""Main window of the ripple GUI."""

from pilgrim.gui.window import ProcedureWindow
from pilgrim.procedure import RippleProcedure
from pilgrim.procedure.procedure_ripple import (
    BANDWIDTH_COLUMN,
    PK2PK_COLUMN,
    RIPPLE_COLUMN,
    VMAX_COLUMN,
    VMEAN_COLUMN,
    VMIN_COLUMN,
)

from .components import INPUTS, RippleInputsWidget


class RippleWindow(ProcedureWindow):
    """Run the ripple procedure: edit its parameters, queue runs and follow them.

    A :class:`ProcedureWindow` of :class:`RippleProcedure`, whose message panel asks for the
    load to be set whenever the procedure pauses for it.

    :param parent: The parent widget.
    """

    PROCEDURE_CLASS = RippleProcedure
    INPUTS_WIDGET = RippleInputsWidget
    INPUT_NAMES = INPUTS
    DISPLAYS = ("vin", "channel", "loads", "horizontal_divs", "bandwidths")
    TITLE = "Ripple capture"
    DEFAULT_FILENAME = "{Procedure name}_{Vin:g}Vin"
    RESULT_COLUMNS = (
        BANDWIDTH_COLUMN,
        VMEAN_COLUMN,
        VMAX_COLUMN,
        VMIN_COLUMN,
        PK2PK_COLUMN,
        RIPPLE_COLUMN,
    )
    MESSAGE_IDLE_TEXT = (
        "The procedure reports here when it pauses, whenever the load must be set or on Pause."
    )

    # ProcedureWindow builds the inputs from INPUTS_WIDGET, which a type checker cannot follow.
    inputs: RippleInputsWidget
