"""The email to the doctor, and the outbox that stands in for it.

An alert that leaves the room has to be delivered or seen to fail. The
agent sends over SMTP with STARTTLS (Gmail's smtp.gmail.com:587 with an
app password is what the demo uses) and keeps a copy of everything it sends
under data/outbox/sent/. Without credentials, or when a send fails, the
message is written to data/outbox/ instead and the caller is told so in
words: "I have emailed your doctor" must never be said of an email that went
nowhere, and the wearer cannot see the outbox.

Credentials come from settings and are used once, inside the send. They are
never logged, never put in a message and never in an error string; the
strings smtplib produces name the failure, and the password is scrubbed from
them regardless.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587
SEND_TIMEOUT_S = 20.0
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Delivery:
    """What became of a message."""

    sent: bool
    to: str = ""
    path: str | None = None  # the copy on disk: outbox/sent/ when sent, outbox/ when not
    error: str | None = None  # why it was not sent; None when only credentials are missing

    def spoken(self, what: str = "the email to your doctor") -> str:
        if self.sent:
            return f"{what[0].upper()}{what[1:]} has been sent."
        if self.error is None:
            return f"Mail is not set up yet, so {what} is waiting in the outbox."
        return f"{what[0].upper()}{what[1:]} could not be sent; it is saved in the outbox."


class EmailAgent:
    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        user: str = "",
        password: str = "",
        to: str = "",
        outbox: str | Path = "data/outbox",
        sender_name: str = "VisionOS",
        timeout_s: float = SEND_TIMEOUT_S,
        smtp_factory: Callable[..., smtplib.SMTP] | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.user = user.strip()
        self._password = password
        self.to = to.strip()
        self.outbox = Path(outbox)
        self.sender_name = sender_name
        self.timeout_s = timeout_s
        self._smtp = smtp_factory or smtplib.SMTP

    # -- state ---------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.user and self._password)

    @property
    def note(self) -> str:
        """One line for /health and the sheet; never the password."""
        if self.configured:
            to = self.to or "the doctor's address in the profile"
            return f"Mail is set up: alerts go to {to} from {self.user}."
        return (
            "Mail is not set up: alerts are written to data/outbox. Put ALERT_SMTP_USER and "
            "ALERT_SMTP_PASSWORD (a Gmail app password) in .env to send them."
        )

    # -- messages ------------------------------------------------------------

    def compose(
        self,
        subject: str,
        body: str,
        *,
        to: str | None = None,
        jpeg: bytes | None = None,
        jpeg_name: str = "frame.jpg",
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        sender = self.user or "visionos@localhost"
        message["From"] = email.utils.formataddr((self.sender_name, sender))
        recipient = (to or self.to or "").strip()
        if recipient:
            message["To"] = recipient
        message["Date"] = email.utils.formatdate(localtime=True)
        message.set_content(body)
        if jpeg:
            message.add_attachment(jpeg, maintype="image", subtype="jpeg", filename=jpeg_name)
        return message

    # -- sending -------------------------------------------------------------

    def send_sync(self, message: EmailMessage) -> Delivery:
        """Send now, on this thread. Every outcome leaves a file behind."""
        to = str(message.get("To") or "").strip()
        if not to:
            return self._park(message, to, "no recipient: set the doctor's email in the profile")
        if not self.configured:
            return self._park(message, to, None)
        try:
            with self._smtp(self.host, self.port, timeout=self.timeout_s) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(self.user, self._password)
                smtp.send_message(message)
        except Exception as exc:  # any failure is a parked message, never a crash
            return self._park(message, to, self._describe(exc))
        path = self._write(message, sent=True)
        log.info("email sent to %s: %s", to, message["Subject"])
        return Delivery(sent=True, to=to, path=path)

    async def send(self, message: EmailMessage) -> Delivery:
        """The SMTP round trip on a worker thread; the event loop is never held."""
        return await asyncio.to_thread(self.send_sync, message)

    def _park(self, message: EmailMessage, to: str, error: str | None) -> Delivery:
        path = self._write(message, sent=False)
        if error is None:
            log.warning("email not sent (mail not configured); written to %s", path)
        else:
            log.error("email to %s not sent (%s); written to %s", to, error, path)
        return Delivery(sent=False, to=to, path=path, error=error)

    def _write(self, message: EmailMessage, *, sent: bool) -> str:
        folder = self.outbox / "sent" if sent else self.outbox
        folder.mkdir(parents=True, exist_ok=True)
        slug = _SLUG.sub("-", str(message.get("Subject") or "message").lower()).strip("-")[:48] or "message"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = folder / f"{stamp}_{slug}.eml"
        counter = 1
        while path.exists():
            counter += 1
            path = folder / f"{stamp}_{slug}-{counter}.eml"
        path.write_bytes(message.as_bytes())
        return str(path)

    def _describe(self, exc: Exception) -> str:
        text = f"{type(exc).__name__}: {exc}"
        if self._password and self._password in text:
            text = text.replace(self._password, "***")
        return text[:200]
