"""API tests using local file storage in a temp directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import bits_marks_tracker.app as app_module
from bits_marks_tracker.storage import LocalStorage, WriteConflictError

TERM = "2026-S1"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    storage = LocalStorage(data_dir=tmp_path)
    monkeypatch.setattr(app_module, "get_storage", lambda: storage)
    return TestClient(app_module.app)


def _submit(client: TestClient, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "term": TERM,
        "bits_id": "2025aa05123",
        "name": "Niraj",
        "pin": "1234",
        "marks": {"MFML": {"quiz1": 4.5, "assignment1": 9}},
    }
    payload.update(overrides)
    return client.post("/api/submit", json=payload)


def test_submit_and_leaderboard(client: TestClient) -> None:
    resp = _submit(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["bits_id"] == "2025AA05123"  # normalized
    assert data["anon"] is False

    board = client.get("/api/leaderboard", params={"term": TERM}).json()
    assert board["stats"]["total_students"] == 1
    entry = board["students"][0]
    assert entry["subjects"]["MFML"]["total"] == 13.5
    assert entry["subjects"]["MFML"]["max_entered"] == 15
    assert entry["subjects"]["MFML"]["pct"] == 90.0
    assert entry["overall"]["pct"] == 90.0


def test_resubmit_merges_components(client: TestClient) -> None:
    _submit(client)
    _submit(client, marks={"MFML": {"midsem": 24}, "ML": {"quiz1": 5}})

    student = client.get("/api/student", params={"term": TERM, "bits_id": "2025AA05123"}).json()[
        "student"
    ]
    assert student["marks"]["MFML"] == {"quiz1": 4.5, "assignment1": 9, "midsem": 24}
    assert student["marks"]["ML"] == {"quiz1": 5}


def test_validation_errors(client: TestClient) -> None:
    assert _submit(client, bits_id="bad id!").status_code == 422
    assert _submit(client, marks={"NOPE": {"quiz1": 1}}).status_code == 422
    assert _submit(client, marks={"MFML": {"quiz1": 99}}).status_code == 422
    assert _submit(client, marks={"MFML": {"bogus": 1}}).status_code == 422
    assert _submit(client, pin="12").status_code == 422
    assert _submit(client, pin="abcd").status_code == 422
    assert client.get("/api/leaderboard", params={"term": "1999-S9"}).status_code == 404


def test_pin_protects_edits(client: TestClient) -> None:
    assert _submit(client, pin="4321").status_code == 200
    # wrong PIN → rejected
    resp = _submit(client, pin="0000", name="Attacker")
    assert resp.status_code == 403
    assert "PIN" in resp.json()["detail"]
    # correct PIN → allowed
    assert _submit(client, pin="4321", name="Niraj K").status_code == 200
    board = client.get("/api/leaderboard", params={"term": TERM}).json()
    assert board["students"][0]["name"] == "Niraj K"


def test_pin_never_leaks(client: TestClient) -> None:
    _submit(client)
    res = client.get("/api/student", params={"term": TERM, "bits_id": "2025AA05123"}).json()
    assert res["has_pin"] is True
    assert "pin_hash" not in res["student"]
    assert "pin_salt" not in res["student"]
    text = client.get("/api/leaderboard", params={"term": TERM}).text
    assert "pin_hash" not in text and "pin_salt" not in text
    text = client.get("/api/export.csv", params={"term": TERM}).text
    assert "pin" not in text.lower()


def test_anonymous_hides_identity_everywhere(client: TestClient, tmp_path: Path) -> None:
    resp = _submit(client, anonymous=True)
    assert resp.status_code == 200
    data = resp.json()
    assert data["anon"] is True and data["alias"] and data["id_hash"]

    # leaderboard: alias shown, no bits_id
    board = client.get("/api/leaderboard", params={"term": TERM})
    entry = board.json()["students"][0]
    assert entry["anon"] is True
    assert entry["bits_id"] is None
    assert entry["name"] == data["alias"]
    assert "2025AA05123" not in board.text and "Niraj" not in board.text

    # raw data file: identity never stored
    raw = (tmp_path / "marks" / f"{TERM}.json").read_text()
    assert "2025AA05123" not in raw and "Niraj" not in raw

    # CSV: no identity either
    csv_text = client.get("/api/export.csv", params={"term": TERM}).text
    assert "2025AA05123" not in csv_text and "Niraj" not in csv_text


def test_anon_lookup_requires_pin(client: TestClient) -> None:
    _submit(client, anonymous=True)
    # without / with wrong PIN: indistinguishable from unregistered
    no_pin = client.get("/api/student", params={"term": TERM, "bits_id": "2025AA05123"}).json()
    assert no_pin["found"] is False
    bad = client.get(
        "/api/student", params={"term": TERM, "bits_id": "2025AA05123"}, headers={"X-Pin": "0000"}
    ).json()
    assert bad["found"] is False
    good = client.get(
        "/api/student", params={"term": TERM, "bits_id": "2025AA05123"}, headers={"X-Pin": "1234"}
    ).json()
    assert good["found"] is True and good["anon"] is True
    assert "bits_id" not in good["student"] and "name" not in good["student"]


def test_toggle_anonymous_and_back(client: TestClient) -> None:
    _submit(client)  # public
    _submit(client, anonymous=True)  # go anonymous
    board = client.get("/api/leaderboard", params={"term": TERM}).json()
    assert board["students"][0]["anon"] is True
    _submit(client, anonymous=False, name="Niraj")  # back to public, same PIN
    board = client.get("/api/leaderboard", params={"term": TERM}).json()
    entry = board["students"][0]
    assert entry["anon"] is False and entry["bits_id"] == "2025AA05123"
    # marks survived the round-trip
    assert entry["subjects"]["MFML"]["total"] == 13.5


def test_legacy_record_without_pin_can_be_claimed(client: TestClient, tmp_path: Path) -> None:
    # simulate a record created before PINs existed
    import json

    marks_dir = tmp_path / "marks"
    marks_dir.mkdir(parents=True, exist_ok=True)
    (marks_dir / f"{TERM}.json").write_text(
        json.dumps({"students": [{"bits_id": "2025AA05123", "name": "Old", "marks": {}}]})
    )
    assert _submit(client, pin="9999").status_code == 200  # first edit claims the ID
    assert _submit(client, pin="1111").status_code == 403  # now locked


def test_csv_export(client: TestClient) -> None:
    _submit(client)
    resp = client.get("/api/export.csv", params={"term": TERM})
    assert resp.status_code == 200
    lines = resp.text.strip().splitlines()
    assert len(lines) == 2
    assert "MFML_quiz1" in lines[0]
    assert "2025AA05123" in lines[1]


def test_submit_retries_on_write_conflict(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent-commit conflict must trigger a fresh read-modify-write, not data loss."""
    conflicts = {"raised": 0}

    class FlakyStorage(LocalStorage):
        def write_marks(self, term: str, data: dict[str, Any], message: str) -> None:
            if conflicts["raised"] == 0:
                conflicts["raised"] += 1
                raise WriteConflictError(term)
            super().write_marks(term, data, message)

    storage = FlakyStorage(data_dir=tmp_path)
    monkeypatch.setattr(app_module, "get_storage", lambda: storage)
    assert _submit(client).status_code == 200
    assert conflicts["raised"] == 1
    board = client.get("/api/leaderboard", params={"term": TERM}).json()
    assert board["stats"]["total_students"] == 1


def test_index_served(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "BITS Marks Tracker" in resp.text
