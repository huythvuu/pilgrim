"""Main window of the transient GUI."""

from pilgrim.gui.window import ProcedureWindow
from pilgrim.procedure import TransientProcedure
from pilgrim.procedure.procedure_transient import (
    FALL_TIME_COLUMN,
    OVERSHOOT_COLUMN,
    PK2PK_COLUMN,
    RISE_TIME_COLUMN,
    UNDERSHOOT_COLUMN,
    VMAX_COLUMN,
    VMEAN_COLUMN,
    VMIN_COLUMN,
)

from .components import INPUTS, TransientInputsWidget


class TransientWindow(ProcedureWindow):
    """Run the transient procedure: edit its parameters, queue runs and follow them.

    A :class:`ProcedureWindow` of :class:`TransientProcedure`. In safe mode the procedure stops
    the acquisition and pauses once the instruments are set for an input; the message panel
    then names the input, so the scope can be adjusted before Resume starts the acquisition.

    :param parent: The parent widget.
    """

    PROCEDURE_CLASS = TransientProcedure
    INPUTS_WIDGET = TransientInputsWidget
    INPUT_NAMES = INPUTS
    DISPLAYS = ("vin", "vout_channel", "iout_channel", "safe_mode")
    TITLE = "Transient response"
    DEFAULT_FILENAME = "{Procedure name}_{Vin:g}Vin"
    RESULT_COLUMNS = (
        VMEAN_COLUMN,
        VMAX_COLUMN,
        VMIN_COLUMN,
        PK2PK_COLUMN,
        OVERSHOOT_COLUMN,
        UNDERSHOOT_COLUMN,
        RISE_TIME_COLUMN,
        FALL_TIME_COLUMN,
    )
    MESSAGE_IDLE_TEXT = (
        "The procedure reports here when it pauses, after each input in safe mode or on Pause."
    )

    # ProcedureWindow builds the inputs from INPUTS_WIDGET, which a type checker cannot follow.
    inputs: TransientInputsWidget
