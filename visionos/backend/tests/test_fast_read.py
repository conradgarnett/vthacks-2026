"""Fast first, thorough when unsure.

Apple Vision reads a frame in 15 ms; RapidOCR takes most of a second on a
CPU and reads what Vision cannot. A read starts on the fast path and only
escalates when the reader is less than 80% sure, the same bar every other
guess in this app has to clear. The exception is a medical label whose dose
the guard would withhold: only agreement across a burst can release a dose,
so it always gets the burst.
"""

from __future__ import annotations

import time

import pytest

from backend.ai import ocr as ocr_module
from backend.ai.ocr import (
    FAST_READ_CONFIDENCE,
    TextLine,
    TextReader,
    TieredReader,
    build_reader,
    reading_confidence,
)
from backend.tests.test_peek import FakePerception, FakeProvider, FakeSocket, frame
from backend.main import Session


def line(text: str, confidence: float = 0.95, top: float = 0.3) -> TextLine:
    return TextLine(text=text, confidence=confidence, top=top, left=0.1, height=0.05)


class Engine(TextReader):
    """A stand-in engine with a scripted answer and a call count."""

    name = "engine"
    confidence_informative = True

    def __init__(self, lines: list[TextLine], name: str = "engine", is_available: bool = True) -> None:
        self.lines = lines
        self.name = name
        self.calls = 0
        self.is_available = is_available

    @property
    def available(self) -> bool:
        return self.is_available

    def read_sync(self, frame_jpeg: bytes) -> list[TextLine]:
        self.calls += 1
        return list(self.lines)

    def read_consensus_sync(self, frames: list[bytes]) -> list[TextLine]:
        self.calls += 1
        return list(self.lines)

    def read_quick_sync(self, frame_jpeg: bytes) -> list[TextLine]:
        self.calls += 1
        return list(self.lines)

    async def read_quick(self, frame_jpeg: bytes) -> list[TextLine]:
        return self.read_quick_sync(frame_jpeg)

    async def read_consensus(self, frames: list[bytes]) -> list[TextLine]:
        return self.read_consensus_sync(frames)


class TestReadingConfidence:
    def test_uses_the_engine_score_when_it_means_something(self):
        reader = Engine([])
        assert reading_confidence(reader, [line("EXIT", 0.9), line("WAY OUT", 0.6)]) == pytest.approx(
            (0.9 * 4 + 0.6 * 7) / 11
        )

    def test_uses_known_words_when_the_engine_score_is_flat(self):
        """Vision's flat 0.5 must not decide anything: words the lexicon
        knows are sure, garble is not, and an unknown drug name is not."""
        reader = Engine([])
        reader.confidence_informative = False
        assert reading_confidence(reader, [line("FIRE EXIT", 0.5)]) == 1.0
        assert reading_confidence(reader, [line("DLpartiirL", 0.5)]) == 0.0
        assert reading_confidence(reader, [line("LISINOPRIL 10 MG", 0.5)]) < FAST_READ_CONFIDENCE

    def test_nothing_read_is_no_confidence(self):
        assert reading_confidence(Engine([]), []) == 0.0


class TestTieredReader:
    def test_a_confident_fast_read_is_final(self):
        fast = Engine([line("FIRE EXIT", 0.95)], "fast")
        slow = Engine([line("FIRE EXIT", 0.95)], "slow")
        reader = TieredReader(fast, slow)
        assert [l.text for l in reader.read_consensus_sync([b"f"])] == ["FIRE EXIT"]
        assert (fast.calls, slow.calls) == (1, 0)

    def test_an_unsure_fast_read_escalates(self):
        fast = Engine([line("F1RE EX1T", FAST_READ_CONFIDENCE - 0.1)], "fast")
        slow = Engine([line("FIRE EXIT", 0.95)], "slow")
        reader = TieredReader(fast, slow)
        assert [l.text for l in reader.read_consensus_sync([b"f"])] == ["FIRE EXIT"]
        assert (fast.calls, slow.calls) == (1, 1)

    def test_an_empty_fast_read_escalates(self):
        fast, slow = Engine([], "fast"), Engine([line("Reception")], "slow")
        assert [l.text for l in TieredReader(fast, slow).read_sync(b"f")] == ["Reception"]

    def test_a_scrap_escalates_even_when_confident(self):
        fast, slow = Engine([line("il", 0.99)], "fast"), Engine([line("EXIT")], "slow")
        assert [l.text for l in TieredReader(fast, slow).read_sync(b"f")] == ["EXIT"]

    def test_the_mesh_keeps_sure_words_and_fills_in_the_rest(self):
        """The fast engine reads the words it can check and skips the rest;
        the thorough engine supplies the rest, and where both read the same
        place the more plausible reading wins."""
        fast = Engine([line("FIRE EXIT", 0.95, top=0.2), line("Ilcccpcion", 0.4, top=0.5)], "fast")
        slow = Engine([line("Reception", 0.9, top=0.5)], "slow")
        texts = sorted(l.text for l in TieredReader(fast, slow).read_consensus_sync([b"f"]))
        assert texts == ["FIRE EXIT", "Reception"]

    def test_one_unsure_word_is_enough_to_look_harder(self):
        fast = Engine([line("FIRE EXIT", 0.95, top=0.2), line("Ilcccpcion", 0.4, top=0.5)], "fast")
        slow = Engine([line("Reception", 0.9, top=0.5)], "slow")
        TieredReader(fast, slow).read_sync(b"f")
        assert slow.calls == 1

    def test_an_unsure_fast_word_the_thorough_engine_also_read_is_replaced(self):
        fast = Engine([line("F1RE EX1T", 0.6, top=0.2)], "fast")
        slow = Engine([line("FIRE EXIT", 0.9, top=0.2)], "slow")
        assert [l.text for l in TieredReader(fast, slow).read_sync(b"f")] == ["FIRE EXIT"]

    def test_the_thorough_reader_finding_nothing_keeps_the_fast_reading(self):
        fast, slow = Engine([line("F1RE", 0.5)], "fast"), Engine([], "slow")
        assert [l.text for l in TieredReader(fast, slow).read_sync(b"f")] == ["F1RE"]

    def test_the_name_says_both(self):
        assert TieredReader(Engine([], "apple-vision"), Engine([], "rapidocr")).name == "apple-vision+rapidocr"


class TestBuildReader:
    def test_two_engines_become_a_tiered_reader(self, monkeypatch):
        monkeypatch.setattr(
            ocr_module, "_ENGINES",
            {"fast": lambda: Engine([], "fast"), "slow": lambda: Engine([], "slow")},
        )
        reader = build_reader()
        assert isinstance(reader, TieredReader)
        assert reader.name == "fast+slow"

    def test_one_engine_stays_plain(self, monkeypatch):
        monkeypatch.setattr(
            ocr_module, "_ENGINES",
            {"fast": lambda: Engine([], "fast", is_available=False), "slow": lambda: Engine([], "slow")},
        )
        assert build_reader().name == "slow"

    def test_pinning_an_engine_skips_the_tier(self, monkeypatch):
        monkeypatch.setattr(
            ocr_module, "_ENGINES",
            {"fast": lambda: Engine([], "fast"), "slow": lambda: Engine([], "slow")},
        )
        assert build_reader("slow").name == "slow"


def make_session(engine: Engine) -> tuple[Session, FakeSocket]:
    socket = FakeSocket()
    return Session(socket, FakeProvider(), FakePerception(), engine), socket


class TestFastTierInASession:
    @pytest.mark.asyncio
    async def test_a_sure_quick_pass_is_spoken_without_the_burst(self):
        engine = Engine([line("FIRE EXIT", 0.95)])
        session, socket = make_session(engine)
        await session.handle_read([frame(), frame(), frame()])
        assert engine.calls == 1, "the burst ran although the quick pass was sure"
        assert socket.spoken() == ["It reads: FIRE EXIT."]

    @pytest.mark.asyncio
    async def test_an_unsure_quick_pass_gets_the_burst(self):
        engine = Engine([line("F1RE EX1T", 0.6)])
        session, _ = make_session(engine)
        await session.handle_read([frame(), frame(), frame()])
        assert engine.calls == 2

    @pytest.mark.asyncio
    async def test_a_withheld_dose_always_gets_the_burst(self):
        """One frame can never corroborate a dose, so a medical label whose
        dose the guard withholds is not settled by the fast tier."""
        engine = Engine([line("TAKE 2 TABLETS BY MOUTH DAILY", 0.97), line("AMOXICILLIN 500 MG", 0.96)])
        session, _ = make_session(engine)
        await session.handle_read([frame(), frame(), frame()])
        assert engine.calls == 2

    @pytest.mark.asyncio
    async def test_fresh_peeks_join_the_quick_pass(self):
        engine = Engine([line("FIRE EXIT", 0.95)])
        session, socket = make_session(engine)
        session.peeks.append((time.monotonic() - 1.0, [line("FIRE EXIT", 0.9)]))
        await session.handle_read([frame()])
        assert socket.spoken() == ["It reads: FIRE EXIT."]
