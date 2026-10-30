"""Tests for the results table tab."""

from pathlib import Path

from pymeasure.display.Qt import QtWidgets
from pytestqt.qtbot import QtBot

from pilgrim.gui.ripple.components import ResultsTableWidget

COLUMNS = ["Load (A)", "Vmean (V)"]


def make_widget(qtbot: QtBot) -> ResultsTableWidget:
    widget = ResultsTableWidget("Results Table", COLUMNS, by_column=False)
    qtbot.addWidget(widget)
    widget.resize(800, 300)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def test_keeps_the_table_of_the_columns(qtbot: QtBot) -> None:
    widget = make_widget(qtbot)
    assert widget.name == "Results Table"
    assert widget.columns == COLUMNS
    assert [widget.column_index_combo.itemText(i) for i in range(3)] == ["<None>", *COLUMNS]
    assert widget.layout_combo.currentText() == "By Row"
    assert widget.table.parentWidget() is widget


def test_index_and_layout_sit_left_of_the_open_csv_button(qtbot: QtBot) -> None:
    widget = make_widget(qtbot)
    assert widget.banner.open_button.text() == "Open CSV"
    assert not widget.banner.label.isVisibleTo(widget)  # The button stands alone.
    fields = [
        widget.column_index_label,
        widget.column_index_combo,
        widget.layout_label,
        widget.layout_combo,
    ]
    rights = [field.geometry().right() for field in fields]
    assert rights == sorted(rights)
    # The fields keep their natural width; the button stands at the far end of the row.
    assert widget.layout_combo.width() <= widget.layout_combo.sizeHint().width()
    button = widget.banner.open_button
    assert button.mapTo(widget, button.rect().topLeft()).x() > rights[-1] + widget.width() / 4
    assert button.mapTo(widget, button.rect().topRight()).x() > widget.width() * 0.9
    assert all(field.geometry().top() < widget.table.geometry().top() for field in fields)


def test_names_the_results_file(qtbot: QtBot, tmp_path: Path) -> None:
    widget = make_widget(qtbot)
    assert not widget.banner.open_button.isEnabled()
    widget.set_results_path(tmp_path / "ripple_0Vin_1.csv")
    assert widget.banner.path == tmp_path / "ripple_0Vin_1.csv"
    assert widget.banner.open_button.isEnabled()
    assert not widget.banner.label.isVisibleTo(widget)
    widget.set_results_path(None)
    assert widget.banner.path is None


def test_preview_is_a_plain_table(qtbot: QtBot) -> None:
    widget = make_widget(qtbot)
    preview = widget.preview_widget()
    qtbot.addWidget(preview)
    assert isinstance(preview, QtWidgets.QWidget)
    assert preview.name == "Table preview"
