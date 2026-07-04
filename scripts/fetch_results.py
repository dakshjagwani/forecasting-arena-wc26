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
# Reuses freeze.py's alias table: football-data.org uses the same bookmaker-ish
# names ("Korea Republic", "Czech Republic", "United States") that plain
# substring matching misses against our fixture names.
sys.path.insert(0, str(Path(__file__).parent))
from freeze import _names_match, is_knockout

# football-data score.winner → our positional side; score.duration → label.
_WINNER_SIDE = {"HOME_TEAM": "home", "AWAY_TEAM": "away"}
_DECIDED_BY = {"REGULAR": "regular", "EXTRA_TIME": "extra_time",
               "PENALTY_SHOOTOUT": "penalties"}

def find_fixture(home_api: str, away_api: str, fixture_by_teams: dict) -> dict | None:
    for (h, a), f in fixture_by_teams.items():
        if _names_match(h, home_api) and _names_match(a, away_api):
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
                # We score the 90-MINUTE result (pre-registered). For knockout
                # matches that went to extra time, "fullTime" includes ET —
                # the 90-minute score lives in "regularTime".
                score_obj = m.get("score", {})
                if score_obj.get("duration", "REGULAR") == "REGULAR":
                    score = score_obj.get("fullTime") or {}
                else:
                    score = score_obj.get("regularTime") or {}
                    if score.get("home") is None:
                        print(f"Warning: ET match without regularTime score "
                              f"({m['homeTeam']['name']} vs {m['awayTeam']['name']}) "
                              f"— enter the 90-min result manually", file=sys.stderr)
                        continue
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
                    # Never overwrite the confirmed 90-min score — but DO backfill
                    # advancement on a knockout result that was recorded before the
                    # shootout winner landed. A penalty match can be caught in a
                    # data-lag window where duration=PENALTY_SHOOTOUT but `winner`
                    # is still null (its `pens` even show an impossible tie); the
                    # guard then froze it without `advanced`. Refresh only the
                    # advancement fields from the now-complete API.
                    ex = existing[mid]
                    if is_knockout(fixture.get("stage", "")) and not ex.get("advanced"):
                        side = _WINNER_SIDE.get(score_obj.get("winner"))
                        if side:
                            ex["advanced"] = side
                            ex["decided_by"] = _DECIDED_BY.get(
                                score_obj.get("duration", "REGULAR"), "regular")
                            pens = score_obj.get("penalties") or {}
                            if pens.get("home") is not None and pens.get("away") is not None:
                                ex["pens"] = f"{int(pens['home'])}-{int(pens['away'])}"
                            print(f"Backfilled advancement for {mid}: {side}", file=sys.stderr)
                            new_count += 1
                    continue  # 90-min score already confirmed

                result = {
                    "match_id": mid,
                    "score_home": int(sh),
                    "score_away": int(sa),
                    "outcome": outcome_from_score(int(sh), int(sa)),
                    "status": "FT",
                }

                # Knockouts: also record who ADVANCED (after extra time /
                # penalties) — display-only for the result line, and the scored
                # outcome for the advancement leaderboard. The 90-min `outcome`
                # above is unchanged (kept for the record + group-style display).
                if is_knockout(fixture.get("stage", "")):
                    side = _WINNER_SIDE.get(score_obj.get("winner"))
                    if side:
                        result["advanced"] = side
                    result["decided_by"] = _DECIDED_BY.get(
                        score_obj.get("duration", "REGULAR"), "regular")
                    pens = score_obj.get("penalties") or {}
                    if pens.get("home") is not None and pens.get("away") is not None:
                        result["pens"] = f"{int(pens['home'])}-{int(pens['away'])}"

                existing[mid] = result
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
