#!/usr/bin/env python3
"""
fetch_results.py — Fetch match results from football-data.org free API.

Appends to data/results/results.json. Idempotent: FT results are never overwritten.
Falls back gracefully if no API key is set — manual entry path remains first-class.

Manual entry:
  Edit data/results/results.json directly, then re-run score.py.
  Format: [{"match_id": "md001-MEX-RSA", "score_home": 2, "score_away": 1,
             "outcome": "home", "status": "FT"}, ...]
"""
from __future__ import annotations
import json, os, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent

FOOTBALL_DATA_URL = "https://api.football-data.org/v4/competitions/WC/matches"

# ── .env loader ───────────────────────────────────────────────────────────────
def load_dotenv() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

def outcome_from_score(score_home: int, score_away: int) -> str:
    if score_home > score_away:
        return "home"
    if score_away > score_home:
        return "away"
    return "draw"

# ── Team name fuzzy matching ──────────────────────────────────────────────────
def names_match(api_name: str, fixture_name: str) -> bool:
    a, b = api_name.lower().strip(), fixture_name.lower().strip()
    return a == b or a in b or b in a

def find_fixture(home_api: str, away_api: str, fixture_by_teams: dict) -> dict | None:
    for (h, a), f in fixture_by_teams.items():
        if names_match(home_api, h) and names_match(away_api, a):
            return f
    return None

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    load_dotenv()

    results_path = ROOT / "data" / "results" / "results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing results
    existing: dict[str, dict] = {}
    if results_path.exists():
        for r in json.loads(results_path.read_text()):
            existing[r["match_id"]] = r

    # Load fixtures for match_id resolution
    fixtures = json.loads((ROOT / "data" / "fixtures" / "fixtures.json").read_text())
    fixture_by_teams = {
        (f["home"], f["away"]): f
        for f in fixtures
        if f.get("home") and f.get("away") and not f.get("is_placeholder")
    }

    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not api_key:
        print(
            "No FOOTBALL_DATA_API_KEY set — skipping API fetch.\n"
            "Add results manually to data/results/results.json, then run score.py.",
            file=sys.stderr,
        )
    else:
        try:
            req = urllib.request.Request(
                FOOTBALL_DATA_URL,
                headers={
                    "X-Auth-Token": api_key,
                    "User-Agent": "python-requests/2.31.0",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())

            new_count = 0
            for m in data.get("matches", []):
                if m.get("status") != "FINISHED":
                    continue
                score = m.get("score", {}).get("fullTime", {})
                sh, sa = score.get("home"), score.get("away")
                if sh is None or sa is None:
                    continue

                home_api = m["homeTeam"]["name"]
                away_api = m["awayTeam"]["name"]
                fixture = find_fixture(home_api, away_api, fixture_by_teams)
                if not fixture:
                    print(f"Warning: no fixture match for {home_api!r} vs {away_api!r}",
                          file=sys.stderr)
                    continue

                mid = fixture["match_id"]
                if mid in existing and existing[mid].get("status") == "FT":
                    continue  # never overwrite confirmed FT result

                existing[mid] = {
                    "match_id": mid,
                    "score_home": int(sh),
                    "score_away": int(sa),
                    "outcome": outcome_from_score(int(sh), int(sa)),
                    "status": "FT",
                }
                new_count += 1

            print(f"Fetched {new_count} new results from football-data.org")

        except urllib.error.HTTPError as e:
            print(f"API error {e.code}: {e.reason}", file=sys.stderr)
            if e.code == 429:
                print("Rate limited — free tier allows 10 req/min", file=sys.stderr)
        except Exception as e:
            print(f"API fetch failed: {e}", file=sys.stderr)

    # Write back (sorted by match_id for stable diffs)
    results_list = sorted(existing.values(), key=lambda r: r["match_id"])
    results_path.write_text(json.dumps(results_list, indent=2))
    print(f"Total results on disk: {len(results_list)}")


if __name__ == "__main__":
    main()
