"""Unit tests for the accuracy-CI sample-size math (no LLM)."""
import pytest

from qaai.eval.sample_size import (
    achieved_margin,
    n_normal,
    n_wilson,
    required_sample_size,
    z_for_confidence,
)

pytestmark = pytest.mark.unit


def test_z_for_95_percent():
    assert z_for_confidence(0.95) == pytest.approx(1.959963, abs=1e-4)


def test_n_normal_worst_case():
    # 95% confidence, +/-5%, p=0.5 -> textbook 385
    assert n_normal(0.95, 0.05, 0.5) == 385


def test_n_normal_expected_p85():
    assert n_normal(0.95, 0.05, 0.85) == 196


def test_wilson_not_larger_than_normal_worst_case():
    assert n_wilson(0.95, 0.05, 0.5) <= n_normal(0.95, 0.05, 0.5)


def test_achieved_margin_round_trips_normal():
    n = n_normal(0.95, 0.05, 0.5)
    hw = achieved_margin(n, 0.95, 0.5)
    assert hw["normal"] <= 0.05 + 1e-9


def test_finite_population_correction_shrinks_n():
    assert n_normal(0.95, 0.05, 0.5, population=200) < n_normal(0.95, 0.05, 0.5)


def test_required_bundle_has_both_methods():
    r = required_sample_size(0.95, 0.05, 0.85)
    assert r["n_normal"] == 196 and r["n_wilson"] >= 1


@pytest.mark.parametrize("bad", [1.5, 0.0, -0.1])
def test_confidence_out_of_range(bad):
    with pytest.raises(ValueError):
        z_for_confidence(bad)


def test_margin_out_of_range():
    with pytest.raises(ValueError):
        n_normal(0.95, 0.0, 0.5)
