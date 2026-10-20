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
