#!/usr/bin/env python3
"""
verify_cycle.py — Did yesterday's full cycle run as designed?

Checks one campaign day end-to-end against the pre-registered requirements:
freeze happened (before kickoff, full lineup), every match got a result,
everything scoreable was scored, and the outputs are mutually consistent.

Run any morning (or via --date):
  python scripts/verify_cycle.py                # most recent finished campaign day
  python scripts/verify_cycle.py --date 2026-06-12

Exit 0 = all green. Exit 1 = at least one ❌ (a ⚠ alone does not fail —
it marks things that may legitimately still be in flight).
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from freeze import AI_LINEUP, group_by_utc8

ROOT = Path(os.environ.get("FORECASTING_ROOT") or Path(__file__).parent.parent)

OK, WARN, FAIL = "✅", "⚠️ ", "❌"
failures = 0
# Structured findings, collected alongside the human-readable lines so
# health_digest.py / ops_playbook.py can triage them. Each: {level, code, message}.
findings: list = []
_JSON_MODE = False

def report(level: str, msg: str, code: str = "") -> None:
    global failures
    if level == FAIL:
        failures += 1
    findings.append({"level": level.strip(), "code": code, "message": msg})
    if not _JSON_MODE:
        print(f"{level} {msg}")

def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def pick_day(day_arg: str | None):
    """Resolve which campaign day to verify; None if nothing finished yet."""
    now = datetime.now(timezone.utc)
    fixtures = json.loads((ROOT / "data/fixtures/fixtures.json").read_text())
    day_map = group_by_utc8(fixtures)
    if day_arg:
        return day_arg, day_map
    finished = [
        d for d, ms in day_map.items()
        if max(parse_utc(m["kickoff_utc"]) for m in ms) < now - timedelta(hours=3)
    ]
    return (max(finished) if finished else None), day_map

def run_checks(day_arg: str | None = None) -> dict:
    """Run all cycle checks for a campaign day. Populates module-level
    `findings`/`failures` and returns a structured summary dict. Callable by
    health_digest.py (no shelling out)."""
    global findings, failures
    findings, failures = [], 0
    now = datetime.now(timezone.utc)
    day, day_map = pick_day(day_arg)
    if day is None:
        return {"day": None, "findings": [], "failures": 0, "action_needed": False,
                "note": "No finished campaign days yet."}

    matches = [m for m in day_map.get(day, []) if not m.get("is_placeholder")]
    if not matches:
        report(WARN, f"no non-placeholder fixtures for {day}", "no_fixtures")
        return _summary(day)

    # ── 1. Freeze ─────────────────────────────────────────────────────────────
    pred_path = ROOT / "data" / "predictions" / f"{day}.json"
    frozen: dict = {}
    if not pred_path.exists():
        report(FAIL, f"freeze: {pred_path.name} missing — the matchday was never frozen",
               "freeze_missing")
    else:
        pred = json.loads(pred_path.read_text())
        freeze_utc = parse_utc(pred["freeze_utc"])
        frozen = {m["match_id"]: m["forecasts"] for m in pred["matches"]}
        late = [mid for mid in frozen
                if mid in {m["match_id"]: m for m in matches}
                and parse_utc(next(x for x in matches if x["match_id"] == mid)["kickoff_utc"]) <= freeze_utc]
        if late:
            report(FAIL, f"freeze: committed AFTER kickoff for {late} — void, investigate",
                   "freeze_late")
        else:
            report(OK, f"freeze: {len(frozen)} matches frozen at {pred['freeze_utc']} (pre-kickoff)",
                   "freeze_ok")

        missing_fixtures = [m["match_id"] for m in matches if m["match_id"] not in frozen]
        if missing_fixtures:
            report(WARN, f"freeze: fixtures not in the freeze (void that day): {missing_fixtures}",
                   "freeze_partial")

        for mid, fc in frozen.items():
            absent = [a for a in AI_LINEUP if a not in fc]
            failed = [a for a in AI_LINEUP if fc.get(a, {}).get("status") == "failed"]
            if absent:
                report(FAIL, f"freeze/{mid}: lineup models absent entirely: {absent}",
                       "model_absent")
            if failed:
                report(WARN, f"freeze/{mid}: models failed (logged, allowed): {failed}",
                       "model_failed")
            if "market" not in fc:
                report(WARN, f"freeze/{mid}: no market odds were matched", "market_missing")
        n_humans = len({k for fc in frozen.values() for k in fc if k.startswith("human:")})
        report(OK if n_humans else WARN, f"freeze: {n_humans} human forecasters",
               "humans_ok" if n_humans else "humans_none")

    # ── 2. Results ────────────────────────────────────────────────────────────
    res_path = ROOT / "data" / "results" / "results.json"
    results = {r["match_id"]: r for r in json.loads(res_path.read_text())} if res_path.exists() else {}
    for m in matches:
        mid = m["match_id"]
        r = results.get(mid)
        kicked_off_h = (now - parse_utc(m["kickoff_utc"])).total_seconds() / 3600
        if r is None:
            if kicked_off_h < 0:
                report(WARN, f"results/{mid}: kicks off in {-kicked_off_h:.0f}h — nothing expected yet",
                       "result_pending")
            elif kicked_off_h > 14:
                report(FAIL, f"results/{mid}: no result {kicked_off_h:.0f}h after kickoff "
                             f"— add manually to results.json + rerun score.py", "results_missing_late")
            else:
                report(WARN, f"results/{mid}: no result yet ({kicked_off_h:.0f}h since kickoff, may still be in flight)",
                       "result_in_flight")
        elif r.get("status") == "FT":
            report(OK, f"results/{mid}: {r['score_home']}–{r['score_away']} ({r['outcome']})", "result_ok")
        else:
            report(WARN, f"results/{mid}: status {r.get('status')} — excluded from scoring", "result_excluded")

    # ── 3. Scoring consistency ────────────────────────────────────────────────
    lb_path = ROOT / "data" / "scores" / "leaderboard.json"
    if not lb_path.exists():
        report(FAIL, "scores: leaderboard.json missing", "leaderboard_missing")
    else:
        lb = json.loads(lb_path.read_text())
        all_pred_ids = set()
        for p in sorted((ROOT / "data" / "predictions").glob("*.json")):
            all_pred_ids |= {m["match_id"] for m in json.loads(p.read_text())["matches"]}
        expected_scored = sum(
            1 for mid, r in results.items()
            if r.get("status") == "FT" and r.get("outcome") in ("home", "draw", "away")
            and mid in all_pred_ids
        )
        if lb["matches_scored"] == expected_scored:
            report(OK, f"scores: matches_scored = {expected_scored} (matches raw data exactly)", "scored_ok")
        else:
            report(FAIL, f"scores: leaderboard says {lb['matches_scored']} scored but raw data "
                         f"implies {expected_scored} — re-run score.py", "scored_mismatch")
        day_results = [results.get(m["match_id"]) for m in matches]
        day_ft = [r for r in day_results if r and r.get("status") == "FT"]
        if day_ft:
            updated = parse_utc(lb["updated_utc"])
            last_ko = max(parse_utc(m["kickoff_utc"]) for m in matches)
            if updated > last_ko:
                report(OK, f"scores: leaderboard updated {lb['updated_utc']} (after the day's last kickoff)",
                       "leaderboard_fresh")
            else:
                report(FAIL, f"scores: leaderboard timestamp {lb['updated_utc']} predates the "
                             f"day's matches — score.yml has not run since; trigger it", "leaderboard_stale")
        cal_path = ROOT / "data" / "scores" / "calibration.json"
        if cal_path.exists() and lb["matches_scored"] > 0:
            cal = json.loads(cal_path.read_text())
            lb_names = {r["forecaster"] for r in lb["rows"]} - {"uniform"}
            missing = lb_names - set(cal)
            if missing:
                report(FAIL, f"scores: forecasters on leaderboard but missing from calibration: {missing}",
                       "calibration_gap")
            else:
                report(OK, f"scores: calibration covers all {len(lb_names)} scored forecasters",
                       "calibration_ok")

    return _summary(day)

def _summary(day: str) -> dict:
    return {
        "day": day,
        "findings": list(findings),
        "failures": failures,
        "action_needed": failures > 0,
    }

def main() -> None:
    global _JSON_MODE
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Campaign day YYYY-MM-DD; default = most recent day whose matches have all kicked off")
    parser.add_argument("--json", action="store_true", help="Emit structured findings as JSON")
    args = parser.parse_args()
    _JSON_MODE = args.json

    day, _ = pick_day(args.date)
    if day is None:
        if args.json:
            print(json.dumps({"day": None, "findings": [], "failures": 0,
                              "action_needed": False, "note": "No finished campaign days yet."}))
        else:
            print("No finished campaign days yet — nothing to verify.")
        return

    if not args.json:
        print(f"── Verifying campaign day {day} ({len([m for m in group_by_utc8(json.loads((ROOT/'data/fixtures/fixtures.json').read_text())).get(day, []) if not m.get('is_placeholder')])} matches) ──\n")

    summary = run_checks(args.date)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        n = summary["failures"]
        print("\n" + ("ALL CHECKS PASSED ✅" if n == 0 else f"{n} FAILURE(S) — see ❌ above"))
    sys.exit(1 if summary["failures"] else 0)

if __name__ == "__main__":
    main()
