"""Sample-size / margin math for a single-model accuracy estimate.

Sizes the labelled set needed to pin one reviewer's ``overall_verdict`` accuracy to
a target confidence interval, and inverts to report the achieved margin for a given N.
Uses only the standard library (``statistics.NormalDist``) — no scipy dependency.

Definitions
    confidence   two-sided confidence level, e.g. 0.95
    margin (m)   desired half-width of the CI (± m), e.g. 0.05
    p            expected accuracy (proportion). Unknown ⇒ use 0.5 (worst case,
                 maximizes required N).
    population   optional finite population for a finite-population correction (FPC).
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Dict, Optional


def z_for_confidence(confidence: float) -> float:
    """Two-sided z critical value for a confidence level (0<confidence<1)."""
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    return NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)


def _apply_fpc(n: int, population: Optional[int]) -> int:
    """Finite-population correction: shrink n toward the population size."""
    if not population or population <= 0:
        return n
    return math.ceil(n / (1.0 + (n - 1.0) / population))


def n_normal(confidence: float, margin: float, p: float = 0.5, population: Optional[int] = None) -> int:
    """Required N via the normal (Wald) approximation: n = z²·p(1−p)/m²."""
    if not 0.0 < margin < 1.0:
        raise ValueError("margin must be in (0, 1)")
    z = z_for_confidence(confidence)
    n = (z ** 2 * p * (1.0 - p)) / (margin ** 2)
    return _apply_fpc(math.ceil(n), population)


def _wilson_half_width(n: int, z: float, p: float) -> float:
    """Half-width of the Wilson score interval at estimate p for sample size n."""
    denom = 1.0 + z ** 2 / n
    return (z * math.sqrt(p * (1.0 - p) / n + z ** 2 / (4.0 * n ** 2))) / denom


def n_wilson(confidence: float, margin: float, p: float = 0.5, population: Optional[int] = None) -> int:
    """Smallest N whose Wilson-interval half-width at estimate p is ≤ margin."""
    if not 0.0 < margin < 1.0:
        raise ValueError("margin must be in (0, 1)")
    z = z_for_confidence(confidence)
    # Exponential search for an upper bound, then binary search for the minimum n.
    hi = 1
    while _wilson_half_width(hi, z, p) > margin:
        hi *= 2
        if hi > 10 ** 9:  # safety guard
            break
    lo = hi // 2 or 1
    while lo < hi:
        mid = (lo + hi) // 2
        if _wilson_half_width(mid, z, p) <= margin:
            hi = mid
        else:
            lo = mid + 1
    return _apply_fpc(lo, population)


def achieved_margin(n: int, confidence: float, p: float = 0.5) -> Dict[str, float]:
    """Half-width achievable with n samples at estimate p (normal + Wilson)."""
    if n <= 0:
        raise ValueError("n must be positive")
    z = z_for_confidence(confidence)
    normal_hw = z * math.sqrt(p * (1.0 - p) / n)
    return {"normal": normal_hw, "wilson": _wilson_half_width(n, z, p)}


def required_sample_size(
    confidence: float,
    margin: float,
    p: float = 0.5,
    population: Optional[int] = None,
) -> Dict[str, object]:
    """Convenience bundle: both methods + the inputs, for CLI/skill output."""
    return {
        "confidence": confidence,
        "margin": margin,
        "p": p,
        "population": population,
        "z": z_for_confidence(confidence),
        "n_normal": n_normal(confidence, margin, p, population),
        "n_wilson": n_wilson(confidence, margin, p, population),
    }
