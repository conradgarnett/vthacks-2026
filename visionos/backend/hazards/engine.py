"""Safety-critical alerts. No LLM anywhere in this path.

Everything here is deterministic and runs off the tracked scene model, so an
obstacle warning costs microseconds and cannot be delayed by a slow API call,
a rate limit, or a dead network. Measured budget is 200 ms from frame to
alert; detection alone accounts for most of it and this stage is negligible.

Debouncing is not a nicety. A tool that repeats "obstacle ahead" four times a
second is one the user switches off, and a switched-off tool protects nobody.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum

from backend.perception.geometry import clock_position, steps_away
from backend.scene.model import SceneModel
from backend.scene.queries import objects_in_cone


class Severity(IntEnum):
    WARNING = 1  # worth knowing
    URGENT = 2  # act now


@dataclass(slots=True)
class HazardAlert:
    text: str
    severity: Severity
    azimuth_deg: float
    distance_m: float
    key: str

    def to_dict(self) -> dict:
        return {
            "type": "hazard",
            "text": self.text,
            "severity": int(self.severity),
            "azimuth_deg": round(self.azimuth_deg, 1),
            "distance_m": round(self.distance_m, 2),
        }


# Inside this range an obstacle is about two steps away: state it plainly.
_URGENT_DISTANCE_M = 1.2
# A drop-off is the one hazard worth interrupting for even when uncertain.
_DROPOFF_SEVERITY_FLOOR = 0.6


@dataclass
class HazardEngine:
    distance_m: float = 1.5
    cone_deg: float = 30.0
    cooldown_s: float = 3.0

    _last_spoken: dict[str, float] = field(default_factory=dict)

    def evaluate(self, scene: SceneModel, dropoffs: list | None = None) -> list[HazardAlert]:
        """Current hazards, already debounced. Safe to call every frame."""
        now = time.monotonic()
        alerts: list[HazardAlert] = []

        for hint in dropoffs or []:
            if getattr(hint, "severity", 0) < _DROPOFF_SEVERITY_FLOOR:
                continue
            alerts.append(
                HazardAlert(
                    # Hedged on purpose: this is a depth discontinuity, not a
                    # verified staircase, and must never be spoken as one.
                    text="Careful, the floor may drop away ahead.",
                    severity=Severity.URGENT,
                    azimuth_deg=hint.azimuth_deg,
                    distance_m=0.0,
                    key="dropoff",
                )
            )
            break  # one floor warning is enough; more is noise

        for obj in objects_in_cone(scene, self.cone_deg, self.distance_m):
            if obj.distance_m is None:
                continue
            urgent = obj.distance_m <= _URGENT_DISTANCE_M
            steps = steps_away(obj.distance_m)
            alerts.append(
                HazardAlert(
                    text=(
                        f"{obj.label.capitalize()} ahead, {steps} step"
                        f"{'' if steps == 1 else 's'}."
                        if urgent
                        else f"{obj.label.capitalize()} at your "
                        f"{clock_position(obj.azimuth_deg)}, about "
                        f"{obj.distance_m:.1f} meters."
                    ),
                    severity=Severity.URGENT if urgent else Severity.WARNING,
                    azimuth_deg=obj.azimuth_deg,
                    distance_m=obj.distance_m,
                    # Keyed by object identity, so the same chair re-warns only
                    # after the cooldown while a new one warns immediately.
                    key=f"{obj.label}:{obj.object_id}",
                )
            )

        return self._debounce(alerts, now)

    def _debounce(self, alerts: list[HazardAlert], now: float) -> list[HazardAlert]:
        fresh: list[HazardAlert] = []
        for alert in sorted(alerts, key=lambda a: (-a.severity, a.distance_m)):
            if now - self._last_spoken.get(alert.key, -1e9) < self.cooldown_s:
                continue
            self._last_spoken[alert.key] = now
            fresh.append(alert)

        # Only the most pressing hazard is spoken per frame. Queueing several
        # means the user hears a stale one after the moment has passed.
        return fresh[:1]

    def reset(self) -> None:
        self._last_spoken.clear()
