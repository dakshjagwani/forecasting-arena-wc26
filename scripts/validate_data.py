#!/usr/bin/env python3
"""
validate_data.py — Repo-wide /data tree validator (TESTING.md §3.2).

Run standalone or as the last step of freeze.yml / score.yml / test.yml.
Exit code 0 = clean; 1 = at least one violation (printed to stderr).

Checks:
  fixtures.json     — unique match_ids, required fields, parseable UTC kickoffs
  predictions/*.json— schema, triples sum to 1, freeze before kickoff,
                      forecasters within the frozen lineup, no placeholders
  results.json      — schema, outcome consistent with score, referential integrity
  scores/*.json     — leaderboard/calibration sanity, no NaN/None where numeric
"""
from __future__ import annotations
import json, math, os, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from freeze import AI_LINEUP, NON_AI_FORECASTERS

ROOT = Path(os.environ.get("FORECASTING_ROOT") or Path(__file__).parent.parent)
DATA = ROOT / "data"

errors: list = []

def err(msg: str) -> None:
    errors.append(msg)

def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def is_prob(x) -> bool:
    return isinstance(x, (int, float)) and not math.isnan(x) and 0 < x < 1

# ── fixtures ──────────────────────────────────────────────────────────────────
def check_fixtures() -> dict:
    path = DATA / "fixtures" / "fixtures.json"
    fixtures = json.loads(path.read_text())
    ids = [f.get("match_id") for f in fixtures]
    if len(ids) != len(set(ids)):
        err("fixtures: duplicate match_ids")
    for f in fixtures:
        mid = f.get("match_id", "<missing>")
        for k in ("stage", "venue", "kickoff_utc"):
            if not f.get(k):
                err(f"fixtures/{mid}: missing field {k!r}")
        if "is_placeholder" not in f:
            err(f"fixtures/{mid}: missing is_placeholder flag")
        # Teams may be unknown only while the fixture is flagged placeholder
        elif not f["is_placeholder"]:
            for k in ("home", "away"):
                if not f.get(k):
                    err(f"fixtures/{mid}: missing {k!r} on a non-placeholder fixture")
        try:
            parse_utc(f["kickoff_utc"])
        except (KeyError, ValueError):
            err(f"fixtures/{mid}: unparseable kickoff_utc")
    return {f["match_id"]: f for f in fixtures if "match_id" in f}

# ── predictions ───────────────────────────────────────────────────────────────
def check_predictions(fixture_map: dict) -> set:
    allowed = set(AI_LINEUP) | set(NON_AI_FORECASTERS)
    predicted_ids = set()
    for path in sorted((DATA / "predictions").glob("*.json")):
        try:
            day = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            err(f"{path.name}: invalid JSON ({e})")
            continue
        if day.get("date") != path.stem:
            err(f"{path.name}: internal date {day.get('date')!r} != filename")
        try:
            freeze_utc = parse_utc(day["freeze_utc"])
        except (KeyError, ValueError):
            err(f"{path.name}: missing/unparseable freeze_utc")
            freeze_utc = None
        for m in day.get("matches", []):
            mid = m.get("match_id", "<missing>")
            predicted_ids.add(mid)
            fx = fixture_map.get(mid)
            if fx is None:
                err(f"{path.name}/{mid}: match_id not in fixtures.json")
            else:
                if fx.get("is_placeholder"):
                    err(f"{path.name}/{mid}: frozen forecast for a placeholder fixture")
                if freeze_utc and parse_utc(fx["kickoff_utc"]) <= freeze_utc:
                    err(f"{path.name}/{mid}: freeze_utc is not before kickoff")
            for fc_id, fc in m.get("forecasts", {}).items():
                if fc_id not in allowed and not fc_id.startswith("human:"):
                    err(f"{path.name}/{mid}: forecaster {fc_id!r} outside frozen lineup")
                if fc.get("status") == "failed":
                    continue
                triple = [fc.get("p_home"), fc.get("p_draw"), fc.get("p_away")]
                if not all(is_prob(p) for p in triple):
                    err(f"{path.name}/{mid}/{fc_id}: invalid probability triple {triple}")
                elif abs(sum(triple) - 1.0) > 1e-4:
                    err(f"{path.name}/{mid}/{fc_id}: triple sums to {sum(triple):.6f}")
    return predicted_ids

# ── results ───────────────────────────────────────────────────────────────────
def check_results(fixture_map: dict) -> None:
    path = DATA / "results" / "results.json"
    if not path.exists():
        return
    results = json.loads(path.read_text())
    seen = set()
    for r in results:
        mid = r.get("match_id", "<missing>")
        if mid in seen:
            err(f"results/{mid}: duplicate entry")
        seen.add(mid)
        if mid not in fixture_map:
            err(f"results/{mid}: match_id not in fixtures.json")
        if r.get("status") not in ("FT", "ABANDONED", "POSTPONED"):
            err(f"results/{mid}: unknown status {r.get('status')!r}")
        if r.get("status") != "FT":
            continue
        sh, sa, outcome = r.get("score_home"), r.get("score_away"), r.get("outcome")
        if not (isinstance(sh, int) and isinstance(sa, int) and sh >= 0 and sa >= 0):
            err(f"results/{mid}: invalid score {sh}-{sa}")
            continue
        expected = "home" if sh > sa else "away" if sa > sh else "draw"
        if outcome != expected:
            err(f"results/{mid}: outcome {outcome!r} inconsistent with score {sh}-{sa}")

# ── scores ────────────────────────────────────────────────────────────────────
def check_scores() -> None:
    lb_path = DATA / "scores" / "leaderboard.json"
    if lb_path.exists():
        lb = json.loads(lb_path.read_text())
        for row in lb.get("rows", []):
            f = row.get("forecaster", "<missing>")
            mb = row.get("mean_brier")
            if mb is None or (isinstance(mb, float) and math.isnan(mb)) or not 0 <= mb <= 2:
                err(f"leaderboard/{f}: mean_brier out of range: {mb!r}")
            if row.get("type") not in ("market", "crowd", "baseline", "human", "ai"):
                err(f"leaderboard/{f}: unknown type {row.get('type')!r}")
            if not isinstance(row.get("n_predicted"), int) or row["n_predicted"] < 1:
                err(f"leaderboard/{f}: invalid n_predicted")
    cal_path = DATA / "scores" / "calibration.json"
    if cal_path.exists():
        cal = json.loads(cal_path.read_text())
        for f, entry in cal.items():
            for b in entry.get("buckets", []):
                if b["n"] > 0 and not (b["actual_freq"] is not None and 0 <= b["actual_freq"] <= 1):
                    err(f"calibration/{f}: bucket {b['lo']}-{b['hi']} bad actual_freq")
                if b["n"] == 0 and b["actual_freq"] is not None:
                    err(f"calibration/{f}: empty bucket has non-null actual_freq")

def main() -> None:
    fixture_map = check_fixtures()
    check_predictions(fixture_map)
    check_results(fixture_map)
    check_scores()
    if errors:
        for e in errors:
            print(f"VIOLATION: {e}", file=sys.stderr)
        print(f"\nvalidate_data: {len(errors)} violation(s)", file=sys.stderr)
        sys.exit(1)
    print("validate_data: clean ✓")

if __name__ == "__main__":
    main()
