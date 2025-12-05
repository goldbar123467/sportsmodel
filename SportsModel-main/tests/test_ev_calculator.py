import pathlib
import sys
import math

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.edge.ev_calculator import compute_expected_value
from src.models.distribution import american_to_probability


def test_expected_value_uses_true_probability():
    result = compute_expected_value(true_prob=0.55, odds=-110)
    assert math.isclose(result.expected_value, 0.05, rel_tol=1e-3)
    assert result.market_prob > 0.52


def test_expected_value_break_even_when_true_matches_market():
    market_prob = american_to_probability(-110)
    result = compute_expected_value(true_prob=market_prob, odds=-110)
    assert math.isclose(result.expected_value, 0.0, abs_tol=1e-4)
