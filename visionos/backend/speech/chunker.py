"""Sentence chunking for streamed speech.

Speech must start before the full answer exists, so we emit each sentence the
moment it completes rather than waiting for the model to finish. This is worth
roughly a second of perceived latency on a two-sentence answer.
"""

from __future__ import annotations

import re

_TERMINATORS = ".!?"

# Abbreviations whose trailing period does not end a sentence. Short list on
# purpose: a false split costs a slightly odd pause, a missed split costs
# nothing at all, so over-engineering here has no payoff.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "approx", "vs", "etc",
    "e.g", "i.e", "ft", "in", "m", "cm",
}

_TRAILING_WORD = re.compile(r"([A-Za-z.]+)\.$")


class SentenceChunker:
    """Accumulates streamed text and yields complete utterances."""

    def __init__(self, max_buffer: int = 220) -> None:
        self._buffer = ""
        self._max_buffer = max_buffer

    def feed(self, text: str) -> list[str]:
        """Add streamed text; return any sentences that are now complete."""
        self._buffer += text
        out: list[str] = []

        while True:
            idx = self._next_boundary(self._buffer)
            if idx is None:
                break
            sentence = self._buffer[: idx + 1].strip()
            self._buffer = self._buffer[idx + 1 :].lstrip()
            if sentence:
                out.append(sentence)

        # A long clause with no terminator would otherwise stall speech
        # indefinitely; flush at the last comma or space instead.
        if len(self._buffer) >= self._max_buffer:
            split = max(self._buffer.rfind(", "), self._buffer.rfind(" "))
            if split > 0:
                out.append(self._buffer[:split].strip())
                self._buffer = self._buffer[split:].lstrip()

        return out

    def flush(self) -> str | None:
        """Return whatever remains. Call once the stream ends."""
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder or None

    def _next_boundary(self, text: str) -> int | None:
        for i, ch in enumerate(text):
            if ch not in _TERMINATORS:
                continue
            # Needs a following space (or end of buffer) to count as a boundary,
            # so "3.5 m" and "yolo11n.pt" survive intact.
            if i + 1 < len(text) and not text[i + 1].isspace():
                continue
            if i + 1 >= len(text):
                return None  # may still be mid-token; wait for more input
            if ch == "." and self._is_abbreviation(text[: i + 1]):
                continue
            return i
        return None

    @staticmethod
    def _is_abbreviation(text: str) -> bool:
        match = _TRAILING_WORD.search(text.strip())
        if not match:
            return False
        return match.group(1).rstrip(".").lower() in _ABBREVIATIONS
