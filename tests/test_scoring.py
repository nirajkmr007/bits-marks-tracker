"""Unit tests for leaderboard math."""

from __future__ import annotations

from typing import Any

from bits_marks_tracker.scoring import compute_leaderboard

TERM_CONFIG: dict[str, Any] = {
    "label": "Test Term",
    "subjects": [{"code": "ML", "name": "Machine Learning"}, {"code": "ISM", "name": "Stats"}],
    "components": [
        {"key": "quiz1", "label": "Quiz 1", "max": 5},
        {"key": "midsem", "label": "Midsem", "max": 30},
    ],
}


def _student(bits_id: str, name: str, marks: dict[str, Any]) -> dict[str, Any]:
    return {"bits_id": bits_id, "name": name, "marks": marks}


def test_partial_marks_use_entered_max() -> None:
    doc = {"students": [_student("2025AA05001", "Asha", {"ML": {"quiz1": 4}})]}
    result = compute_leaderboard(TERM_CONFIG, doc)
    entry = result["students"][0]
    assert entry["subjects"]["ML"]["total"] == 4
    assert entry["subjects"]["ML"]["max_entered"] == 5
    assert entry["subjects"]["ML"]["pct"] == 80.0
    assert entry["subjects"]["ISM"]["pct"] is None
    assert entry["overall"]["pct"] == 80.0
    assert entry["rank"] == 1
    assert entry["percentile"] is None  # single student — no percentile


def test_ranking_percentile_and_ties() -> None:
    doc = {
        "students": [
            _student("A1B2C3D4", "Top", {"ML": {"quiz1": 5}}),  # 100%
            _student("A1B2C3D5", "AlsoTop", {"ML": {"quiz1": 5}}),  # 100%
            _student("A1B2C3D6", "Mid", {"ML": {"quiz1": 4}}),  # 80%
            _student("A1B2C3D7", "Low", {"ML": {"quiz1": 1}}),  # 20%
            _student("A1B2C3D8", "Empty", {}),  # unranked
        ]
    }
    result = compute_leaderboard(TERM_CONFIG, doc)
    ranked = result["students"]
    assert [e["rank"] for e in ranked] == [1, 1, 3, 4, None]
    assert ranked[0]["percentile"] == 50.0  # 2 of 4 strictly below
    assert ranked[2]["percentile"] == 25.0
    assert ranked[3]["percentile"] == 0.0
    assert ranked[4]["percentile"] is None


def test_stats() -> None:
    doc = {
        "students": [
            _student("A1B2C3D4", "A", {"ML": {"quiz1": 5, "midsem": 25}}),
            _student("A1B2C3D5", "B", {"ML": {"quiz1": 3, "midsem": 15}}),
        ]
    }
    stats = compute_leaderboard(TERM_CONFIG, doc)["stats"]
    assert stats["total_students"] == 2
    assert stats["ranked_students"] == 2
    assert stats["subjects"]["ML"]["filled"] == 2
    assert stats["subjects"]["ML"]["top_total"] == 30
    assert stats["subjects"]["ML"]["top_pct"] == 85.71
    assert stats["subjects"]["ISM"]["filled"] == 0
    assert stats["overall"]["top_pct"] == 85.71
