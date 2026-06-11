#!/usr/bin/env python3
"""
fetch_context_data.py — Download and prepare match context data.

Run once before the tournament, then re-run weekly to pick up new results.
Outputs:
  data/reference/international_results.csv  — martj42 dataset (49k+ matches)
  data/reference/elo_ratings.json           — Elo ratings computed from history

Usage:
  python scripts/fetch_context_data.py
"""
from __future__ import annotations
import csv, json, logging, math, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
REF_DIR = ROOT / "data" / "reference"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

MARTJ42_URL = (
    "https://raw.githubusercontent.com/martj42/international_results/"
    "master/results.csv"
)

# ── Elo constants ─────────────────────────────────────────────────────────────
DEFAULT_ELO = 1500.0
HOME_ADV    = 100.0   # Elo points added to home team in non-neutral venues

def _k_factor(tournament: str) -> float:
    t = tournament.lower()
    if "world cup" in t and "qualif" not in t:
        return 60.0
    if any(x in t for x in ["euro ", "copa am", "africa cup", "african cup", "afcon",
                              "asian cup", "gold cup", "nations league", "concacaf nations"]):
        return 50.0
    if "qualif" in t or "qualification" in t:
        return 40.0
    return 20.0

def _goal_weight(goal_diff: int) -> float:
    if goal_diff <= 1: return 1.0
    if goal_diff == 2: return 1.5
    return 1.75

def compute_elo(results_path: Path) -> dict[str, float]:
    """Compute Elo for every team from the full historical record."""
    ratings: dict[str, float] = {}

    with open(results_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = sorted(reader, key=lambda r: r["date"])

    log.info(f"  Computing Elo across {len(rows):,} matches...")
    for row in rows:
        home = row["home_team"].strip()
        away = row["away_team"].strip()
        try:
            sh = int(row["home_score"])
            sa = int(row["away_score"])
        except (ValueError, TypeError):
            continue

        neutral = str(row.get("neutral", "")).strip().upper() == "TRUE"
        Ra = ratings.get(home, DEFAULT_ELO)
        Rb = ratings.get(away, DEFAULT_ELO)

        adv  = 0.0 if neutral else HOME_ADV
        Ea   = 1.0 / (1.0 + 10.0 ** ((Rb - (Ra + adv)) / 400.0))

        if sh > sa:   Sa = 1.0
        elif sh < sa: Sa = 0.0
        else:         Sa = 0.5

        K  = _k_factor(row.get("tournament", ""))
        W  = _goal_weight(abs(sh - sa))
        d  = K * W * (Sa - Ea)

        ratings[home] = Ra + d
        ratings[away] = Rb - d

    return {k: round(v, 1) for k, v in ratings.items()}

# ── Download ──────────────────────────────────────────────────────────────────
def download_results(dest: Path) -> None:
    log.info(f"Downloading martj42 international_results...")
    req = urllib.request.Request(MARTJ42_URL, headers={"User-Agent": "python/3.x"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    dest.write_bytes(data)
    lines = data.count(b"\n")
    log.info(f"  Saved {lines:,} rows → {dest}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    REF_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = REF_DIR / "international_results.csv"
    download_results(csv_path)

    log.info("Computing Elo ratings...")
    elo = compute_elo(csv_path)
    log.info(f"  Computed Elo for {len(elo):,} teams")

    elo_path = REF_DIR / "elo_ratings.json"
    elo_path.write_text(json.dumps(elo, indent=2, sort_keys=True))
    log.info(f"  Written → {elo_path}")

    # Spot-check a few well-known teams
    for team in ["Brazil", "Argentina", "France", "Germany", "Mexico", "United States"]:
        if team in elo:
            log.info(f"  {team}: {elo[team]:.0f}")

    log.info("Done — run this again weekly to refresh with new results.")

if __name__ == "__main__":
    main()
