"""The allergy scanner's ladder: a barcode hit or a sure CONTAINS line
alerts and emails; "may contain", the model's opinion and a food in view
only speak; one email per allergen per cooldown; a false alarm sends a
correction; and the whole path through a Session."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.ai.ocr import TextLine
from backend.alerts import watch as watch_module
from backend.alerts.barcode import ProductFacts, parse_product, valid_ean
from backend.alerts.email_agent import EmailAgent
from backend.alerts.profile import Profile
from backend.alerts.reasoner import LabelReasoner, Verdict, parse_verdict
from backend.alerts.watch import CHECK_BEFORE_EATING, EATING_PROMPT, NO_STATEMENT, AllergyWatch
from backend.config import Settings
from backend.main import Session
from backend.perception.detector import Detection
from backend.perception.geometry import BoundingBox
from backend.tests.test_peek import FakePerception, FakeProvider, FakeReader, FakeSocket, frame

PROFILE = Profile(name="Peter Savage", allergens=["peanut", "dairy"], doctor_email="doc@example.com")
FRAME = (1000, 1000)
CODE = "4006381333931"  # a valid EAN-13


def line(text: str, confidence: float = 0.9, agreement: int = 2) -> TextLine:
    return TextLine(text=text, confidence=confidence, top=0.3, left=0.1, height=0.05, agreement=agreement)


def det(label: str, confidence: float, y1: float, y2: float) -> Detection:
    return Detection(label, confidence, BoundingBox(100, y1, 300, y2), 0.0, 0.0, None)


class Clock:
    def __init__(self, at: float = 1000.0) -> None:
        self.t = at

    def __call__(self) -> float:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += seconds


class FakeLookup:
    def __init__(self, facts: dict[str, ProductFacts | None]) -> None:
        self.facts_by_code = facts
        self.calls = 0

    async def facts(self, code: str):
        self.calls += 1
        return self.facts_by_code.get(code)


class FakeReasoner:
    available = True

    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict
        self.calls = 0

    async def judge(self, lines, allergens, frame_jpeg=None) -> Verdict:
        self.calls += 1
        return self.verdict


def make_watch(tmp_path, *, profile: Profile = PROFILE, lookup=None, reasoner=None, mailer: bool = True):
    clock = Clock()
    made = AllergyWatch(
        profile=lambda: profile,
        mailer=EmailAgent(outbox=tmp_path) if mailer else None,
        lookup=lookup,
        reasoner=reasoner,
        place=lambda: "Office 1",
        clock=clock,
    )
    return made, clock


class TestLabelEvidence:
    @pytest.mark.asyncio
    async def test_a_sure_contains_line_alerts_and_emails(self, tmp_path):
        watch, _ = make_watch(tmp_path)
        outcome = await watch.check([line("CONTAINS: PEANUTS")], None, asked=True)
        assert len(outcome.alerts) == 1 and not outcome.notices
        alert = outcome.alerts[0]
        assert alert.allergens == ("peanut",) and alert.source == "label" and alert.place == "Office 1"
        assert watch.spoken(alert) == (
            "Careful. This label says it contains peanuts, which you are allergic to. I am emailing your doctor."
        )
        event = alert.to_event(watch.spoken(alert))
        assert event["type"] == "hazard" and event["kind"] == "allergy" and event["severity"] == 2
        delivery = await watch.deliver(alert, b"\xff\xd8jpeg")
        assert delivery is not None and not delivery.sent and delivery.path
        raw = Path(delivery.path).read_text(errors="replace")
        assert "Peter Savage" in raw and "peanuts" in raw and "doc@example.com" in raw and "Office 1" in raw
        assert "CONTAINS: PEANUTS" in raw and "image/jpeg" in raw

    @pytest.mark.asyncio
    async def test_one_frame_is_a_glimpse(self, tmp_path):
        watch, _ = make_watch(tmp_path)
        outcome = await watch.check([line("CONTAINS: PEANUTS", 0.95, 1)], None, asked=False)
        assert not outcome.spoke

    @pytest.mark.asyncio
    async def test_below_80_percent_is_not_evidence(self, tmp_path):
        watch, _ = make_watch(tmp_path)
        outcome = await watch.check([line("CONTAINS: PEANUTS", 0.7, 3)], None, asked=False)
        assert not outcome.spoke and watch.alerts == []

    @pytest.mark.asyncio
    async def test_may_contain_warns_without_an_email(self, tmp_path):
        watch, _ = make_watch(tmp_path)
        outcome = await watch.check([line("MAY CONTAIN PEANUTS")], None)
        assert not outcome.alerts and len(outcome.notices) == 1
        notice = outcome.notices[0]
        assert notice.kind == "warning" and notice.tone and "may contain peanuts" in notice.text
        assert notice.to_event()["kind"] == "allergy-warning" and notice.to_event()["severity"] == 1
        assert not list(tmp_path.glob("*.eml"))

    @pytest.mark.asyncio
    async def test_an_allergen_word_outside_a_statement_warns_without_an_email(self, tmp_path):
        """A product name is not an ingredient list: "PEANUT BUTTER CUPS" is
        spoken as a warning; the email waits for a CONTAINS or INGREDIENTS
        line, or a barcode."""
        watch, _ = make_watch(tmp_path)
        outcome = await watch.check([line("PEANUT BUTTER CUPS")], None, asked=True)
        assert not outcome.alerts and outcome.notices[0].kind == "warning"
        assert "mentions peanuts" in outcome.notices[0].text
        assert (await watch.check([line("INGREDIENTS: SUGAR, PEANUTS, SALT")], None)).alerts

    @pytest.mark.asyncio
    async def test_an_allergen_not_in_the_profile_is_nothing(self, tmp_path):
        watch, _ = make_watch(tmp_path)
        assert not (await watch.check([line("CONTAINS: SOY")], None, asked=True)).spoke

    @pytest.mark.asyncio
    async def test_one_email_per_allergen_per_cooldown(self, tmp_path):
        watch, clock = make_watch(tmp_path)
        assert (await watch.check([line("CONTAINS: PEANUTS")], None)).alerts
        clock.tick(60)
        again = await watch.check([line("CONTAINS: PEANUTS")], None, asked=True)
        assert not again.alerts and again.notices[0].kind == "warning"
        assert "a few minutes ago" in again.notices[0].text
        clock.tick(600)
        assert (await watch.check([line("CONTAINS: PEANUTS")], None)).alerts
        # A different allergen is not held back by the first one's cooldown.
        assert (await watch.check([line("CONTAINS: MILK")], None)).alerts[0].allergens == ("dairy",)

    @pytest.mark.asyncio
    async def test_a_profile_without_allergens_is_off(self, tmp_path):
        watch, _ = make_watch(tmp_path, profile=Profile(name="x"))
        assert not watch.active
        assert not (await watch.check([line("CONTAINS: PEANUTS")], None, asked=True)).spoke
        assert watch.observe_frame([det("peanut butter", 0.9, 600, 950)], FRAME) is None

    @pytest.mark.asyncio
    async def test_a_reader_whose_confidence_means_nothing_uses_the_words(self, tmp_path):
        """Vision reports ~0.5 for everything; the known-word share stands in."""
        watch, _ = make_watch(tmp_path)
        reader = FakeReader([])
        reader.confidence_informative = False
        outcome = await watch.check([line("CONTAINS PEANUTS", 0.5, 2)], reader)
        assert outcome.alerts, "every word is in the lexicon, so the line is sure"


class TestBarcodeEvidence:
    def test_ean_checksums(self):
        assert valid_ean("4006381333931") and valid_ean("96385074") and valid_ean("036000291452")
        assert not valid_ean("4006381333932") and not valid_ean("abc") and not valid_ean("")

    def test_open_food_facts_json_becomes_facts(self):
        payload = {"status": 1, "product": {
            "product_name": "Crunchy Peanut Butter", "brands": "Jif,Smucker",
            "allergens_tags": ["en:peanuts"], "traces_tags": ["en:milk", "en:mustard"],
        }}
        facts = parse_product("0051500255162", payload)
        assert facts.found and facts.title == "Jif Crunchy Peanut Butter"
        assert facts.allergens == ("peanut",) and facts.traces == ("dairy", "mustard")
        assert not parse_product("1", {"status": 0}).found
        assert parse_product("1", {"status": 1, "product": {}}).title == "product 1"

    @pytest.mark.asyncio
    async def test_a_known_product_with_the_allergen_alerts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch_module, "decode_barcodes", lambda jpeg: [CODE])
        lookup = FakeLookup({CODE: ProductFacts(CODE, True, "Peanut Butter", "Jif", ("peanut",), ())})
        watch, _ = make_watch(tmp_path, lookup=lookup)
        outcome = await watch.check([], None, b"jpeg", asked=False)
        assert outcome.alerts and outcome.alerts[0].source == "barcode"
        assert "This is Jif Peanut Butter, and it contains peanuts" in watch.spoken(outcome.alerts[0])
        assert lookup.calls == 1

    @pytest.mark.asyncio
    async def test_traces_warn_without_an_email(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch_module, "decode_barcodes", lambda jpeg: [CODE])
        lookup = FakeLookup({CODE: ProductFacts(CODE, True, "Oat Bar", "", (), ("peanut",))})
        watch, _ = make_watch(tmp_path, lookup=lookup)
        outcome = await watch.check([], None, b"jpeg")
        assert not outcome.alerts and outcome.notices[0].kind == "warning"
        assert "traces of peanuts" in outcome.notices[0].text

    @pytest.mark.asyncio
    async def test_a_clean_product_is_said_only_when_asked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch_module, "decode_barcodes", lambda jpeg: [CODE])
        lookup = FakeLookup({CODE: ProductFacts(CODE, True, "Rice Cakes", "", (), ())})
        watch, _ = make_watch(tmp_path, lookup=lookup)
        assert not (await watch.check([], None, b"jpeg", asked=False)).spoke
        outcome = await watch.check([], None, b"jpeg", asked=True)
        assert outcome.notices[0].kind == "clear" and "none of your allergens" in outcome.notices[0].text

    @pytest.mark.asyncio
    async def test_an_unknown_product_falls_through_to_the_label(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch_module, "decode_barcodes", lambda jpeg: [CODE])
        lookup = FakeLookup({CODE: ProductFacts(CODE, False)})
        watch, _ = make_watch(tmp_path, lookup=lookup)
        outcome = await watch.check([line("CONTAINS: PEANUTS")], None, b"jpeg")
        assert outcome.alerts and outcome.alerts[0].source == "label"

    @pytest.mark.asyncio
    async def test_no_lookup_means_no_barcode_step(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch_module, "decode_barcodes", lambda jpeg: [CODE])
        watch, _ = make_watch(tmp_path, lookup=None)
        assert (await watch.check([], None, b"jpeg", asked=True)).product is None


class TestReasoner:
    def test_the_answer_must_be_the_shape_asked_for(self):
        verdict = parse_verdict(
            'Sure: {"verdict": "confirm", "allergens": ["dairy", "soy"], "evidence": "the label reads WHEY", '
            '"reason": "Whey is milk."}',
            ["dairy", "peanut"],
        )
        assert verdict.kind == "confirm" and verdict.allergens == ("dairy",) and verdict.reason == "Whey is milk."
        assert verdict.evidence == "the label reads WHEY"
        assert parse_verdict("I think it is fine.", ["dairy"]) is None
        assert parse_verdict('{"verdict": "maybe"}', ["dairy"]) is None
        assert parse_verdict('{"verdict": "confirm", "allergens": ["soy"], "evidence": "x"}', ["dairy"]).kind == "check"
        assert parse_verdict('{"verdict": "deny", "allergens": []}', ["dairy"]).kind == "deny"

    def test_a_confirm_without_evidence_is_a_check(self):
        """'Cookies usually contain nuts' names no label and no product."""
        verdict = parse_verdict(
            '{"verdict": "confirm", "allergens": ["tree nut"], "evidence": "", "reason": "cookies usually contain nuts"}',
            ["tree nut"],
        )
        assert verdict.kind == "check" and verdict.from_model

    def test_without_a_key_the_reasoner_is_off(self):
        settings = Settings(vision_provider="nvidia", nvidia_api_key=None, _env_file=None)
        assert not LabelReasoner(settings).available
        assert not LabelReasoner(Settings(vision_provider="local", nvidia_api_key="x", _env_file=None)).available

    @pytest.mark.asyncio
    async def test_off_means_check(self):
        settings = Settings(vision_provider="local", _env_file=None)
        verdict = await LabelReasoner(settings).judge([line("INGREDIENTS: WHEY")], ["dairy"])
        assert verdict.kind == "check" and not verdict.from_model

    @pytest.mark.asyncio
    async def test_the_models_confirm_warns_and_never_emails(self, tmp_path):
        reasoner = FakeReasoner(Verdict("confirm", ("dairy",), "Lactalbumin is a milk protein", "model"))
        watch, _ = make_watch(tmp_path, reasoner=reasoner)
        # A derived name the word list does not know: the model's job.
        lines = [line("INGREDIENTS: WHEAT FLOUR, LACTALBUMIN, SOY LECITHIN"), line("CALORIES 120 PER SERVING")]
        outcome = await watch.check(lines, None, asked=False)
        assert not outcome.alerts and outcome.notices[0].kind == "warning"
        assert "dairy" in outcome.notices[0].text and "Lactalbumin is a milk protein" in outcome.notices[0].text
        assert not list(tmp_path.glob("*.eml"))

    @pytest.mark.asyncio
    async def test_the_models_check_is_spoken_when_asked(self, tmp_path):
        reasoner = FakeReasoner(Verdict("check", (), "the online helper did not answer", "unavailable"))
        watch, _ = make_watch(tmp_path, reasoner=reasoner)
        lines = [line("INGREDIENTS: SUGAR, SALT"), line("NUTRITION FACTS")]
        assert not (await watch.check(lines, None, asked=False)).spoke
        outcome = await watch.check(lines, None, asked=True)
        assert outcome.notices[0].text == CHECK_BEFORE_EATING

    @pytest.mark.asyncio
    async def test_the_models_deny_is_spoken_only_when_asked(self, tmp_path):
        reasoner = FakeReasoner(Verdict("deny", (), "no allergens listed", "model"))
        watch, _ = make_watch(tmp_path, reasoner=reasoner)
        lines = [line("INGREDIENTS: SUGAR, SALT"), line("NUTRITION FACTS")]
        assert not (await watch.check(lines, None, asked=False)).spoke
        outcome = await watch.check(lines, None, asked=True)
        assert outcome.notices[0].kind == "clear"

    @pytest.mark.asyncio
    async def test_the_same_label_is_judged_once(self, tmp_path):
        reasoner = FakeReasoner(Verdict("deny", (), "", "model"))
        watch, _ = make_watch(tmp_path, reasoner=reasoner)
        lines = [line("INGREDIENTS: SUGAR, SALT"), line("NUTRITION FACTS")]
        await watch.check(lines, None)
        await watch.check(lines, None)
        assert reasoner.calls == 1

    @pytest.mark.asyncio
    async def test_a_sign_is_not_sent_to_the_model(self, tmp_path):
        reasoner = FakeReasoner(Verdict("deny", (), "", "model"))
        watch, _ = make_watch(tmp_path, reasoner=reasoner)
        await watch.check([line("EXIT THIS WAY")], None, asked=False)
        assert reasoner.calls == 0

    @pytest.mark.asyncio
    async def test_no_model_and_a_food_label_when_asked(self, tmp_path):
        watch, _ = make_watch(tmp_path, reasoner=None)
        lines = [line("INGREDIENTS: SUGAR, SALT"), line("NUTRITION FACTS")]
        outcome = await watch.check(lines, None, asked=True)
        assert outcome.notices[0].text == NO_STATEMENT


class TestSightTrigger:
    def test_eating_prompts_once(self, tmp_path):
        watch, clock = make_watch(tmp_path)
        detections = [det("sandwich", 0.9, 600, 950)]
        notices = [watch.observe_frame(detections, FRAME, now=clock.t + i * 0.3) for i in range(3)]
        assert notices[:2] == [None, None]
        assert notices[2] is not None and notices[2].kind == "prompt" and notices[2].text == EATING_PROMPT
        assert notices[2].to_event() == {"type": "speech", "text": EATING_PROMPT}
        assert watch.observe_frame(detections, FRAME, now=clock.t + 1.0) is None

    def test_a_food_that_is_the_allergen_warns_hedged_without_an_email(self, tmp_path):
        watch, clock = make_watch(tmp_path)
        detections = [det("peanut butter", 0.9, 600, 950)]
        notice = None
        for index in range(3):
            notice = watch.observe_frame(detections, FRAME, now=clock.t + index * 0.3)
        assert notice is not None and notice.kind == "warning"
        assert "peanut butter" in notice.text and "peanuts" in notice.text and "can't confirm" in notice.text
        assert watch.alerts == [] and not list(tmp_path.glob("*.eml"))

    def test_a_food_outside_the_profile_is_just_eating(self, tmp_path):
        watch, clock = make_watch(tmp_path, profile=Profile(allergens=["dairy"]))
        detections = [det("peanuts", 0.9, 600, 950)]
        notice = None
        for index in range(3):
            notice = watch.observe_frame(detections, FRAME, now=clock.t + index * 0.3)
        assert notice is not None and notice.kind == "prompt"


class TestFalseAlarm:
    @pytest.mark.asyncio
    async def test_a_false_alarm_sends_a_correction(self, tmp_path):
        watch, _ = make_watch(tmp_path)
        alert = (await watch.check([line("CONTAINS: PEANUTS")], None)).alerts[0]
        await watch.deliver(alert)
        spoken = await watch.false_alarm()
        assert spoken.startswith("Noted, false alarm.") and "outbox" in spoken
        files = sorted(tmp_path.glob("*.eml"))
        assert len(files) == 2
        raw = files[-1].read_text(errors="replace")
        assert "false alarm" in raw and "doc@example.com" in raw and "Peter Savage" in raw

    @pytest.mark.asyncio
    async def test_nothing_to_cancel(self, tmp_path):
        watch, _ = make_watch(tmp_path)
        assert "no recent allergy alert" in await watch.false_alarm()


class SureReader(FakeReader):
    """A quick pass as sure as the burst, so two peeks agree at 0.9. Each
    peek is one frame's reading, so its lines carry no agreement yet."""

    async def read_quick(self, frame_jpeg: bytes) -> list[TextLine]:
        self.quick_calls += 1
        return [TextLine(l.text, 0.9, l.top, l.left, l.height) for l in self.lines]


def make_session(tmp_path, lines: list[TextLine], reader_class=FakeReader):
    reader = reader_class(lines)
    socket = FakeSocket()
    watch, _ = make_watch(tmp_path)
    session = Session(socket, FakeProvider(), FakePerception(), reader, None, watch)
    return session, socket


class TestSession:
    @pytest.mark.asyncio
    async def test_a_read_of_a_contains_label_alerts_speaks_and_writes_the_email(self, tmp_path):
        session, socket = make_session(tmp_path, [line("CONTAINS: PEANUTS")])
        await session.handle_read([frame()])
        await asyncio.gather(*session._tasks)
        # The read itself comes first, as it always did (the lexicon
        # singularizes the plural in speech; that is its business).
        assert socket.spoken()[0].startswith("It reads: CONTAINS")
        hazards = [p for p in socket.sent if p.get("type") == "hazard"]
        assert len(hazards) == 1 and hazards[0]["kind"] == "allergy" and hazards[0]["allergens"] == ["peanut"]
        assert "I am emailing your doctor" in hazards[0]["text"]
        assert socket.spoken()[-1] == "Mail is not set up yet, so the email to your doctor is waiting in the outbox."
        assert len(list(tmp_path.glob("*.eml"))) == 1

    @pytest.mark.asyncio
    async def test_a_sure_quick_read_of_an_allergen_still_takes_the_burst(self, tmp_path):
        """One sharp frame settles an ordinary read. A label naming one of
        the wearer's allergens runs the burst anyway, like a dose, so two
        frames can agree and release the email."""
        session, socket = make_session(tmp_path, [line("CONTAINS: PEANUTS")], SureReader)
        await session.handle_read([frame(), frame(), frame()])
        await asyncio.gather(*session._tasks)
        reader = session.ocr
        assert reader.burst_calls == 1, "the quick pass was sure, and the burst ran anyway"
        hazards = [p for p in socket.sent if p.get("type") == "hazard"]
        assert len(hazards) == 1 and hazards[0]["kind"] == "allergy"
        # A plain sure label settles on the quick pass as before.
        session, _ = make_session(tmp_path, [line("EXIT THIS WAY")], SureReader)
        await session.handle_read([frame()])
        assert session.ocr.burst_calls == 0

    @pytest.mark.asyncio
    async def test_a_false_alarm_after_an_alert(self, tmp_path):
        session, socket = make_session(tmp_path, [line("CONTAINS: PEANUTS")])
        await session.handle_read([frame()])
        await asyncio.gather(*session._tasks)
        await session.handle_false_alarm()
        assert socket.spoken()[-1].startswith("Noted, false alarm.")

    @pytest.mark.asyncio
    async def test_two_agreeing_peeks_alert_in_the_background(self, tmp_path):
        session, socket = make_session(tmp_path, [line("CONTAINS: PEANUTS")], SureReader)
        await session.handle_peek([frame()])
        await session.peek_task
        assert not [p for p in socket.sent if p.get("type") == "hazard"], "one peek is a glimpse"
        await session.handle_peek([frame()])
        await session.peek_task
        await asyncio.gather(*session._tasks)
        hazards = [p for p in socket.sent if p.get("type") == "hazard"]
        assert len(hazards) == 1 and hazards[0]["kind"] == "allergy"

    @pytest.mark.asyncio
    async def test_a_plain_sign_is_just_read(self, tmp_path):
        session, socket = make_session(tmp_path, [line("EXIT THIS WAY")])
        await session.handle_read([frame()])
        assert socket.spoken() == ["It reads: EXIT THIS WAY."]

    @pytest.mark.asyncio
    async def test_without_the_scanner_the_session_says_so(self):
        socket = FakeSocket()
        session = Session(socket, FakeProvider(), FakePerception(), FakeReader([]))
        await session.handle_false_alarm()
        assert socket.spoken() == ["The allergy scanner is off on this machine."]
