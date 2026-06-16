#!/usr/bin/env python3
"""
ops_playbook.py — turn verify_cycle findings into a triaged verdict.

Maps each finding `code` (from verify_cycle.run_checks) to:
  verdict  : "healthy" | "auto" | "action"
  why      : plain-English explanation for a non-expert
  action   : what to do (only meaningful when verdict == "action")

A finding whose code is NOT in the playbook is treated as "unknown" → needs a
human (so a novel failure is never silently swallowed). This module is pure
data + functions: no I/O, fully unit-testable.
"""
from __future__ import annotations

# verdict precedence for the overall digest: action > unknown > auto > healthy
_RANK = {"healthy": 0, "auto": 1, "unknown": 2, "action": 3}

PLAYBOOK: dict = {
    # ── healthy: everything as designed ──────────────────────────────────────
    "freeze_ok":        ("healthy", "Predictions frozen before kickoff.", ""),
    "humans_ok":        ("healthy", "Human picks were ingested.", ""),
    "result_ok":        ("healthy", "Final score recorded.", ""),
    "scored_ok":        ("healthy", "Leaderboard count matches the raw data.", ""),
    "leaderboard_fresh":("healthy", "Leaderboard updated after the last kickoff.", ""),
    "calibration_ok":   ("healthy", "Calibration covers every scored forecaster.", ""),

    # ── auto: expected/absorbed, NO action needed ────────────────────────────
    "model_failed":     ("auto", "A model returned no valid answer for a match "
                         "(transient free-tier error). It's recorded 'failed' and "
                         "just isn't scored on that match.", ""),
    "market_missing":   ("auto", "No bookmaker odds matched this fixture; the "
                         "market simply isn't scored on it.", ""),
    "result_pending":   ("auto", "Match hasn't kicked off yet — nothing expected.", ""),
    "result_in_flight": ("auto", "Result not in yet but the match finished recently "
                         "— the next scoring run should pick it up.", ""),
    "result_excluded":  ("auto", "Match abandoned/postponed — excluded from scoring "
                         "by design.", ""),
    "humans_none":      ("auto", "Nobody submitted human picks for this day "
                         "(fine if expected; nudge the group otherwise).", ""),
    "freeze_partial":   ("auto", "Some fixtures weren't in the freeze (e.g. still "
                         "placeholders); they're void for everyone that day.", ""),
    "no_fixtures":      ("auto", "No real fixtures this campaign day (rest day or "
                         "placeholder-only).", ""),

    # ── action: a human must do something ────────────────────────────────────
    "freeze_missing":   ("action", "The matchday was never frozen.",
                         "Trigger the freeze NOW (Actions → Daily freeze → Run "
                         "workflow), before the first kickoff."),
    "freeze_late":      ("action", "A forecast was committed AFTER kickoff — it is "
                         "void and must not be scored.",
                         "Investigate; remove/void the late entry per the incident "
                         "playbook (CLAUDE.md §14.3)."),
    "model_absent":     ("action", "A lineup model is missing from a match entirely "
                         "(not even a 'failed' record) — unexpected.",
                         "Check the freeze run logs for that model."),
    "results_missing_late": ("action", "A match finished >14h ago but still has no "
                         "result — scoring is stuck on it.",
                         "Enter the score in data/results/results.json and rerun "
                         "score.py (or trigger Daily score)."),
    "leaderboard_missing":  ("action", "leaderboard.json is missing.",
                         "Run score.py / trigger Daily score."),
    "scored_mismatch":  ("action", "The leaderboard's match count disagrees with the "
                         "raw results — scoring is out of date.",
                         "Rerun score.py / trigger Daily score."),
    "leaderboard_stale":("action", "The leaderboard timestamp predates today's "
                         "matches — scoring hasn't run since.",
                         "Trigger Daily score."),
    "calibration_gap":  ("action", "A scored forecaster is missing from calibration "
                         "— data inconsistency.",
                         "Rerun score.py; if it persists, investigate."),
}

def triage_finding(code: str) -> tuple:
    """(verdict, why, action) for a finding code; unknown codes → needs a human."""
    if code in PLAYBOOK:
        return PLAYBOOK[code]
    return ("unknown", "Unrecognised condition — not in the playbook.",
            "Bring this to a human/Claude with the evidence pack.")

def classify(summary: dict) -> dict:
    """Triage a verify_cycle summary into an overall verdict + per-finding notes.
    Returns {overall, action_items, auto_items, unknown_items, healthy_count}."""
    overall = "healthy"
    action_items, auto_items, unknown_items, healthy = [], [], [], 0
    for f in summary.get("findings", []):
        verdict, why, action = triage_finding(f.get("code", ""))
        if _RANK[verdict] > _RANK[overall]:
            overall = verdict
        entry = {"code": f.get("code", ""), "message": f.get("message", ""),
                 "why": why, "action": action}
        if verdict == "action":
            action_items.append(entry)
        elif verdict == "unknown":
            unknown_items.append(entry)
        elif verdict == "auto":
            auto_items.append(entry)
        else:
            healthy += 1
    return {
        "overall": overall,
        "action_items": action_items,
        "unknown_items": unknown_items,
        "auto_items": auto_items,
        "healthy_count": healthy,
    }
