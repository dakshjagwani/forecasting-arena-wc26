#!/usr/bin/env python3
"""
score.py — Post-match Brier scoring.

Reads data/predictions/*.json + data/results/results.json,
computes multiclass Brier scores, writes:
  data/scores/leaderboard.json
  data/scores/calibration.json

Idempotent: safe to re-run after correcting results.json.
"""
import json, math, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# FORECASTING_ROOT lets the golden-file integration test point this script at
# a synthetic data tree (TESTING.md §3.3). Defaults to the repo root.
ROOT = Path(os.environ.get("FORECASTING_ROOT") or Path(__file__).parent.parent)

# ── Scoring functions (tested in tests/test_scoring.py) ──────────────────────
def brier(p_home: float, p_draw: float, p_away: float, outcome: str) -> float:
    """Multiclass Brier score. outcome in {'home', 'draw', 'away'}. Range [0, 2]."""
    y = {"home": (1, 0, 0), "draw": (0, 1, 0), "away": (0, 0, 1)}[outcome]
    return sum((pi - yi) ** 2 for pi, yi in zip((p_home, p_draw, p_away), y))

def outcome_from_score(score_home: int, score_away: int) -> str:
    if score_home > score_away:
        return "home"
    if score_away > score_home:
        return "away"
    return "draw"

def forecaster_type(name: str) -> str:
    if name == "market":
        return "market"
    if name == "crowd":
        return "crowd"
    if name == "uniform":
        return "baseline"
    if name.startswith("human:"):
        return "human"
    return "ai"

# ── Per-match boards (display-only; the cumulative leaderboard is the
#    official pre-registered ranking) ─────────────────────────────────────────
def build_match_scores(scoreable: list, all_preds: dict, fixture_map: dict) -> dict:
    """{campaign_day: [match blocks with per-forecaster Brier, sorted]}"""
    days: dict = {}
    for match_id, result in scoreable:
        f = fixture_map[match_id]
        ko = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
        day = (ko - timedelta(hours=8)).strftime("%Y-%m-%d")
        rows = []
        for fc_name, fc in all_preds[match_id].items():
            if fc.get("status") == "failed":
                continue
            rows.append({
                "forecaster": fc_name,
                "type": forecaster_type(fc_name),
                "p_home": fc["p_home"], "p_draw": fc["p_draw"], "p_away": fc["p_away"],
                "brier": round(brier(fc["p_home"], fc["p_draw"], fc["p_away"],
                                     result["outcome"]), 4),
            })
        rows.sort(key=lambda r: (r["brier"], r["forecaster"]))
        days.setdefault(day, []).append({
            "match_id": match_id,
            "home": f.get("home"), "away": f.get("away"), "stage": f.get("stage"),
            "kickoff_utc": f["kickoff_utc"],
            "score": f"{result['score_home']}-{result['score_away']}",
            "outcome": result["outcome"],
            "rows": rows,
        })
    for day in days:
        days[day].sort(key=lambda m: m["kickoff_utc"])
    return days

# ── Load all predictions ──────────────────────────────────────────────────────
def load_all_predictions() -> dict:
    """Returns {match_id: {forecaster: forecast_dict}}."""
    pred_dir = ROOT / "data" / "predictions"
    all_preds: dict[str, dict] = {}
    for path in sorted(pred_dir.glob("*.json")):
        try:
            day = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"Warning: could not parse {path}: {e}", file=sys.stderr)
            continue
        for m in day.get("matches", []):
            all_preds[m["match_id"]] = m["forecasts"]
    return all_preds

# ── Qualification rule (pre-registered — CLAUDE.md §7) ───────────────────────
def is_qualified(n_predicted: int, n_available: int) -> bool:
    """
    A forecaster qualifies if they predicted >= 60% of the matches available
    since their first submission (n_available = scored matches from their
    first predicted match onwards, in kickoff order).
    """
    if n_available == 0:
        return False
    return n_predicted >= math.ceil(0.6 * n_available)

# ── Calibration buckets ───────────────────────────────────────────────────────
_BUCKETS = [(i / 10, (i + 1) / 10) for i in range(10)]

def build_calibration(all_preds: dict, results: dict) -> dict:
    """Returns {forecaster: {buckets: [{lo, hi, n, actual_freq, predicted_mid}]}}"""
    cal: dict[str, list] = {}  # forecaster -> [[lo, hi, n, hit_sum], ...]

    outcome_key = {"home": "p_home", "draw": "p_draw", "away": "p_away"}

    for match_id, result in results.items():
        if result.get("status") != "FT":
            continue
        outcome = result.get("outcome")
        if outcome not in outcome_key or match_id not in all_preds:
            continue

        for forecaster, fc in all_preds[match_id].items():
            if fc.get("status") == "failed":
                continue
            buckets = cal.setdefault(
                forecaster, [[lo, hi, 0, 0.0] for lo, hi in _BUCKETS]
            )
            for prob_key in ("p_home", "p_draw", "p_away"):
                predicted = fc.get(prob_key, 0.0)
                actual = 1.0 if outcome_key[outcome] == prob_key else 0.0
                for b in buckets:
                    if b[0] <= predicted < b[1]:
                        b[2] += 1
                        b[3] += actual
                        break

    return {
        f: {
            "buckets": [
                {
                    "lo": b[0], "hi": b[1],
                    "n": b[2],
                    "actual_freq": round(b[3] / b[2], 4) if b[2] > 0 else None,
                    "predicted_mid": round((b[0] + b[1]) / 2, 2),
                }
                for b in buckets
            ]
        }
        for f, buckets in cal.items()
    }

# ── Post-score gate ───────────────────────────────────────────────────────────
def validate_leaderboard(leaderboard: dict, all_preds: dict) -> None:
    rows = leaderboard["rows"]
    for row in rows:
        for k in ("forecaster", "type", "n_predicted", "mean_brier", "qualified"):
            assert k in row, f"Missing key {k!r} in leaderboard row"
        assert row["mean_brier"] is not None, "mean_brier is None"
        assert 0 <= row["mean_brier"] <= 2, f"mean_brier out of range: {row['mean_brier']}"

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    # Load fixtures (for referential integrity check + kickoff ordering)
    fixtures_path = ROOT / "data" / "fixtures" / "fixtures.json"
    fixtures = json.loads(fixtures_path.read_text())
    fixture_ids = {m["match_id"] for m in fixtures}
    kickoff_of = {m["match_id"]: m["kickoff_utc"] for m in fixtures}

    # Load results
    results_path = ROOT / "data" / "results" / "results.json"
    if not results_path.exists():
        print("No results yet — nothing to score", file=sys.stderr)
        sys.exit(0)
    results_raw = json.loads(results_path.read_text())
    results = {r["match_id"]: r for r in results_raw}

    # Referential integrity
    for mid in results:
        if mid not in fixture_ids:
            print(f"Warning: result {mid!r} not in fixtures — skipping", file=sys.stderr)

    # Load predictions
    all_preds = load_all_predictions()

    # Scoreable matches in kickoff order (drives the qualification rule)
    scoreable = sorted(
        (
            (mid, r) for mid, r in results.items()
            if r.get("status") == "FT"
            and r.get("outcome") in ("home", "draw", "away")
            and mid in all_preds
        ),
        key=lambda t: kickoff_of.get(t[0], ""),
    )
    total_scored = len(scoreable)

    forecaster_stats: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "sum_brier": 0.0, "first_idx": None}
    )

    for idx, (match_id, result) in enumerate(scoreable):
        for forecaster, fc in all_preds[match_id].items():
            if fc.get("status") == "failed":
                continue
            b = brier(fc["p_home"], fc["p_draw"], fc["p_away"], result["outcome"])
            s = forecaster_stats[forecaster]
            s["n"] += 1
            s["sum_brier"] += b
            if s["first_idx"] is None:
                s["first_idx"] = idx

    # Uniform baseline scored over the same matches as everyone else
    uni = forecaster_stats["uniform"]
    uni["first_idx"] = 0
    for _, result in scoreable:
        uni["n"] += 1
        uni["sum_brier"] += brier(1 / 3, 1 / 3, 1 / 3, result["outcome"])

    # Build leaderboard rows
    rows = []
    market_brier = None

    REFERENCE = {"market", "crowd", "uniform"}

    for forecaster, s in sorted(forecaster_stats.items()):
        n = s["n"]
        if n == 0:
            continue
        mean_b = round(s["sum_brier"] / n, 4)

        ftype = forecaster_type(forecaster)
        if forecaster == "market":
            market_brier = mean_b

        n_available = total_scored - (s["first_idx"] or 0)
        qualified = (
            True if forecaster in REFERENCE
            else is_qualified(n, n_available)
        )

        rows.append({
            "forecaster": forecaster,
            "type": ftype,
            "n_predicted": n,
            "mean_brier": mean_b,
            "vs_market": None,
            "qualified": qualified,
        })

    # Fill vs_market
    for row in rows:
        if market_brier is not None and row["forecaster"] != "market":
            row["vs_market"] = round(row["mean_brier"] - market_brier, 4)

    # Sort: qualified first, then ascending Brier
    rows.sort(key=lambda r: (not r["qualified"], r["mean_brier"]))

    leaderboard = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "matches_scored": total_scored,
        "rows": rows,
    }

    validate_leaderboard(leaderboard, all_preds)

    # Build calibration + per-match boards
    calibration = build_calibration(all_preds, results)
    fixture_map = {m["match_id"]: m for m in fixtures}
    match_scores = {
        "updated_utc": leaderboard["updated_utc"],
        "days": build_match_scores(scoreable, all_preds, fixture_map),
    }

    # Write outputs
    scores_dir = ROOT / "data" / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)

    (scores_dir / "leaderboard.json").write_text(json.dumps(leaderboard, indent=2))
    (scores_dir / "calibration.json").write_text(json.dumps(calibration, indent=2))
    (scores_dir / "match_scores.json").write_text(json.dumps(match_scores, indent=2))

    print(
        f"Scored {total_scored} matches | "
        f"{len(rows)} forecasters | "
        f"leaderboard + calibration + match boards written"
    )


if __name__ == "__main__":
    main()
