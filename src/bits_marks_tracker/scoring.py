"""Leaderboard math: totals, percentages, ranks and percentiles.

Marks arrive incrementally through the semester, so a student may only have a
subset of components filled in. Percentages are therefore computed against the
maximum of the *entered* components, which keeps mid-semester comparisons fair.

Percentile follows the exam convention: the percentage of ranked students whose
overall percentage is strictly below yours.
"""

from __future__ import annotations

from typing import Any


def _subject_score(marks: dict[str, Any], components: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0.0
    max_entered = 0.0
    for comp in components:
        value = marks.get(comp["key"])
        if value is None:
            continue
        total += float(value)
        max_entered += float(comp["max"])
    pct = round(total / max_entered * 100, 2) if max_entered > 0 else None
    return {"total": round(total, 2), "max_entered": max_entered, "pct": pct}


def compute_leaderboard(term_config: dict[str, Any], marks_doc: dict[str, Any]) -> dict[str, Any]:
    """Build ranked leaderboard entries plus dashboard stats for one term."""
    subjects: list[dict[str, Any]] = term_config["subjects"]
    components: list[dict[str, Any]] = term_config["components"]

    entries: list[dict[str, Any]] = []
    for student in marks_doc.get("students", []):
        per_subject: dict[str, Any] = {}
        total = 0.0
        max_entered = 0.0
        for subject in subjects:
            code = subject["code"]
            score = _subject_score(student.get("marks", {}).get(code, {}), components)
            per_subject[code] = {
                **score,
                "components": student.get("marks", {}).get(code, {}),
            }
            total += score["total"]
            max_entered += score["max_entered"]
        overall_pct = round(total / max_entered * 100, 2) if max_entered > 0 else None
        entries.append(
            {
                "bits_id": student["bits_id"],
                "name": student["name"],
                "updated_at": student.get("updated_at"),
                "subjects": per_subject,
                "overall": {
                    "total": round(total, 2),
                    "max_entered": max_entered,
                    "pct": overall_pct,
                },
            }
        )

    ranked = [e for e in entries if e["overall"]["pct"] is not None]
    unranked = [e for e in entries if e["overall"]["pct"] is None]
    ranked.sort(key=lambda e: (-e["overall"]["pct"], e["name"].lower()))

    n = len(ranked)
    for index, entry in enumerate(ranked):
        # Competition ranking: equal percentages share the same rank (1, 1, 3, ...).
        if index > 0 and entry["overall"]["pct"] == ranked[index - 1]["overall"]["pct"]:
            entry["rank"] = ranked[index - 1]["rank"]
        else:
            entry["rank"] = index + 1
        below = sum(1 for other in ranked if other["overall"]["pct"] < entry["overall"]["pct"])
        entry["percentile"] = round(below / n * 100, 2) if n > 1 else None
    for entry in unranked:
        entry["rank"] = None
        entry["percentile"] = None

    return {
        "students": ranked + unranked,
        "stats": _stats(subjects, ranked, len(entries)),
    }


def _stats(
    subjects: list[dict[str, Any]], ranked: list[dict[str, Any]], total_students: int
) -> dict[str, Any]:
    subject_stats: dict[str, Any] = {}
    for subject in subjects:
        code = subject["code"]
        scored = [e["subjects"][code] for e in ranked if e["subjects"][code]["pct"] is not None]
        subject_stats[code] = {
            "filled": len(scored),
            "top_total": max((s["total"] for s in scored), default=None),
            "top_pct": max((s["pct"] for s in scored), default=None),
            "avg_pct": (round(sum(s["pct"] for s in scored) / len(scored), 2) if scored else None),
        }
    overall_pcts = [e["overall"]["pct"] for e in ranked]
    return {
        "total_students": total_students,
        "ranked_students": len(ranked),
        "overall": {
            "top_pct": max(overall_pcts, default=None),
            "avg_pct": round(sum(overall_pcts) / len(overall_pcts), 2) if overall_pcts else None,
        },
        "subjects": subject_stats,
    }
