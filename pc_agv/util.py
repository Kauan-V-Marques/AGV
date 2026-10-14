"""Funcoes utilitarias sem dependencia de hardware."""

import time


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def now() -> float:
    return time.time()
