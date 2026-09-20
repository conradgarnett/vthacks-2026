"""The profile on disk and its routes: normalized, atomic, re-read when the
file changes; the test email through the same path an alert takes."""

from __future__ import annotations

import json
import os
from email import message_from_string
from email.policy import default as default_policy
from pathlib import Path

import pytest

from backend import profile_api
from backend.alerts.email_agent import EmailAgent
from backend.alerts.profile import Profile, ProfileStore
from backend.profile_api import ProfileEdit, alerts_test, profile_edit, profile_index


def body(response) -> dict:
    return json.loads(response.body)


@pytest.fixture
def bound(tmp_path):
    store = ProfileStore(tmp_path / "profile.json")
    mailer = EmailAgent(outbox=tmp_path / "outbox")
    profile_api.bind(lambda: store, lambda: mailer)
    yield store, mailer
    profile_api.bind(lambda: None, lambda: None)


class TestStore:
    def test_a_missing_file_is_an_empty_profile(self, tmp_path):
        assert ProfileStore(tmp_path / "none.json").current == Profile()

    def test_save_and_reload_normalize_the_allergens(self, tmp_path):
        path = tmp_path / "profile.json"
        ProfileStore(path).save(Profile.from_dict({
            "name": "Peter", "allergens": ["Peanuts", "Tree Nuts", "milk", "peanut"], "doctor_email": "doc@example.com",
        }))
        again = ProfileStore(path).current
        assert again.name == "Peter" and again.doctor_email == "doc@example.com"
        assert again.allergens == ["peanut", "tree nut", "dairy"]
        assert not list(tmp_path.glob(".profile-*")), "no temp file left behind"

    def test_an_edit_by_hand_is_picked_up(self, tmp_path):
        path = tmp_path / "profile.json"
        store = ProfileStore(path)
        store.save(Profile(allergens=["peanut"]))
        assert store.current.allergens == ["peanut"]
        path.write_text(json.dumps({"allergens": ["egg", "soy"]}), encoding="utf-8")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns + 5_000_000_000, stat.st_mtime_ns + 5_000_000_000))
        assert store.current.allergens == ["egg", "soy"]

    def test_bad_json_is_an_empty_profile_not_a_crash(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_text("{not json", encoding="utf-8")
        assert ProfileStore(path).current == Profile()

    def test_update_keeps_what_it_was_not_given(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        store.save(Profile(name="Peter", allergens=["peanut"], doctor_email="doc@example.com"))
        updated = store.update(allergens=["Eggs"])
        assert updated == Profile(name="Peter", allergens=["egg"], doctor_email="doc@example.com")


@pytest.mark.asyncio
async def test_get_and_post_round_trip(bound):
    store, _ = bound
    data = body(await profile_index())
    assert data["enabled"] and data["profile"]["allergens"] == [] and data["mail"]["configured"] is False
    assert "peanut" in data["known_allergens"]
    data = body(await profile_edit(ProfileEdit(name="Peter", allergens=["Peanuts"], doctor_email="doc@example.com")))
    assert data["profile"] == {"name": "Peter", "allergens": ["peanut"], "doctor_email": "doc@example.com"}
    assert data["mail"]["to"] == "doc@example.com"
    assert store.current.allergens == ["peanut"]


@pytest.mark.asyncio
async def test_a_partial_edit_keeps_the_rest(bound):
    await profile_edit(ProfileEdit(allergens=["peanut", "egg"], doctor_email="doc@example.com"))
    data = body(await profile_edit(ProfileEdit(name="Peter")))
    assert data["profile"] == {"name": "Peter", "allergens": ["peanut", "egg"], "doctor_email": "doc@example.com"}


@pytest.mark.asyncio
async def test_the_test_email_goes_through_the_outbox(bound):
    _, mailer = bound
    await profile_edit(ProfileEdit(name="Peter", doctor_email="doc@example.com"))
    data = body(await alerts_test())
    assert data["sent"] is False and data["path"] and data["to"] == "doc@example.com"
    assert "outbox" in data["spoken"]
    written = message_from_string(
        Path(data["path"]).read_text(errors="replace"), policy=default_policy
    )
    assert written["To"] == "doc@example.com"
    # Decode before matching. Quoted-printable wraps the body at 76
    # characters and will split whatever word straddles the boundary --
    # renaming the product to Med-i-Glasses pushed this line from 74 to 79
    # and cut "Peter" in half. The mail was always fine; the assertion was
    # reading the encoding rather than the message.
    assert "Peter" in written.get_content()
    assert Path(data["path"]).parent == mailer.outbox


@pytest.mark.asyncio
async def test_unbound_routes_say_so():
    profile_api.bind(lambda: None, lambda: None)
    assert (await profile_edit(ProfileEdit(name="x"))).status_code == 503
    assert body(await profile_index())["enabled"] is False
    assert (await alerts_test()).status_code == 503
