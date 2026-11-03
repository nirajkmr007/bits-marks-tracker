"""Leaderboard math: totals, percentages, ranks and percentiles.

Marks arrive incrementally through the semester, so a student may only have a
subset of components filled in. Percentages are therefore computed against the
maximum of the *entered* components, which keeps mid-semester comparisons fair.

Percentile follows the exam convention: the percentage of ranked students whose
overall percentage is strictly below yours.
"""

from __future__ import annotations

from typing import Any


def subject_components(
    subject: dict[str, Any], term_config: dict[str, Any]
) -> list[dict[str, Any]]:
    """A subject's components (its own override, else the term-wide list)."""
    comps: list[dict[str, Any]] = subject.get("components", term_config["components"])
    return comps


def component_groups(subject: dict[str, Any], term_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Groups of components that count together towards a fixed total (e.g. EC-1)."""
    groups: list[dict[str, Any]] = subject.get(
        "component_groups", term_config.get("component_groups", [])
    )
    return groups


def component_factors(
    components: list[dict[str, Any]], groups: list[dict[str, Any]]
) -> dict[str, float]:
    """Weight applied to each component's marks.

    A group such as ``{"keys": [quiz1, assignment1, quiz2, assignment2], "scale_to": 30}``
    makes those components count out of 30 in total. If their raw maxima already
    add up to 30 the factor is 1; for ML (10+5+10+15 = 40) it is 30/40 = 0.75.
    Components outside any group count as entered (factor 1). The factor is
    linear, so partially entered groups stay fair.
    """
    by_key = {c["key"]: float(c["max"]) for c in components}
    factors = dict.fromkeys(by_key, 1.0)
    for group in groups:
        keys = [k for k in group.get("keys", []) if k in by_key]
        raw_max = sum(by_key[k] for k in keys)
        scale_to = group.get("scale_to")
        if keys and raw_max > 0 and scale_to:
            for k in keys:
                factors[k] = float(scale_to) / raw_max
    return factors


def subject_full_max(subject: dict[str, Any], term_config: dict[str, Any]) -> float:
    """Total marks a subject is out of, after group scaling (e.g. 100)."""
    comps = subject_components(subject, term_config)
    factors = component_factors(comps, component_groups(subject, term_config))
    return round(sum(float(c["max"]) * factors[c["key"]] for c in comps), 4)


def _subject_score(
    marks: dict[str, Any], components: list[dict[str, Any]], factors: dict[str, float]
) -> dict[str, Any]:
    total = 0.0
    max_entered = 0.0
    for comp in components:
        value = marks.get(comp["key"])
        if value is None:
            continue
        f = factors.get(comp["key"], 1.0)
        total += float(value) * f
        max_entered += float(comp["max"]) * f
    pct = round(total / max_entered * 100, 2) if max_entered > 0 else None
    return {"total": round(total, 2), "max_entered": round(max_entered, 2), "pct": pct}


def compute_leaderboard(
    term_config: dict[str, Any],
    marks_doc: dict[str, Any],
    flags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build ranked leaderboard entries plus dashboard stats for one term.

    ``flags`` maps a record ref (``bits_id`` for public rows, ``id_hash`` for
    hidden ones) to a reason. Flagged records are held out of ranking and stats
    and returned separately under ``under_review`` so bad/premature data can't
    skew anyone's percentile while the student corrects it.
    """
    flags = flags or {}
    subjects: list[dict[str, Any]] = term_config["subjects"]
    # Per subject: its components and the weight of each (group scaling, e.g. EC-1 → 30).
    plan = {
        s["code"]: (
            subject_components(s, term_config),
            component_factors(subject_components(s, term_config), component_groups(s, term_config)),
        )
        for s in subjects
    }

    entries: list[dict[str, Any]] = []
    under_review: list[dict[str, Any]] = []
    for student in marks_doc.get("students", []):
        # A flag can reference a record by bits_id, id_hash, or alias — whichever
        # the admin finds convenient (alias is the human-readable one shown in UI).
        matched = next(
            (
                r
                for r in (student.get("bits_id"), student.get("id_hash"), student.get("alias"))
                if r and r in flags
            ),
            None,
        )
        if matched is not None:
            anon = bool(student.get("anon"))
            under_review.append(
                {
                    "anon": anon,
                    "bits_id": None if anon else student.get("bits_id"),
                    "id_hash": student.get("id_hash") if anon else None,
                    "name": (
                        student.get("alias", "Anonymous")
                        if anon
                        else student.get("name") or student.get("bits_id", "?")
                    ),
                    "reason": flags[matched],
                    "updated_at": student.get("updated_at"),
                }
            )
            continue
        per_subject: dict[str, Any] = {}
        total = 0.0
        max_entered = 0.0
        for subject in subjects:
            code = subject["code"]
            # A subject may override the term-wide component structure
            # (e.g. ML: Quiz 1 = 10, Assignment 1 = 5) and group scaling applies.
            comps, factors = plan[code]
            score = _subject_score(student.get("marks", {}).get(code, {}), comps, factors)
            per_subject[code] = {
                **score,
                "components": student.get("marks", {}).get(code, {}),
                "grade": student.get("grades", {}).get(code),
            }
            total += score["total"]
            max_entered += score["max_entered"]
        overall_pct = round(total / max_entered * 100, 2) if max_entered > 0 else None
        anon = bool(student.get("anon"))
        fallback = student.get("alias", "Anonymous") if anon else student.get("bits_id", "?")
        entries.append(
            {
                "anon": anon,
                "bits_id": None if anon else student.get("bits_id"),
                "id_hash": student.get("id_hash") if anon else None,
                "name": student.get("name") or fallback,
                "updated_at": student.get("updated_at"),
                "cgpa": student.get("cgpa"),
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
        "under_review": under_review,
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
