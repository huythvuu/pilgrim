"""Input of a directory on this computer, browsable like the results directory."""

import logging

from pymeasure.display.inputs import StringInput
from pymeasure.display.widgets.directory_widget import DirectoryLineEdit
from pymeasure.experiment import Parameter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class DirectoryInput(StringInput, DirectoryLineEdit):
    """Text box for a directory on this computer, with completion and a browse button.

    Connected to a string parameter such as the local capture directory. The button at the end
    opens a directory dialog, starting at the directory typed when it exists, and the text
    completes from the file system while it is typed.

    :param parameter: The parameter holding the directory.
    :param parent: The parent widget.
    """

    def set_parameter(self, parameter: Parameter) -> None:
        """Connect the parameter and show its value as the text.

        :param parameter: The parameter holding the directory.
        """
        super().set_parameter(parameter)
        self.setToolTip(f"{self.toolTip()}\nThe button at the end browses for the directory.")
