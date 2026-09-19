"""Geometry is the one place a bug actively misleads the user.

These assert the properties that matter at the microphone: center is center,
left is negative, the frame edge is exactly half the field of view, and path
clearance accounts for distance.
"""

from __future__ import annotations

import math

import pytest

from backend.perception.geometry import (
    BoundingBox,
    clock_position,
    describe_direction,
    in_forward_cone,
    lateral_offset_m,
    pixel_to_azimuth,
    pixel_to_elevation,
    region_of,
    steps_away,
    vertical_fov_deg,
)

WIDTH, HEIGHT, HFOV = 640, 480, 66.0


class TestAzimuth:
    def test_frame_center_is_dead_ahead(self):
        assert pixel_to_azimuth(WIDTH / 2, WIDTH, HFOV) == pytest.approx(0.0)

    def test_left_is_negative_right_is_positive(self):
        assert pixel_to_azimuth(0, WIDTH, HFOV) < 0
        assert pixel_to_azimuth(WIDTH, WIDTH, HFOV) > 0

    def test_frame_edges_are_exactly_half_the_fov(self):
        """Pinhole model must be exact at the edges, not approximate."""
        assert pixel_to_azimuth(WIDTH, WIDTH, HFOV) == pytest.approx(HFOV / 2)
        assert pixel_to_azimuth(0, WIDTH, HFOV) == pytest.approx(-HFOV / 2)

    def test_is_nonlinear_unlike_a_naive_linear_ramp(self):
        """The reason we don't just scale pixels linearly.

        atan is concave, so half the pixel offset yields MORE than half the
        edge angle: pixels near the center subtend more angle than pixels near
        the edge. A linear ramp would report 16.5 deg here; the truth is ~18.
        """
        quarter = pixel_to_azimuth(WIDTH * 0.75, WIDTH, HFOV)
        edge = pixel_to_azimuth(WIDTH, WIDTH, HFOV)
        linear_guess = HFOV / 4

        assert quarter > edge / 2, "expected angular compression toward the edge"
        assert quarter == pytest.approx(17.99, abs=0.01)
        assert quarter > linear_guess, "linear approximation under-reports"

    def test_degenerate_width_does_not_raise(self):
        assert pixel_to_azimuth(10, 0, HFOV) == 0.0


class TestElevation:
    def test_center_is_level(self):
        vfov = vertical_fov_deg(HFOV, WIDTH, HEIGHT)
        assert pixel_to_elevation(HEIGHT / 2, HEIGHT, vfov) == pytest.approx(0.0)

    def test_top_of_frame_is_up(self):
        """Screen y grows downward; elevation must not inherit that."""
        vfov = vertical_fov_deg(HFOV, WIDTH, HEIGHT)
        assert pixel_to_elevation(0, HEIGHT, vfov) > 0
        assert pixel_to_elevation(HEIGHT, HEIGHT, vfov) < 0

    def test_vertical_fov_is_smaller_than_horizontal_on_landscape(self):
        assert vertical_fov_deg(HFOV, WIDTH, HEIGHT) < HFOV


class TestClockPosition:
    @pytest.mark.parametrize(
        "azimuth,expected",
        [
            (0, "twelve o'clock"),
            (5, "twelve o'clock"),
            (30, "one o'clock"),
            (60, "two o'clock"),
            (90, "three o'clock"),
            (-30, "eleven o'clock"),
            (-60, "ten o'clock"),
            (-90, "nine o'clock"),
        ],
    )
    def test_maps_azimuth_to_spoken_hour(self, azimuth, expected):
        assert clock_position(azimuth) == expected

    def test_hours_advance_clockwise(self):
        """Right of center must read as 1 o'clock, never 11."""
        assert "one" in clock_position(30)
        assert "eleven" in clock_position(-30)

    def test_beyond_the_frame_refuses_to_invent_an_hour(self):
        assert "behind you" in clock_position(150)
        assert "behind you" in clock_position(-150)


class TestDirectionAndRegion:
    @pytest.mark.parametrize(
        "azimuth,expected",
        [(0, "straight ahead"), (30, "slightly to your right"),
         (-30, "slightly to your left"), (70, "to your right"), (120, "behind you")],
    )
    def test_coarse_direction(self, azimuth, expected):
        assert describe_direction(azimuth) == expected

    @pytest.mark.parametrize(
        "azimuth,region", [(0, "center"), (-40, "left"), (40, "right")]
    )
    def test_region_buckets(self, azimuth, region):
        assert region_of(azimuth) == region


class TestPathClearance:
    def test_forward_cone_membership(self):
        assert in_forward_cone(0, 30)
        assert in_forward_cone(-15, 30)
        assert not in_forward_cone(20, 30)

    def test_lateral_offset_grows_with_distance(self):
        """A fixed cone over-triggers far away; offset is the honest measure."""
        near = lateral_offset_m(15, 1.0)
        far = lateral_offset_m(15, 5.0)
        assert far > near
        assert far == pytest.approx(math.sin(math.radians(15)) * 5.0)

    def test_dead_ahead_has_no_lateral_offset(self):
        assert lateral_offset_m(0, 3.0) == pytest.approx(0.0)

    def test_offset_is_unsigned(self):
        assert lateral_offset_m(-20, 2.0) == pytest.approx(lateral_offset_m(20, 2.0))


class TestSteps:
    def test_converts_meters_to_walkable_steps(self):
        assert steps_away(0.75) == 1
        assert steps_away(3.0) == 4

    def test_never_reports_zero_steps(self):
        """'Zero steps ahead' is meaningless to act on."""
        assert steps_away(0.05) == 1


class TestBoundingBox:
    def test_identical_boxes_have_full_overlap(self):
        box = BoundingBox(0, 0, 10, 10)
        assert box.iou(box) == pytest.approx(1.0)

    def test_disjoint_boxes_have_no_overlap(self):
        assert BoundingBox(0, 0, 5, 5).iou(BoundingBox(10, 10, 15, 15)) == 0.0

    def test_touching_edges_do_not_count_as_overlap(self):
        assert BoundingBox(0, 0, 5, 5).iou(BoundingBox(5, 0, 10, 5)) == 0.0

    def test_half_overlap(self):
        a, b = BoundingBox(0, 0, 10, 10), BoundingBox(5, 0, 15, 10)
        assert a.iou(b) == pytest.approx(50 / 150)

    def test_center_and_area(self):
        box = BoundingBox(10, 20, 30, 60)
        assert box.center == (20.0, 40.0)
        assert box.area == pytest.approx(800.0)

    def test_inverted_box_has_no_negative_area(self):
        assert BoundingBox(10, 10, 5, 5).area == 0.0
