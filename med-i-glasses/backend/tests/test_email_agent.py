"""The email agent: sends over STARTTLS when it can, parks the message in
the outbox when it cannot, always says which in words, and never lets the
password out."""

from __future__ import annotations

import smtplib
from pathlib import Path

import pytest

from backend.alerts.email_agent import EmailAgent


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list = []
        self.message = None
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, context=None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user))

    def send_message(self, message) -> None:
        self.calls.append("send")
        self.message = message


class RefusingSMTP(FakeSMTP):
    def login(self, user: str, password: str) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")


def test_without_credentials_the_email_waits_in_the_outbox(tmp_path):
    agent = EmailAgent(outbox=tmp_path)
    assert not agent.configured
    message = agent.compose("Subject here", "Body text", to="doc@example.com", jpeg=b"\xff\xd8jpeg")
    delivery = agent.send_sync(message)
    assert not delivery.sent and delivery.error is None
    assert Path(delivery.path).parent == tmp_path
    raw = Path(delivery.path).read_bytes()
    assert b"Subject here" in raw and b"image/jpeg" in raw and b"doc@example.com" in raw
    assert "waiting in the outbox" in delivery.spoken()


def test_with_credentials_it_sends_over_starttls_and_keeps_a_copy(tmp_path):
    agent = EmailAgent(
        user="me@gmail.com", password="app-pass", to="doc@example.com", outbox=tmp_path, smtp_factory=FakeSMTP,
    )
    delivery = agent.send_sync(agent.compose("Hi", "Body"))
    assert delivery.sent and delivery.to == "doc@example.com"
    assert Path(delivery.path).parent == tmp_path / "sent"
    smtp = FakeSMTP.instances[-1]
    assert smtp.host == "smtp.gmail.com" and smtp.port == 587
    assert smtp.calls[:2] == ["ehlo", "starttls"] and ("login", "me@gmail.com") in smtp.calls
    assert smtp.message["To"] == "doc@example.com" and "Med-i-Glasses" in smtp.message["From"]
    assert delivery.spoken() == "The email to your doctor has been sent."


def test_a_refused_send_is_parked_and_the_password_never_appears(tmp_path):
    agent = EmailAgent(
        user="me@gmail.com", password="s3cret-app-pass", to="doc@example.com", outbox=tmp_path,
        smtp_factory=RefusingSMTP,
    )
    delivery = agent.send_sync(agent.compose("Hi", "Body"))
    assert not delivery.sent and "SMTPAuthenticationError" in delivery.error
    assert "s3cret" not in delivery.error
    assert "s3cret" not in Path(delivery.path).read_text(errors="replace")
    assert Path(delivery.path).parent == tmp_path
    assert "could not be sent" in delivery.spoken()


def test_no_recipient_is_parked_with_a_reason(tmp_path):
    agent = EmailAgent(user="me@gmail.com", password="x", outbox=tmp_path, smtp_factory=FakeSMTP)
    delivery = agent.send_sync(agent.compose("Hi", "Body"))
    assert not delivery.sent and "no recipient" in delivery.error


def test_the_note_names_the_setup_and_never_the_password():
    agent = EmailAgent(user="me@gmail.com", password="s3cret", to="doc@example.com")
    assert agent.configured
    assert "doc@example.com" in agent.note and "me@gmail.com" in agent.note and "s3cret" not in agent.note
    assert "not set up" in EmailAgent().note


@pytest.mark.asyncio
async def test_send_runs_off_the_loop(tmp_path):
    agent = EmailAgent(outbox=tmp_path)
    delivery = await agent.send(agent.compose("A", "B", to="doc@example.com"))
    assert delivery.path and Path(delivery.path).exists()


def test_two_messages_in_one_second_get_their_own_files(tmp_path):
    agent = EmailAgent(outbox=tmp_path)
    first = agent.send_sync(agent.compose("Same subject", "one", to="doc@example.com"))
    second = agent.send_sync(agent.compose("Same subject", "two", to="doc@example.com"))
    assert first.path != second.path
    assert len(list(tmp_path.glob("*.eml"))) == 2
