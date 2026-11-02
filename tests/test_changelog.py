"""Notification center: /api/changelog and the shape of data/changelog.json."""

from __future__ import annotations

import json
from datetime import date

from fastapi.testclient import TestClient

from bits_marks_tracker.app import app
from bits_marks_tracker.storage import DATA_DIR

client = TestClient(app)


def _entries() -> list[dict[str, str]]:
    doc = json.loads((DATA_DIR / "changelog.json").read_text(encoding="utf-8"))
    entries: list[dict[str, str]] = doc["entries"]
    return entries


def test_changelog_newest_first() -> None:
    data = client.get("/api/changelog").json()
    dates = [e["date"] for e in data["entries"]]
    assert dates == sorted(dates, reverse=True)
    assert data["updated"] == dates[0]


def test_every_entry_is_well_formed() -> None:
    entries = _entries()
    assert entries, "changelog should not be empty"
    for e in entries:
        assert set(e) == {"date", "title", "description"}, e
        date.fromisoformat(e["date"])  # valid YYYY-MM-DD
        assert e["title"].strip() and len(e["title"]) <= 60, e["title"]
        # one-line description: no line breaks, short enough to read at a glance
        assert "\n" not in e["description"] and len(e["description"]) <= 140, e["description"]


def test_no_duplicate_titles() -> None:
    titles = [e["title"] for e in _entries()]
    assert len(titles) == len(set(titles))
