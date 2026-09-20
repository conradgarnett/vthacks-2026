"""Pixel space -> egocentric space.

Every distance and direction the user hears comes through here, so this module
is pure, dependency-free, and heavily tested. A bug here is not a cosmetic
bug: it points a blind person at the wrong part of the room.

Convention: azimuth is degrees from the camera's optical axis, negative left,
positive right. Elevation is negative down, positive up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Clock positions only make sense for what's roughly in front of the user.
# Past this, say "behind you" rather than inventing a precise hour.
_MAX_CLOCK_AZIMUTH = 90.0

_STEP_LENGTH_M = 0.75


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Detection box in pixels, origin top-left."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    def iou(self, other: BoundingBox) -> float:
        """Intersection over union. Drives frame-to-frame track association."""
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        intersection = (ix2 - ix1) * (iy2 - iy1)
        union = self.area + other.area - intersection
        return intersection / union if union > 0 else 0.0


def vertical_fov_deg(hfov_deg: float, frame_width: int, frame_height: int) -> float:
    """Derive vertical FOV from horizontal FOV and aspect ratio."""
    if frame_width <= 0 or frame_height <= 0:
        return hfov_deg
    half_h = math.radians(hfov_deg) / 2.0
    half_v = math.atan(math.tan(half_h) * (frame_height / frame_width))
    return math.degrees(half_v) * 2.0


def pixel_to_azimuth(x: float, frame_width: int, hfov_deg: float) -> float:
    """Horizontal pixel -> degrees off-axis.

    Uses the pinhole model rather than a linear ramp: at the edge of a 66-deg
    frame the linear approximation is off by several degrees, which is the
    difference between "2 o'clock" and "3 o'clock".
    """
    if frame_width <= 0:
        return 0.0
    focal_px = (frame_width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    return math.degrees(math.atan((x - frame_width / 2.0) / focal_px))


def pixel_to_elevation(y: float, frame_height: int, vfov_deg: float) -> float:
    """Vertical pixel -> degrees off-axis. Positive is up."""
    if frame_height <= 0:
        return 0.0
    focal_px = (frame_height / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
    return -math.degrees(math.atan((y - frame_height / 2.0) / focal_px))


def clock_position(azimuth_deg: float) -> str:
    """Azimuth -> spoken clock face.

    12 o'clock is straight ahead and hours advance clockwise, so a positive
    (rightward) azimuth maps to 1, 2, 3...
    """
    if abs(azimuth_deg) > _MAX_CLOCK_AZIMUTH:
        return "behind you, to your left" if azimuth_deg < 0 else "behind you, to your right"

    hour = round(azimuth_deg / 30.0)
    if hour == 0:
        return "twelve o'clock"

    names = {
        1: "one", 2: "two", 3: "three", -1: "eleven", -2: "ten", -3: "nine",
    }
    return f"{names[hour]} o'clock"


def describe_direction(azimuth_deg: float) -> str:
    """Coarser than a clock position; used when precision would be false."""
    if abs(azimuth_deg) <= 10:
        return "straight ahead"
    if abs(azimuth_deg) <= 45:
        return "slightly to your left" if azimuth_deg < 0 else "slightly to your right"
    if abs(azimuth_deg) <= 90:
        return "to your left" if azimuth_deg < 0 else "to your right"
    return "behind you"


def region_of(azimuth_deg: float) -> str:
    """Bucket for describe_region() queries."""
    if azimuth_deg < -15:
        return "left"
    if azimuth_deg > 15:
        return "right"
    return "center"


def steps_away(distance_m: float) -> int:
    """Distance in walking steps -- often more actionable than meters."""
    return max(1, round(distance_m / _STEP_LENGTH_M))


def in_forward_cone(azimuth_deg: float, cone_deg: float) -> bool:
    """Is this in the user's walking path?"""
    return abs(azimuth_deg) <= cone_deg / 2.0


def lateral_offset_m(azimuth_deg: float, distance_m: float) -> float:
    """How far off the straight-ahead line, in meters.

    A cone alone over-triggers at distance: 15 deg is 0.26 m away at 1 m but
    1.3 m away at 5 m. Path clearance needs the real offset.
    """
    return abs(math.sin(math.radians(azimuth_deg)) * distance_m)
