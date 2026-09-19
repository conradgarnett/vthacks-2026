"""Base contract for a single assistive sense.

Every sense module (sight, hearing, touch, taste, smell) is a pipeline of three
stages that run at different rates:

    perceive()  raw sensor payload -> observations        (fast, every frame)
    integrate() observations       -> persistent world model
    express()   a user intent      -> prioritized output events

Splitting perception from expression is what lets a safety-critical path skip
the LLM entirely: `hazards` can be derived from the world model and emitted
without ever calling `express()`.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Generic, Iterable, TypeVar


class Priority(IntEnum):
    """Output precedence. Higher always interrupts lower."""

    AMBIENT = 10
    BEACON = 20
    ANSWER = 30
    HAZARD = 40


class Channel(IntEnum):
    """Which output device an event drives."""

    SPEECH = 1
    SPATIAL_AUDIO = 2
    HAPTIC = 3
    VISUAL_DEBUG = 4


@dataclass(slots=True)
class Observation:
    """One thing a sense noticed at one instant, in egocentric coordinates."""

    label: str
    confidence: float
    at: float = field(default_factory=time.monotonic)
    azimuth_deg: float | None = None
    elevation_deg: float | None = None
    distance_m: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OutputEvent:
    """A unit of output bound for the user, tagged for the router."""

    channel: Channel
    priority: Priority
    payload: dict[str, Any]
    interrupts: bool = False
    at: float = field(default_factory=time.monotonic)


RawInput = TypeVar("RawInput")
WorldModel = TypeVar("WorldModel")


class SenseModule(ABC, Generic[RawInput, WorldModel]):
    """One assistive sense.

    Implementations own their world model; the harness only sees this surface.
    `sense_name` namespaces a module's events so a future fusion layer can merge
    streams from several senses without collision.
    """

    sense_name: str

    @abstractmethod
    def perceive(self, raw: RawInput) -> list[Observation]:
        """Convert one raw sensor payload into observations. Must not block."""

    @abstractmethod
    def integrate(self, observations: Iterable[Observation]) -> WorldModel:
        """Fold observations into the persistent model and return it."""

    @abstractmethod
    def hazards(self) -> list[OutputEvent]:
        """Safety-critical events from current state. Never calls an LLM."""

    @abstractmethod
    async def express(self, intent: str) -> Iterable[OutputEvent]:
        """Answer a user intent against the world model."""

    def snapshot(self) -> dict[str, Any]:
        """Token-efficient state for LLM context and the debug dashboard."""
        return {"sense": self.sense_name}
