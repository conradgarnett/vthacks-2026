"""The allergy scanner for one session: what it watches, what it decides,
what it says, and when it emails.

The evidence ladder, agreed with the other agent on PR #2 (2026-09-20),
strongest first:

  1. A barcode on the packet, looked up in Open Food Facts. A database
     cannot invent an allergen: a product whose `allergens` meet the
     profile is an alert and an email. Its `traces` are a warning, spoken,
     never emailed.
  2. A CONTAINS / ALLERGENS line the reader was sure of (80% or better) in
     two frames or more, holding one of the profile's allergen words, whole
     and not negated: an alert and an email. "May contain" and "traces of"
     lines are a warning, spoken, never emailed.
  3. The label's text judged by the online model (reasoner.py), which
     knows that whey is milk and semolina is wheat: "confirm" is spoken
     firmly, "deny" is spoken hedged when the wearer asked, "check" is
     "I can't tell what is in this; check the label before eating". None of
     the three emails.
  4. A food the detector recognized is a trigger only: "it looks like you
     are eating; hold the label up and press Read". The five foods that are
     the allergen itself (peanuts, peanut butter, nuts, shrimp, a whole
     egg) earn a hedged warning. Neither emails.

One email per allergen per ten minutes; a "false alarm" from the wearer
sends a correction to the same address. Everything spoken goes through
the client's hazard channel (a tone and a vibration before the words) or
plain speech, and nothing here can block the socket: the email leaves on
a worker thread after the wearer has already been told.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from backend.alerts.allergy import (
    find_allergen_mentions,
    looks_like_food_label,
    obvious_allergens,
    spoken_name,
)
from backend.alerts.barcode import ProductFacts, ProductLookup, decode_barcodes
from backend.alerts.eating import EatingDetector
from backend.alerts.email_agent import Delivery, EmailAgent
from backend.alerts.profile import Profile
from backend.alerts.reasoner import LabelReasoner, Verdict

log = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.8
MIN_AGREEMENT = 2
MIN_FRAMES = 3
COOLDOWN_S = 600.0
PROMPT_COOLDOWN_S = 120.0
CHECK_COOLDOWN_S = 30.0
JUDGED_S = 120.0

CHECK_BEFORE_EATING = "I can't tell what is in this from the label. Check it before eating."
NO_STATEMENT = "I couldn't find an allergen statement on this label. Check it before eating."
EATING_PROMPT = (
    "It looks like you are eating. If there is a label, hold it up and press Read, "
    "and I'll check it against your allergies."
)


def _names(allergens: Iterable[str]) -> str:
    spoken = [spoken_name(a) for a in allergens]
    if not spoken:
        return ""
    if len(spoken) == 1:
        return spoken[0]
    return ", ".join(spoken[:-1]) + " and " + spoken[-1]


def when_text(at: float) -> str:
    """'1:52 AM on Saturday 20 September 2026 (EDT)'."""
    local = time.localtime(at)
    return f"{time.strftime('%I:%M %p on %A %d %B %Y', local).lstrip('0')} ({time.strftime('%Z', local)})"


@dataclass
class Alert:
    """Authoritative evidence that what is in front of the wearer holds one
    of their allergens. The thing that emails."""

    allergens: tuple[str, ...]
    source: str  # "barcode" | "label"
    evidence: str
    confidence: float
    at: float = field(default_factory=time.time)
    place: str | None = None
    product: str = ""
    emailed: bool = False
    delivery: Delivery | None = None

    @property
    def names(self) -> str:
        return _names(self.allergens)

    def to_event(self, text: str) -> dict:
        return {
            "type": "hazard",
            "kind": "allergy",
            "severity": 2,
            "text": text,
            "azimuth_deg": 0.0,
            "distance_m": 0.5,
            "allergens": list(self.allergens),
            "source": self.source,
            "evidence": self.evidence,
            "emailed": self.emailed,
        }


@dataclass(frozen=True)
class Notice:
    """Something to say that is not an alert: a warning that does not email,
    a prompt to read the label, a verdict on it."""

    text: str
    kind: str  # "warning" | "prompt" | "check" | "clear"
    allergens: tuple[str, ...] = ()

    @property
    def tone(self) -> bool:
        return self.kind == "warning"

    def to_event(self) -> dict:
        if self.tone:
            return {
                "type": "hazard",
                "kind": "allergy-warning",
                "severity": 1,
                "text": self.text,
                "azimuth_deg": 0.0,
                "distance_m": 0.5,
                "allergens": list(self.allergens),
                "emailed": False,
            }
        return {"type": "speech", "text": self.text}


@dataclass
class Outcome:
    alerts: list[Alert] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)
    barcode: str | None = None  # the code read from the frame, if any
    product: ProductFacts | None = None  # what the database said of it
    verdict: Verdict | None = None

    @property
    def spoke(self) -> bool:
        return bool(self.alerts or self.notices)


class AllergyWatch:
    def __init__(
        self,
        *,
        profile: Callable[[], Profile],
        mailer: EmailAgent | None = None,
        lookup: ProductLookup | None = None,
        reasoner: LabelReasoner | None = None,
        place: Callable[[], str | None] | None = None,
        enabled: bool = True,
        min_confidence: float = MIN_CONFIDENCE,
        min_agreement: int = MIN_AGREEMENT,
        min_frames: int = MIN_FRAMES,
        cooldown_s: float = COOLDOWN_S,
        prompt_cooldown_s: float = PROMPT_COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._profile = profile
        self.mailer = mailer
        self.lookup = lookup
        self.reasoner = reasoner
        self._place = place
        self.enabled = enabled
        self.min_confidence = min_confidence
        self.min_agreement = min_agreement
        self.cooldown_s = cooldown_s
        self.prompt_cooldown_s = prompt_cooldown_s
        self._clock = clock
        self.eating = EatingDetector(min_frames=min_frames, clock=clock)
        self._last_alert: dict[str, float] = {}
        self._last_notice: dict[str, float] = {}
        # The model's verdict on a label's text, kept for a while: the
        # peeks read the same packet every few seconds and one opinion on
        # it is enough.
        self._judged: dict[str, tuple[float, Verdict]] = {}
        self.alerts: list[Alert] = []

    # -- state ---------------------------------------------------------------

    @property
    def profile(self) -> Profile:
        return self._profile()

    @property
    def active(self) -> bool:
        return self.enabled and self.profile.has_allergens

    @property
    def last_alert(self) -> Alert | None:
        return self.alerts[-1] if self.alerts else None

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else now

    def _cool(self, table: dict[str, float], key: str, cooldown: float, now: float) -> bool:
        """True, and remembers the moment, when the key is out of its cooldown."""
        if now - table.get(key, -1e9) < cooldown:
            return False
        table[key] = now
        return True

    def _notice(self, text: str, kind: str, allergens: tuple[str, ...], now: float,
                cooldown: float | None = None) -> Notice | None:
        key = f"{kind}:{','.join(allergens)}" if allergens else kind
        cooldown = self.prompt_cooldown_s if cooldown is None else cooldown
        if not self._cool(self._last_notice, key, cooldown, now):
            return None
        return Notice(text, kind, allergens)

    def _alert(self, allergens: Iterable[str], source: str, evidence: str, confidence: float,
               now: float, product: str = "") -> Alert | None:
        fresh = tuple(a for a in dict.fromkeys(allergens) if self._cool(self._last_alert, a, self.cooldown_s, now))
        if not fresh:
            return None
        place = None
        if self._place is not None:
            try:
                place = self._place()
            except Exception:  # noqa: BLE001 - the place is decoration
                place = None
        alert = Alert(fresh, source, evidence, confidence, place=place, product=product)
        self.alerts.append(alert)
        del self.alerts[:-8]
        log.warning("ALLERGY ALERT (%s): %s; %s", source, alert.names, evidence)
        return alert

    # -- sight: a trigger ----------------------------------------------------

    def observe_frame(self, detections: Iterable, frame_size: tuple[int, int] | None,
                      now: float | None = None) -> Notice | None:
        """A live frame's detections. Returns at most one thing to say."""
        now = self._now(now)
        self.eating.observe(detections, frame_size, now)
        if not self.active:
            return None
        eating = self.eating.eating(now)
        if not eating.confirmed:
            return None
        allergens = self.profile.allergens
        for food in eating.foods:
            groups = tuple(g for g in obvious_allergens(food.label) if g in allergens)
            if not groups:
                continue
            return self._notice(
                f"Careful. It looks like {food.label}, and you are allergic to {_names(groups)}. "
                "I can't confirm that from the picture; check the label.",
                "warning", groups, now,
            )
        return self._notice(EATING_PROMPT, "prompt", (), now)

    # -- text and barcode: evidence -----------------------------------------

    async def _product(self, frame_jpeg: bytes | None) -> tuple[str | None, ProductFacts | None]:
        """The first barcode in the frame and what the database says of it:
        (None, None) with no barcode, (code, None) when the database could
        not be reached, (code, facts) otherwise, found or not."""
        if self.lookup is None or not frame_jpeg:
            return None, None
        try:
            codes = await asyncio.to_thread(decode_barcodes, frame_jpeg)
        except Exception:  # noqa: BLE001 - a decoder failure is no barcode
            log.exception("barcode decoding failed")
            return None, None
        first: str | None = None
        for code in codes[:2]:
            first = first or code
            facts = await self.lookup.facts(code)
            if facts is not None:
                return code, facts
        return first, None

    async def _judge(self, lines: list, wanted: list[str], now: float,
                     frame_jpeg: bytes | None = None) -> Verdict:
        """The model's verdict on this label, asked once per label per while."""
        from backend.alerts.reasoner import label_text

        key = " ".join(label_text(lines).lower().split())
        cached = self._judged.get(key)
        if cached is not None and now - cached[0] <= JUDGED_S and cached[1].from_model:
            return cached[1]
        verdict = await self.reasoner.judge(lines, wanted, frame_jpeg)
        self._judged[key] = (now, verdict)
        while len(self._judged) > 32:
            del self._judged[next(iter(self._judged))]
        return verdict

    def _line_confidence(self, reader, line) -> float:
        if reader is None:
            return float(getattr(line, "confidence", 0.0) or 0.0)
        from backend.ai.ocr import line_confidence

        return line_confidence(reader, line)

    async def check(self, lines: list, reader=None, frame_jpeg: bytes | None = None, *,
                    asked: bool = False, now: float | None = None) -> Outcome:
        """The ladder, over what was just read and the frame it came from.

        `asked` is a Read the wearer pressed for: then a verdict is always
        spoken, even a shrug; in the background only evidence speaks.
        """
        outcome = Outcome()
        if not self.active:
            return outcome
        now = self._now(now)
        profile = self.profile
        wanted = list(profile.allergens)

        # 1. The barcode.
        code, facts = await self._product(frame_jpeg)
        outcome.barcode = code
        outcome.product = facts
        if code is not None and facts is None and asked:
            # A code was read but the database did not answer: say so
            # rather than let silence read as "nothing found".
            notice = self._notice(
                "I read a barcode, but I could not reach the product database. "
                "Check the label yourself.", "check", (), now, CHECK_COOLDOWN_S)
            if notice is not None:
                outcome.notices.append(notice)
        elif facts is not None and not facts.found and asked:
            notice = self._notice(
                "I read a barcode, but the product database does not know this product. "
                "Check the label yourself.", "check", (), now, CHECK_COOLDOWN_S)
            if notice is not None:
                outcome.notices.append(notice)
        if facts is not None and facts.found:
            hits = tuple(g for g in facts.allergens if g in wanted)
            traces = tuple(g for g in facts.traces if g in wanted and g not in hits)
            if hits:
                alert = self._alert(
                    hits, "barcode",
                    f"the barcode {facts.code} is {facts.title}, which Open Food Facts lists as "
                    f"containing {_names(hits)}",
                    1.0, now, product=facts.title,
                )
                if alert is not None:
                    outcome.alerts.append(alert)
                elif asked:
                    outcome.notices.append(Notice(
                        f"This is {facts.title}, and it contains {_names(hits)}, which you are allergic "
                        "to. Your doctor was emailed about this a few minutes ago.", "warning", hits))
            if traces and not hits:
                notice = self._notice(
                    f"This is {facts.title}. It may contain traces of {_names(traces)}, which you are "
                    "allergic to. Check the packet before eating.", "warning", traces, now)
                if notice is not None:
                    outcome.notices.append(notice)
            if hits or traces:
                return outcome
            if asked:
                notice = self._notice(
                    f"This is {facts.title}. Open Food Facts lists none of your allergens in it. "
                    "Still check the packet if you are unsure.", "clear", (), now, CHECK_COOLDOWN_S)
                if notice is not None:
                    outcome.notices.append(notice)
                return outcome

        # 2. The label's own statement. Only a CONTAINS / ALLERGENS /
        # INGREDIENTS line is evidence enough to email; an allergen word
        # elsewhere on the packet (a product name, say) is spoken as a
        # warning, since a name is not an ingredient list.
        firm: dict[str, str] = {}
        hedged: dict[str, str] = {}
        bare: dict[str, str] = {}
        best = 0.0
        for line in lines:
            confidence = self._line_confidence(reader, line)
            agreement = int(getattr(line, "agreement", 1) or 1)
            if confidence < self.min_confidence or agreement < self.min_agreement:
                continue
            for mention in find_allergen_mentions([line], wanted):
                target = hedged if mention.hedged else (firm if mention.statement else bare)
                if mention.allergen not in target:
                    target[mention.allergen] = mention.text
                best = max(best, confidence)
        if firm:
            quoted = "; ".join(dict.fromkeys(firm.values()))
            alert = self._alert(
                firm, "label",
                f'the label read "{quoted}" (the reader was {round(best * 100)}% sure, across frames that agreed)',
                best, now,
            )
            if alert is not None:
                outcome.alerts.append(alert)
            elif asked:
                outcome.notices.append(Notice(
                    f"This label says it contains {_names(firm)}, which you are allergic to. Your doctor "
                    "was emailed about this a few minutes ago.", "warning", tuple(firm)))
            return outcome
        if hedged:
            groups = tuple(hedged)
            notice = self._notice(
                f"This label says it may contain {_names(groups)}, which you are allergic to. "
                "Check the packet before eating.", "warning", groups, now)
            if notice is not None:
                outcome.notices.append(notice)
            return outcome
        if bare:
            groups = tuple(bare)
            notice = self._notice(
                f"This packet mentions {_names(groups)}, which you are allergic to. "
                "Check the ingredients before eating.", "warning", groups, now)
            if notice is not None:
                outcome.notices.append(notice)
            return outcome

        # 3. The online model over the text, when there is a label to judge.
        food_label = looks_like_food_label(lines)
        if self.reasoner is not None and lines and (food_label or asked):
            verdict = await self._judge(lines, wanted, now, frame_jpeg)
            outcome.verdict = verdict
            if verdict.kind == "confirm":
                reason = f" {verdict.reason.rstrip('.')}." if verdict.reason else ""
                notice = self._notice(
                    f"This label appears to contain {_names(verdict.allergens)}, which you are allergic "
                    f"to.{reason} Check the packet before eating.", "warning", verdict.allergens, now)
                if notice is not None:
                    outcome.notices.append(notice)
                return outcome
            if verdict.kind == "deny":
                if asked:
                    notice = self._notice(
                        "From the ingredients I could read, none of your allergens are listed. "
                        "Still check the packet if you are unsure.", "clear", (), now, CHECK_COOLDOWN_S)
                    if notice is not None:
                        outcome.notices.append(notice)
                return outcome
            if asked or (food_label and self.eating.eating(now).confirmed):
                notice = self._notice(CHECK_BEFORE_EATING, "check", (), now, CHECK_COOLDOWN_S)
                if notice is not None:
                    outcome.notices.append(notice)
            return outcome
        if asked and food_label:
            notice = self._notice(NO_STATEMENT, "check", (), now, CHECK_COOLDOWN_S)
            if notice is not None:
                outcome.notices.append(notice)
        return outcome

    # -- what an alert sounds like ------------------------------------------

    def spoken(self, alert: Alert) -> str:
        if alert.source == "barcode":
            head = f"Careful. This is {alert.product}, and it contains {alert.names}, which you are allergic to."
        else:
            head = f"Careful. This label says it contains {alert.names}, which you are allergic to."
        if self.mailer is None:
            return f"{head} I can't email your doctor from this machine."
        return f"{head} I am emailing your doctor."

    # -- the email -----------------------------------------------------------

    def compose_alert(self, alert: Alert, frame_jpeg: bytes | None = None):
        assert self.mailer is not None
        profile = self.profile
        who = profile.name or "the wearer"
        subject = f"Med-i-Glasses alert: possible {alert.names} exposure"
        if profile.name:
            subject += f" for {profile.name}"
        where = alert.place or "not known (no place recognized recently)"
        body = (
            f"This is an automated alert from Med-i-Glasses, the assistive glasses worn by {who}.\n\n"
            f"At {when_text(alert.at)}, the glasses found evidence that what they are eating, or are "
            f"about to eat, contains {alert.names}, which is on their allergy list.\n\n"
            f"Evidence: {alert.evidence}.\n"
            f"Where: {where}.\n"
            f"Allergies on file: {', '.join(profile.allergens) or 'none'}.\n\n"
            + ("A photo of the moment is attached.\n\n" if frame_jpeg else "")
            + "This alert was generated automatically and has not been checked by a person. If it turns "
            "out to be a false alarm, a correction will follow from this address.\n"
        )
        return self.mailer.compose(subject, body, to=profile.doctor_email or None, jpeg=frame_jpeg)

    def compose_correction(self, alert: Alert):
        assert self.mailer is not None
        profile = self.profile
        who = profile.name or "the wearer"
        subject = f"Med-i-Glasses correction: the {alert.names} alert was a false alarm"
        body = (
            f"{who} has said that the Med-i-Glasses alert sent at {when_text(alert.at)} about "
            f"{alert.names} was a false alarm. Please disregard it.\n\n"
            f"The alert's evidence was: {alert.evidence}.\n"
        )
        return self.mailer.compose(subject, body, to=profile.doctor_email or None)

    async def deliver(self, alert: Alert, frame_jpeg: bytes | None = None) -> Delivery | None:
        """Send the email for an alert; None when this machine has no mailer."""
        if self.mailer is None:
            return None
        delivery = await self.mailer.send(self.compose_alert(alert, frame_jpeg))
        alert.delivery = delivery
        alert.emailed = delivery.sent
        return delivery

    async def false_alarm(self, now: float | None = None) -> str:
        """The wearer says the last alert was wrong: tell the doctor so."""
        now = self._now(now)
        alert = self.last_alert
        if alert is None or time.time() - alert.at > self.cooldown_s:
            return "There is no recent allergy alert to cancel."
        if self.mailer is None:
            return "Noted, false alarm. Mail is off on this machine, so there is nothing to correct."
        delivery = await self.mailer.send(self.compose_correction(alert))
        return f"Noted, false alarm. {delivery.spoken('the correction to your doctor')}"
