"""The wearer's profile and the mail check, for the Allergies sheet: plain
HTTP beside the socket, like the places panel.

The sheet is a sighted helper's tool and needs no session. Every edit
answers with the whole profile so the sheet redraws from what the server
now holds, and the client speaks the change. `bind()` tells the router
where the profile and the mailer live; until then the routes say so.
"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.alerts.email_agent import EmailAgent
from backend.alerts.profile import KNOWN_ALLERGENS, Profile, ProfileStore

router = APIRouter()
_store: Callable[[], ProfileStore | None] = lambda: None
_mailer: Callable[[], EmailAgent | None] = lambda: None


def bind(store: Callable[[], ProfileStore | None], mailer: Callable[[], EmailAgent | None]) -> None:
    """Where the routes find the profile and the mailer."""
    global _store, _mailer
    _store = store
    _mailer = mailer


class ProfileEdit(BaseModel):
    name: str | None = None
    allergens: list[str] | None = None
    doctor_email: str | None = None


def _payload() -> dict:
    store = _store()
    mailer = _mailer()
    profile = store.current if store is not None else Profile()
    to = profile.doctor_email or (mailer.to if mailer is not None else "")
    return {
        "enabled": store is not None,
        "profile": profile.to_dict(),
        "known_allergens": list(KNOWN_ALLERGENS),
        "mail": {
            "configured": bool(mailer is not None and mailer.configured),
            "note": mailer.note if mailer is not None else "Mail is off.",
            "to": to,
        },
    }


@router.get("/profile")
async def profile_index() -> JSONResponse:
    return JSONResponse(_payload())


@router.post("/profile")
async def profile_edit(body: ProfileEdit) -> JSONResponse:
    store = _store()
    if store is None:
        return JSONResponse({"error": "the profile is off"}, status_code=503)
    store.update(name=body.name, allergens=body.allergens, doctor_email=body.doctor_email)
    return JSONResponse(_payload())


@router.post("/alerts/test")
async def alerts_test() -> JSONResponse:
    """A test email through the same path an alert takes, so the mail
    setup can be checked without a peanut."""
    mailer = _mailer()
    if mailer is None:
        return JSONResponse({"error": "mail is off"}, status_code=503)
    store = _store()
    profile = store.current if store is not None else Profile()
    to = profile.doctor_email or mailer.to
    who = profile.name or "the wearer"
    when = time.strftime("%I:%M %p on %A %d %B %Y").lstrip("0")
    message = mailer.compose(
        "Med-i-Glasses test: allergy alerts can reach this address",
        f"This is a test message from Med-i-Glasses, the assistive glasses used by {who}.\n\n"
        f"If you are reading it, allergy alerts from the glasses can reach this address. "
        f"Sent at {when}.\n",
        to=to,
    )
    delivery = await mailer.send(message)
    return JSONResponse({
        "sent": delivery.sent,
        "to": delivery.to,
        "path": delivery.path,
        "error": delivery.error,
        "spoken": delivery.spoken("the test email"),
    })
