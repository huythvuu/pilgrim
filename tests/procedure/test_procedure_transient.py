"""Tests for the transient procedure that need no instruments."""

import pytest

from pilgrim.procedure import TransientProcedure


def make_procedure(**parameters: object) -> TransientProcedure:
    """Build a procedure whose parameter attributes hold the given values, as during a run.

    :param parameters: Parameter values by name, in any form the parameter converts.
    :returns: The procedure after ``refresh_parameters()``.
    """
    procedure = TransientProcedure(**parameters)
    procedure.refresh_parameters()
    return procedure


def test_checkpoints_default_to_empty() -> None:
    procedure = TransientProcedure()
    assert procedure.checkpoints == []
    assert procedure.sweep_frequencies() == []


def test_checkpoints_accept_frequencies_with_units() -> None:
    procedure = make_procedure(checkpoints="[100 Hz, 1 kHz, 10 kHz, 100 kHz, 1 MHz]")
    assert procedure.checkpoints == [100.0, 1e3, 1e4, 1e5, 1e6]


def test_sweep_steps_by_ratio_of_each_range() -> None:
    frequencies = make_procedure(checkpoints=[100, 1e3, 1e4]).sweep_frequencies()
    assert len(frequencies) == 100 + 100 + 1
    assert frequencies[:3] == pytest.approx([100, 109, 118])
    assert frequencies[99:102] == pytest.approx([991, 1e3, 1090])
    assert frequencies[-2:] == pytest.approx([9910, 1e4])


def test_sweep_shortens_last_step_when_ratio_does_not_divide_range() -> None:
    frequencies = make_procedure(checkpoints=[0, 100], sweep_ratio=0.3).sweep_frequencies()
    assert frequencies == pytest.approx([0, 30, 60, 90, 100])


@pytest.mark.parametrize("steps", [3, 7, 49, 93, 98, 103])
def test_sweep_does_not_repeat_checkpoints_through_float_error(steps: int) -> None:
    frequencies = make_procedure(
        checkpoints=[100, 1e3, 1e4], sweep_ratio=1 / steps
    ).sweep_frequencies()
    assert len(frequencies) == 2 * steps + 1
    assert len(set(frequencies)) == len(frequencies)


def test_sweep_of_single_checkpoint_is_that_checkpoint() -> None:
    assert make_procedure(checkpoints=[1e3]).sweep_frequencies() == [1e3]


def test_sweep_at_full_ratio_visits_only_checkpoints() -> None:
    assert make_procedure(checkpoints=[100, 1e3, 1e4], sweep_ratio=1.0).sweep_frequencies() == [
        100,
        1e3,
        1e4,
    ]


LOAD_STEPS = "[(0, .25), (.25, .75), (.5, 1), (0, 1)]"


@pytest.mark.parametrize(
    ("frequency", "scale"),
    [(1e3, 100e-6), (50.0, 2e-3), (1e6, 100e-9), (1e-3, 100.0)],
)
def test_horizontal_scale_fits_one_cycle_on_ten_divisions(frequency: float, scale: float) -> None:
    procedure = TransientProcedure()
    assert procedure.horizontal_scale_for(frequency) == pytest.approx(scale)


def test_horizontal_scale_fits_the_cycles_on_screen() -> None:
    procedure = make_procedure(cycles_on_screen=5)
    assert procedure.horizontal_scale_for(1e3) == pytest.approx(500e-6)
    with pytest.raises(ValueError, match="minimum"):
        make_procedure(cycles_on_screen=0)


def test_minimum_population_defaults_to_300_and_must_be_positive() -> None:
    assert make_procedure().pop_min == 300  # noqa: PLR2004
    assert make_procedure(pop_min=1).pop_min == 1
    with pytest.raises(ValueError, match="minimum"):
        make_procedure(pop_min=0)


def test_raw_columns_hold_every_statistic_of_every_measurement() -> None:
    columns = TransientProcedure.RAW_COLUMNS
    conditions = ["Load low (V)", "Load high (V)", "Frequency (Hz)", "Duty (%)"]
    assert columns[: len(conditions)] == conditions
    assert columns[len(conditions) : len(conditions) + 5] == [
        "Vout MEAN mean (V)",
        "Vout MEAN minimum (V)",
        "Vout MEAN maximum (V)",
        "Vout MEAN standard deviation (V)",
        "Vout MEAN population",
    ]
    assert "Iout RISETIME standard deviation (s)" in columns
    assert columns[-1] == "Iout FALLTIME population"
    measurements = len(TransientProcedure.SCOPE_MEASUREMENTS)
    assert len(columns) == len(conditions) + measurements * len(TransientProcedure.STATISTICS)
    assert len(set(columns)) == len(columns)


def test_data_columns_add_the_extreme_label_and_the_capture_path() -> None:
    raw = TransientProcedure.RAW_COLUMNS
    assert ["Extreme", *raw, "Capture"] == TransientProcedure.DATA_COLUMNS


def test_capture_directory_is_the_capture_destination(tmp_path) -> None:
    procedure = make_procedure(capture_dest=str(tmp_path / "captures"))
    assert procedure.capture_directory == tmp_path / "captures"


VMAX, VMIN, PK2PK = "Vout MAXIMUM maximum (V)", "Vout MINIMUM minimum (V)", "Vout PK2PK maximum (V)"


def row(frequency: float, vmax: float, vmin: float, pk2pk: float) -> dict[str, float]:
    """Build a row of results with the given Vout extremes at ``frequency``.

    :param frequency: The frequency in Hz, which identifies the row.
    :param vmax: The highest maximum voltage.
    :param vmin: The lowest minimum voltage.
    :param pk2pk: The highest peak-to-peak voltage.
    :returns: The row.
    """
    return {"Frequency (Hz)": frequency, VMAX: vmax, VMIN: vmin, PK2PK: pk2pk}


def test_extreme_columns_are_data_columns() -> None:
    for _, column, _ in TransientProcedure.EXTREMES:
        assert column in TransientProcedure.DATA_COLUMNS
    assert [column for _, column, _ in TransientProcedure.EXTREMES] == [VMAX, VMIN, PK2PK]


def test_find_extremes_picks_the_row_of_each_extreme() -> None:
    rows = [row(100, 1.0, -0.5, 1.5), row(200, 1.2, -0.1, 1.3), row(300, 0.9, -0.9, 1.8)]
    extremes = TransientProcedure.find_extremes(rows)
    assert extremes == {
        "Highest Vmax": rows[1],
        "Lowest Vmin": rows[2],
        "Highest peak-to-peak": rows[2],
    }


def test_find_extremes_passes_over_nan_and_leaves_out_empty_labels() -> None:
    nan = float("nan")
    rows = [row(100, nan, nan, 1.5), row(200, 1.2, nan, nan)]
    assert TransientProcedure.find_extremes(rows) == {
        "Highest Vmax": rows[1],
        "Highest peak-to-peak": rows[0],
    }
    assert TransientProcedure.find_extremes([]) == {}


def test_load_steps_default_to_empty() -> None:
    procedure = TransientProcedure()
    assert procedure.load_ranges == []
    assert procedure.calc_load_steps() == []


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        (0.0, 0.5, [(0.0, 0.125), (0.125, 0.375), (0.25, 0.5), (0.0, 0.5)]),
        (-0.25, 0.25, [(-0.25, -0.125), (-0.125, 0.125), (0.0, 0.25), (-0.25, 0.25)]),
    ],
)
def test_load_steps_scale_between_levels(
    low: float, high: float, expected: list[tuple[float, float]]
) -> None:
    procedure = make_procedure(low_level=low, high_level=high, load_ranges=LOAD_STEPS)
    steps = procedure.calc_load_steps()
    assert len(steps) == len(expected)
    for step, expected_step in zip(steps, expected, strict=True):
        assert step == pytest.approx(expected_step)


def test_full_load_step_lands_exactly_on_levels() -> None:
    procedure = make_procedure(low_level=-0.1, high_level=0.3, load_ranges=[(0, 1)])
    assert procedure.calc_load_steps() == [(-0.1, 0.3)]


def test_load_steps_reject_fractions_outside_levels() -> None:
    with pytest.raises(ValueError, match="Load steps"):
        make_procedure(load_ranges=[(0.5, 1.5)])
