"""Tab that shows the results as a table and opens the results file of the running procedure."""

from pathlib import Path

from pymeasure.display.Qt import QtWidgets
from pymeasure.display.widgets import TableWidget

from .path_banner import PathBanner


class ResultsTableWidget(TableWidget):
    """Show the results as a table and open the results file of the running procedure.

    PyMeasure's table widget, with its Index and Layout fields kept to the left of the header
    and, at its right end, a button that opens the results file once :meth:`set_results_path`
    names it, which the window does when a procedure starts. The parameters are those of
    :class:`~pymeasure.display.widgets.TableWidget`.
    """

    def _setup_ui(self) -> None:
        super()._setup_ui()
        # TableWidget stores the layout combo box as ``layout``, which shadows QWidget.layout();
        # a typed name lets this class and its tests reach it.
        layout_combo = vars(self)["layout"]
        if not isinstance(layout_combo, QtWidgets.QComboBox):
            raise TypeError("TableWidget no longer stores the layout combo box as 'layout'.")
        self.layout_combo: QtWidgets.QComboBox = layout_combo
        # The browser below the tabs already names the results file, so only the button shows.
        self.banner = PathBanner("", "Open CSV", self)
        self.banner.label.hide()

    def _layout(self) -> None:
        # The same header as TableWidget's, with the spare width between the fields and the
        # button, which keeps the fields at their natural size on the left.
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        header.setContentsMargins(-1, 6, -1, 6)
        header.addWidget(self.column_index_label)
        header.addWidget(self.column_index_combo)
        header.addWidget(self.layout_label)
        header.addWidget(self.layout_combo)
        header.addStretch(1)
        header.addWidget(self.banner)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.table)

    def set_results_path(self, path: Path | None) -> None:
        """Name the results file the Open CSV button opens.

        :param path: The path of the results file; None names none.
        """
        self.banner.set_path(path)
