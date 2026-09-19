"""Per-stage latency tracking.

The demo lives or dies on two numbers: frame -> first spoken word (< 2.5 s) and
hazard -> alert (< 200 ms). Both are sums over stages, so we time every stage
individually -- an aggregate number tells you that you missed, not where.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Iterator

_trace_ids = count(1)
# Bounded: this runs for hours during a hackathon and must not grow forever.
_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))


@dataclass(slots=True)
class LatencyTrace:
    """Stage timings for one request, in milliseconds."""

    label: str
    trace_id: int = field(default_factory=lambda: next(_trace_ids))
    started: float = field(default_factory=time.perf_counter)
    stages: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        begin = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - begin) * 1000.0)

    def record(self, name: str, elapsed_ms: float) -> None:
        self.stages[name] = round(elapsed_ms, 1)
        _history[f"{self.label}.{name}"].append(elapsed_ms)

    def mark(self, name: str) -> float:
        """Record elapsed time since trace start. For 'time to first X'."""
        elapsed = (time.perf_counter() - self.started) * 1000.0
        self.record(name, elapsed)
        return elapsed

    @property
    def total_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000.0, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "label": self.label,
            "stages": self.stages,
            "total_ms": self.total_ms,
        }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct), len(ordered) - 1)
    return round(ordered[idx], 1)


def metrics_snapshot() -> dict[str, Any]:
    """Rolling percentiles for the judge dashboard."""
    out: dict[str, Any] = {}
    for key, samples in _history.items():
        values = list(samples)
        if values:
            out[key] = {
                "n": len(values),
                "p50": _percentile(values, 0.50),
                "p95": _percentile(values, 0.95),
                "last": round(values[-1], 1),
            }
    return out


def reset_metrics() -> None:
    _history.clear()
