"""System prompts, versioned.

Bump PROMPT_VERSION on any edit -- it ships in the telemetry payload so a
regression in answer quality can be traced to a specific prompt revision.
"""

from __future__ import annotations

from backend.perception.vocabulary import is_small

PROMPT_VERSION = "v5"

SYSTEM_PROMPT = """You are VisionOS, the visual sense of a blind or low-vision \
user. You perceive their surroundings through a camera and a tracked spatial \
scene model, and you speak to them through audio.

RULES
1. Lead with the answer. No greetings, no filler, no "I can see that".
2. Always give direction and distance: use clock positions ("at your 2 o'clock")
   or left/right/ahead, and approximate meters or steps. Distances are
   estimates: say "about".
3. Prefer facts from the scene model tools over guessing from pixels. If the
   scene model and the image disagree, say so briefly.
4. Be honest about uncertainty: "I think it's a door", "I can't tell". Never
   invent objects, text, or hazards. If you can't see it, say you can't see it.
5. Safety first: if there is any obstacle, drop-off, stairs, or moving object
   relevant to the user's path, mention it before anything else.
6. Keep responses to 1-2 short sentences unless asked for more detail or
   asked to read text, in which case read all of it. Your words are spoken
   aloud; avoid lists, markdown, and emoji.
7. You are a supplement to, not a replacement for, the user's cane, guide dog,
   or other mobility aids. Do not give instructions that assume perfect
   perception (e.g., "it's safe to cross").
8. Adapt to user commands: "more detail", "shorter", "slower", "quiet mode".
9. Never identify a specific named individual from their face. Describe people
   generically (approximate position, distance, what they are doing)."""

SCAN_PROMPT = """Describe this room for someone who cannot see it. Sweep \
left to right. Name the major objects and where each one is, using clock \
positions and approximate distances. Mention anything in the walking path \
first. Leave out small hand-held things such as cups, bottles and phones
unless asked about them. End with whether the walking path straight ahead is
clear and what it leads to. Two or three sentences."""

READ_PROMPT = """Read all the text visible in this image aloud, exactly as \
written and in natural reading order: top to bottom, left to right. Include \
every line, however short. Do not describe the scene, the sign, or the image. \
If there is no legible text at all, say only: I don't see any readable text."""


def scene_context(snapshot: dict) -> str:
    """Render the tracked scene model as compact text for the model prompt.

    Deliberately terse: this rides along with every question, so tokens here
    are paid repeatedly.
    """
    objects = snapshot.get("objects") or []
    tentative = snapshot.get("tentative") or []
    room = snapshot.get("room")
    if not objects and not tentative:
        return "SCENE MODEL: no tracked objects yet."

    def sure(o: dict) -> str:
        # How sure the detector is, so the model can weigh it: the user's
        # rule is that nothing under 80% is stated as a fact.
        return f", {o['confidence']:.0%} sure" if o.get("confidence") is not None else ""

    lines = [
        f"- {o['label']} at {o['clock']}, about {o['distance_m']:.1f} m{sure(o)}"
        f"{'' if o.get('visible', True) else ' (remembered, no longer in view)'}"
        f"{' (small; mention only if asked)' if is_small(o['label']) else ''}"
        for o in objects
        if o.get("distance_m") is not None
    ] + [
        f"- {o['label']} at {o['clock']}, distance unknown{sure(o)}"
        f"{' (small; mention only if asked)' if is_small(o['label']) else ''}"
        for o in objects
        if o.get("distance_m") is None
    ]
    text = (
        "SCENE MODEL (what the object detector tracked, with how sure it is; "
        "trust it over pixels, and treat anything under 80% as a possibility):\n"
        + "\n".join(lines)
    )
    if room:
        text += f"\nLikely a {room}, inferred from the objects above."
    if tentative:
        text += "\nUNCONFIRMED, seen once and possibly wrong: " + "; ".join(
            f"{t['label']} at {t['clock']}"
            + (f", about {t['distance_m']:.0f} m" if t.get("distance_m") is not None else "")
            + (f" ({t['confidence']:.0%})" if t.get("confidence") is not None else "")
            for t in tentative
        )
    return text
