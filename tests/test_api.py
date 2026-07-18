"""API tests using local file storage in a temp directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import bits_marks_tracker.app as app_module
from bits_marks_tracker.storage import LocalStorage

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
        "marks": {"MFML": {"quiz1": 4.5, "assignment1": 9}},
    }
    payload.update(overrides)
    return client.post("/api/submit", json=payload)


def test_submit_and_leaderboard(client: TestClient) -> None:
    resp = _submit(client)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "bits_id": "2025AA05123"}  # normalized

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
    assert client.get("/api/leaderboard", params={"term": "1999-S9"}).status_code == 404


def test_csv_export(client: TestClient) -> None:
    _submit(client)
    resp = client.get("/api/export.csv", params={"term": TERM})
    assert resp.status_code == 200
    lines = resp.text.strip().splitlines()
    assert len(lines) == 2
    assert "MFML_quiz1" in lines[0]
    assert "2025AA05123" in lines[1]


def test_index_served(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "BITS Marks Tracker" in resp.text
