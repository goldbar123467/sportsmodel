from __future__ import annotations

from typing import List


def kelly_fraction(win_prob: float, odds: int, fraction: float = 1.0) -> float:
    if not 0 <= win_prob <= 1:
        raise ValueError("win_prob must be between 0 and 1")
    if odds == 0:
        raise ValueError("odds cannot be zero")

    b = (odds / 100) if odds > 0 else (100 / -odds)
    q = 1 - win_prob
    f_star = (win_prob * b - q) / b
    return max(0.0, f_star * fraction)


__all__: List[str] = ["kelly_fraction"]
