"""Who the wearer is, for the alerts: their name, their allergies, their
doctor's address.

A small JSON file under the gitignored data folder, edited from the
Allergies sheet in the client or by hand. It is re-read whenever the file
changes, so an edit takes effect without a restart, and every write is
atomic: an alert that fires mid-edit reads the old profile or the new one,
never half of each.

Allergen names are normalized to the groups the cross-check knows
("Peanuts" -> "peanut", "Tree Nuts" -> "tree nut", "milk" -> "dairy"); a
name it does not know is kept as typed, lowercased, and matched on the
label as that word.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# The groups the cross-check knows, in the wording the sheet offers.
KNOWN_ALLERGENS: tuple[str, ...] = (
    "peanut", "tree nut", "dairy", "egg", "gluten", "shellfish", "fish", "soy", "sesame",
)

_SYNONYMS: dict[str, str] = {
    "peanuts": "peanut", "groundnut": "peanut", "groundnuts": "peanut",
    "nut": "tree nut", "nuts": "tree nut", "tree nuts": "tree nut", "treenut": "tree nut",
    "treenuts": "tree nut", "almond": "tree nut", "almonds": "tree nut", "walnut": "tree nut",
    "walnuts": "tree nut", "cashew": "tree nut", "cashews": "tree nut", "hazelnut": "tree nut",
    "hazelnuts": "tree nut", "pecan": "tree nut", "pecans": "tree nut", "pistachio": "tree nut",
    "pistachios": "tree nut",
    "milk": "dairy", "lactose": "dairy", "dairy products": "dairy", "cow's milk": "dairy",
    "cows milk": "dairy",
    "eggs": "egg",
    "wheat": "gluten", "celiac": "gluten", "coeliac": "gluten",
    "shrimp": "shellfish", "prawns": "shellfish", "prawn": "shellfish", "crustaceans": "shellfish",
    "crustacean": "shellfish", "crab": "shellfish", "lobster": "shellfish", "molluscs": "shellfish",
    "mollusks": "shellfish",
    "soya": "soy", "soybean": "soy", "soybeans": "soy",
    "sesame seeds": "sesame", "sesame seed": "sesame",
}


def normalize_allergen(text: str) -> str:
    """'Peanuts' -> 'peanut', 'Tree-Nuts' -> 'tree nut'; otherwise the words, lowercased."""
    cleaned = " ".join(text.lower().replace("-", " ").replace("_", " ").split())
    return _SYNONYMS.get(cleaned, cleaned)


@dataclass
class Profile:
    name: str = ""
    allergens: list[str] = field(default_factory=list)
    doctor_email: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        raw = data.get("allergens") or []
        if isinstance(raw, str):
            raw = raw.split(",")
        allergens: list[str] = []
        for item in raw:
            name = normalize_allergen(str(item))
            if name and name not in allergens:
                allergens.append(name)
        return cls(
            name=str(data.get("name") or "").strip(),
            allergens=allergens,
            doctor_email=str(data.get("doctor_email") or "").strip(),
        )

    def to_dict(self) -> dict:
        return {"name": self.name, "allergens": list(self.allergens), "doctor_email": self.doctor_email}

    @property
    def has_allergens(self) -> bool:
        return bool(self.allergens)


class ProfileStore:
    """The profile on disk, re-read when the file changes."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self._loaded: Profile | None = None
        self._stamp: tuple[int, int] | None = None

    def _file_stamp(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def load(self) -> Profile:
        """Read the file. Missing or unreadable means an empty profile, said in the log."""
        stamp = self._file_stamp()
        if stamp is None:
            self._loaded, self._stamp = Profile(), None
            return self._loaded
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("profile is not an object")
            self._loaded = Profile.from_dict(data)
        except (OSError, ValueError) as exc:
            log.warning("profile %s could not be read (%s); using an empty one", self.path, exc)
            self._loaded = Profile()
        self._stamp = stamp
        return self._loaded

    @property
    def current(self) -> Profile:
        if self._loaded is None or self._file_stamp() != self._stamp:
            return self.load()
        return self._loaded

    def save(self, profile: Profile) -> Profile:
        """Write atomically: a temp file beside the target, then a rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(profile.to_dict(), indent=2, ensure_ascii=False) + "\n"
        handle, temp = tempfile.mkstemp(prefix=".profile-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(temp, self.path)
        except Exception:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        self._loaded = profile
        self._stamp = self._file_stamp()
        return profile

    def update(self, *, name: str | None = None, allergens: list[str] | None = None,
               doctor_email: str | None = None) -> Profile:
        """Change the given fields, keep the rest, save."""
        current = self.current
        data = current.to_dict()
        if name is not None:
            data["name"] = name
        if allergens is not None:
            data["allergens"] = allergens
        if doctor_email is not None:
            data["doctor_email"] = doctor_email
        return self.save(Profile.from_dict(data))
