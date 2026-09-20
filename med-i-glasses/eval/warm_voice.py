"""Pre-render the phrases the app says verbatim, once.

    cd med-i-glasses && PYTHONPATH=. .venv/bin/python eval/warm_voice.py [--dry-run]

Anything said word-for-word -- status lines, the sample sentence, the
allergy prompts -- never needs synthesizing twice. Rendering them ahead of
time buys three things at once: the good voice, no network at speak time
(a cached phrase survives the WiFi dying, which the live path does not),
and roughly 11 ms instead of 1100 ms to first sound.

Run it after changing the voice or the model: the cache key covers both, so
old audio is simply never read again. Re-running is free -- already-cached
phrases cost no request and no quota.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings
from backend.speech.voice import Voice

# Kept here rather than imported from the client so a phrase change is a
# deliberate act with a visible diff, not a silent cache miss at the demo.
PHRASES = [
    # Connection and status. Verified against the source by
    # backend/tests/test_warm_voice.py -- a phrase that is not said verbatim
    # somewhere is a cache entry nothing will ever hit, and the real wording
    # then falls back to the browser voice without anyone noticing.
    "Watch connected.",
    "Watch connected. Press a button on it.",
    "I lost connection. Reconnecting.",
    "Lost the connection to the watch.",
    "Not connected yet.",
    "Backend not reachable yet. It will keep trying once you start.",
    "Couldn't capture the image to read.",
    "No on-device text reader.",
    # Reading. Spoken at Answer priority, so it takes the network path and
    # is worth having rendered.
    "I couldn't find an allergen statement on this label.",
]

# Deliberately NOT here: every "Careful, ..." allergy line. Those ride the
# hazard channel at Hazard priority, which never calls /speech -- a warning
# must not wait on a request. Rendering them would spend characters on audio
# that is never played. They are also interpolated ("{alert.names}"), so
# there is no fixed string to render in the first place.


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="count characters, synthesize nothing")
    args = parser.parse_args()

    voice = Voice(get_settings())
    if not voice.enabled:
        print("no ELEVENLABS_API_KEY -- nothing to warm, the client will use its own voice")
        return 0

    pending = [p for p in PHRASES if voice.cached(p) is None]
    chars = sum(len(p) for p in pending)
    print(f"{len(PHRASES)} phrases, {len(PHRASES) - len(pending)} already cached, "
          f"{len(pending)} to render ({chars} characters)")
    if args.dry_run:
        return 0

    failed = 0
    for phrase in PHRASES:
        if voice.cached(phrase) is not None:
            print(f"  cached  {phrase[:60]}")
            continue
        if await voice.synthesize(phrase) is None:
            failed += 1
            print(f"  FAILED  {phrase[:60]}")
        else:
            print(f"  ok      {phrase[:60]}")

    if failed:
        print(f"\n{failed} failed -- those fall back to the browser voice, which is not fatal")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
