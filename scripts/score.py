#!/usr/bin/env python3
"""
score.py — Post-match Brier scoring.

Reads data/predictions/*.json + data/results/results.json,
computes multiclass Brier scores, writes:
  data/scores/leaderboard.json
  data/scores/calibration.json

Idempotent: safe to re-run after correcting results.json.
"""
from __future__ import annotations
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

def is_knockout(stage: str) -> bool:
    """Any stage that isn't a group is a knockout (Round of 32 … Final, plus the
    Third Place Playoff). Avoids enumerating the exact knockout labels."""
    return bool(stage) and not stage.startswith("Group")

# ── Per-match boards (display-only; the cumulative leaderboard is the
#    official pre-registered ranking) ─────────────────────────────────────────
def build_match_scores(scoreable: list, all_preds: dict, fixture_map: dict) -> dict:
    """{campaign_day: [match blocks with per-forecaster Brier, sorted]}"""
    days: dict = {}
    for match_id, result in scoreable:
        f = fixture_map[match_id]
        ko = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
        day = (ko - timedelta(hours=8)).strftime("%Y-%m-%d")
        # Knockout matches with a known advancer are scored/shown on advancement;
        # everything else on the 90-min 3-way result.
        advanced = result.get("advanced") if is_knockout(f.get("stage", "")) else None
        rows = []
        for fc_name, fc in all_preds[match_id].items():
            if fc.get("status") == "failed":
                continue
            row = {
                "forecaster": fc_name,
                "type": forecaster_type(fc_name),
                "p_home": fc["p_home"], "p_draw": fc["p_draw"], "p_away": fc["p_away"],
            }
            if advanced:
                p_adv = advancement_prob(fc)
                row["p_advance"] = round(p_adv, 4)              # P(home advances)
                row["brier"] = round(advancement_brier(p_adv, advanced), 4)
            else:
                row["brier"] = round(brier(fc["p_home"], fc["p_draw"], fc["p_away"],
                                           result["outcome"]), 4)
            rows.append(row)
        rows.sort(key=lambda r: (r["brier"], r["forecaster"]))
        block = {
            "match_id": match_id,
            "home": f.get("home"), "away": f.get("away"), "stage": f.get("stage"),
            "kickoff_utc": f["kickoff_utc"],
            "score": f"{result['score_home']}-{result['score_away']}",
            "outcome": result["outcome"],
            "rows": rows,
        }
        if advanced:
            block["metric"] = "advancement"
            block["advanced"] = advanced          # "home" / "away"
            block["decided_by"] = result.get("decided_by", "regular")
            if result.get("pens"):
                block["pens"] = result["pens"]
            if result.get("final_score"):         # real result incl. ET (e.g. 3-2)
                block["final_score"] = result["final_score"]
        days.setdefault(day, []).append(block)
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

# ── Advancement scoring (knockout phase — CLAUDE.md §7) ──────────────────────
def advancement_prob(fc: dict) -> float:
    """P(home team advances) derived from a forecast triple: p_home + ½·p_draw.
    The ½-split is the pre-registered convention (extra time / penalties ≈ a coin
    toss). Direct picks (humans + md074+ models) carry draw≈0, so this is ≈p_home;
    md073 models / the market split their real draw mass identically."""
    return min(1.0, max(0.0, fc["p_home"] + 0.5 * fc["p_draw"]))

def advancement_brier(p_adv_home: float, advanced: str) -> float:
    """Two-class Brier on who advanced. Same [0,2] scale as the 3-way Brier:
    perfect 0 · coin-flip (0.5) → 0.5 · certain-and-wrong → 2."""
    y = 1.0 if advanced == "home" else 0.0
    p_adv_away = 1.0 - p_adv_home
    return (p_adv_home - y) ** 2 + (p_adv_away - (1.0 - y)) ** 2

# ── Cumulative leaderboards (group 3-way + knockout advancement share assembly)
def _assemble_leaderboard(forecaster_stats: dict, total_scored: int,
                          updated_utc: str, extra: dict | None = None) -> dict:
    """Turn accumulated {forecaster: {n, sum_brier, first_idx}} into a sorted,
    qualification-tagged, vs-market leaderboard. Shared by both metrics."""
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
            "n_available": n_available,  # matches scored since this forecaster's first prediction
            "mean_brier": mean_b,
            "vs_market": None,
            "qualified": qualified,
        })

    for row in rows:
        if market_brier is not None and row["forecaster"] != "market":
            row["vs_market"] = round(row["mean_brier"] - market_brier, 4)

    rows.sort(key=lambda r: (not r["qualified"], r["mean_brier"]))

    out = {"updated_utc": updated_utc, "matches_scored": total_scored, "rows": rows}
    if extra:
        out.update(extra)
    return out

def build_leaderboard(scoreable: list, all_preds: dict, updated_utc: str) -> dict:
    """Cumulative multiclass-Brier (90-min result) leaderboard over `scoreable`
    (match_id, result) pairs in kickoff order. Used for the group-stage board."""
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

    uni = forecaster_stats["uniform"]
    uni["first_idx"] = 0
    for _, result in scoreable:
        uni["n"] += 1
        uni["sum_brier"] += brier(1 / 3, 1 / 3, 1 / 3, result["outcome"])

    return _assemble_leaderboard(forecaster_stats, len(scoreable), updated_utc)

def build_advancement_leaderboard(ko_scoreable: list, all_preds: dict,
                                  updated_utc: str) -> dict:
    """Knockout "who advances" board: binary advancement Brier over knockout
    matches that have an `advanced` result. Coin-flip reference = 0.50."""
    forecaster_stats: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "sum_brier": 0.0, "first_idx": None}
    )
    for idx, (match_id, result) in enumerate(ko_scoreable):
        advanced = result["advanced"]
        for forecaster, fc in all_preds[match_id].items():
            if fc.get("status") == "failed":
                continue
            b = advancement_brier(advancement_prob(fc), advanced)
            s = forecaster_stats[forecaster]
            s["n"] += 1
            s["sum_brier"] += b
            if s["first_idx"] is None:
                s["first_idx"] = idx

    uni = forecaster_stats["uniform"]
    uni["first_idx"] = 0
    for _, result in ko_scoreable:
        uni["n"] += 1
        uni["sum_brier"] += advancement_brier(0.5, result["advanced"])

    return _assemble_leaderboard(
        forecaster_stats, len(ko_scoreable), updated_utc,
        extra={"metric": "advancement", "coinflip": 0.5},
    )

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
    updated_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stage_of = {m["match_id"]: m.get("stage", "") for m in fixtures}

    # Two experiments (CLAUDE.md §7): the GROUP board is the 3-way 90-min Brier
    # over group matches only; the KNOCKOUT board is the binary "who advances"
    # Brier. Knockouts are excluded from the group board so the draw-less
    # knockout picks are never judged on the 3-way metric.
    group_scoreable = [t for t in scoreable
                       if not is_knockout(stage_of.get(t[0], ""))]
    leaderboard = build_leaderboard(group_scoreable, all_preds, updated_utc)
    validate_leaderboard(leaderboard, all_preds)

    # Knockout matches need an `advanced` result (who went through after ET/pens).
    ko_adv_scoreable = sorted(
        (
            (mid, r) for mid, r in results.items()
            if r.get("status") == "FT" and r.get("advanced") in ("home", "away")
            and mid in all_preds and is_knockout(stage_of.get(mid, ""))
        ),
        key=lambda t: kickoff_of.get(t[0], ""),
    )
    ko_leaderboard = build_advancement_leaderboard(ko_adv_scoreable, all_preds, updated_utc)
    validate_leaderboard(ko_leaderboard, all_preds)
    total_scored = len(scoreable)

    # Build calibration + per-match boards
    calibration = build_calibration(all_preds, results)
    fixture_map = {m["match_id"]: m for m in fixtures}
    match_scores = {
        "updated_utc": updated_utc,
        "days": build_match_scores(scoreable, all_preds, fixture_map),
    }

    # Write outputs
    scores_dir = ROOT / "data" / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)

    (scores_dir / "leaderboard.json").write_text(json.dumps(leaderboard, indent=2))
    (scores_dir / "leaderboard_knockouts.json").write_text(json.dumps(ko_leaderboard, indent=2))
    (scores_dir / "calibration.json").write_text(json.dumps(calibration, indent=2))
    (scores_dir / "match_scores.json").write_text(json.dumps(match_scores, indent=2))

    print(
        f"Scored {total_scored} matches | "
        f"group(3-way): {leaderboard['matches_scored']} | "
        f"knockout(advancement): {ko_leaderboard['matches_scored']} | "
        f"leaderboard + knockouts + calibration + match boards written"
    )


if __name__ == "__main__":
    main()
