"""An engine that costs most of a second per call escalates with care.

Measured on this CPU with RapidOCR, a Read of a blank wall made 32
recognitions (27.8 s) and a one-word sign 22 (16.9 s): the 4x4 tile grid
and the nine enhancement variants, each a full read, were built for an
engine at 15 ms a call. Three rules, for costly engines only: a short word
read with confidence is a reading, not a scrap to tile; tiling stops at
2x2; and enhancement is skipped when the detector saw nothing text-like
anywhere. The default engine keeps every escalation it had.
"""

from __future__ import annotations

from backend.ai.ocr import TextLine, TextReader, _needs_enhancement, _needs_tiles
from backend.tests.test_peek import frame


def line(text: str, confidence: float = 0.95) -> TextLine:
    return TextLine(text=text, confidence=confidence, top=0.4, left=0.3, height=0.1)


class FakeEngine(TextReader):
    """Returns the same lines for every call and counts the calls."""

    name = "fake"

    def __init__(self, full: list[TextLine], regions: int) -> None:
        self.full = full
        self.regions = regions
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    def _recognize(self, frame_jpeg: bytes, minimum_height: float) -> list[TextLine]:
        self.calls += 1
        self._note_regions(self.regions)
        return list(self.full)


class CostlyEngine(FakeEngine):
    costly_frames = True
    confidence_informative = True
    reports_regions = True
    tile_grids = (2,)


class TestTheGates:
    def test_a_sure_short_word_is_a_reading_for_a_confident_engine(self):
        engine = CostlyEngine([], 0)
        assert not _needs_tiles([line("EXIT", 0.97)], engine)
        assert _needs_tiles([line("EX", 0.97)], engine), "two characters are still a scrap"
        assert _needs_tiles([line("EXIT", 0.5)], engine), "an unsure short word still escalates"
        assert _needs_tiles([line("EXIT", 0.97)]), "without an engine the old rule stands"
        assert _needs_tiles([line("EXIT", 0.97)], FakeEngine([], 0)), "flat confidence never clears it"

    def test_enhancement_is_skipped_only_when_a_costly_engine_saw_no_region(self):
        blank = CostlyEngine([], 0)
        blank._begin_read()
        assert not _needs_enhancement([], blank)
        something = CostlyEngine([], 3)
        something._begin_read()
        something._note_regions(3)
        assert _needs_enhancement([], something)
        assert _needs_enhancement([]), "no engine: the old rule"
        plain = FakeEngine([], 0)
        plain._begin_read()
        assert _needs_enhancement([], plain), "an engine that cannot report regions keeps enhancing"


class TestWhatAReadCosts:
    def test_a_one_word_sign_costs_a_costly_engine_one_recognition(self):
        engine = CostlyEngine([line("EXIT", 0.97)], 1)
        assert engine.read_sync(frame()) and engine.calls == 1

    def test_a_blank_frame_costs_a_costly_engine_the_full_frame_and_four_tiles(self):
        engine = CostlyEngine([], 0)
        assert engine.read_sync(frame()) == []
        assert engine.calls == 1 + 4, "full frame, the 2x2 grid, no 4x4, no enhancement"

    def test_a_blank_burst_costs_three_frames_and_four_tiles(self):
        engine = CostlyEngine([], 0)
        assert engine.read_consensus_sync([frame(), frame(), frame()]) == []
        # Two empty readings are not an agreement, so every frame is read;
        # tiles once, on the sharpest frame; nothing text-like, so no
        # enhancement. Measured live: 27.8 s and 32 calls before, 5.0 s
        # and 7 calls after.
        assert engine.calls == 3 + 4

    def test_enhancement_still_runs_when_something_text_like_was_seen(self):
        engine = CostlyEngine([], 3)
        engine.read_sync(frame())
        assert engine.calls > 1 + 4, "the variants were read"

    def test_the_default_engine_keeps_every_escalation(self):
        engine = FakeEngine([], 0)
        engine.read_sync(frame())
        assert engine.calls >= 1 + 4 + 16 + 5, "full frame, both grids, the variants"
