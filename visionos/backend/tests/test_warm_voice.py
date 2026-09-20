"""The pre-rendered phrase list must match what the app actually says.

A phrase in PHRASES that no longer appears in the source is worse than
useless: it spends characters rendering audio nothing will ever request,
and the wording that IS spoken quietly falls back to the browser voice.
Nothing fails, nothing logs, the demo just sounds worse in the places
someone took care over.

This caught eight of seventeen on the first run -- phrases written from a
description of the app rather than from the app.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from warm_voice import PHRASES  # noqa: E402

# Where a spoken string can legitimately live.
SEARCHED = [ROOT / "client" / "src", ROOT / "backend"]
SUFFIXES = {".ts", ".py", ".html"}


def _corpus() -> str:
    parts = []
    for root in SEARCHED:
        for path in root.rglob("*"):
            if path.suffix in SUFFIXES and "__pycache__" not in path.parts:
                # This file quotes the phrases itself; it would vouch for them.
                if path.name == "test_warm_voice.py":
                    continue
                parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def corpus() -> str:
    return _corpus()


@pytest.mark.parametrize("phrase", PHRASES)
def test_phrase_is_actually_said(phrase: str, corpus: str) -> None:
    assert phrase in corpus, (
        f"{phrase!r} is pre-rendered but appears nowhere in the client or "
        "backend. Either the wording changed (update PHRASES and re-run "
        "eval/warm_voice.py) or it was never said."
    )


def test_no_duplicates() -> None:
    assert len(PHRASES) == len(set(PHRASES)), "a repeated phrase renders twice"


def test_no_interpolated_phrases() -> None:
    """A template is not a fixed phrase and can never be a cache hit."""
    for phrase in PHRASES:
        assert "{" not in phrase and "}" not in phrase, (
            f"{phrase!r} looks interpolated; only fixed strings can be cached"
        )
