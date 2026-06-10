"""
One-off script: builds data/fixtures/fixtures.json from the raw Kaggle CSVs.
Run once before matchday 1. Safe to re-run — overwrites the output file.
"""
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "fixtures" / "fixtures.json"


def load_csv(name: str) -> dict:
    rows = {}
    with open(RAW / name, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows[int(row["id"])] = row
    return rows


def to_utc_iso(ts: str) -> str:
    """Convert '2026-06-11 15:00:00-06' to '2026-06-11T21:00:00Z'.

    Python <3.11 fromisoformat requires -HH:MM not -HH, so we pad short offsets.
    """
    import re
    # Pad bare hour offsets: -06 -> -06:00, +05 -> +05:00
    ts = re.sub(r"([+-])(\d{2})$", r"\1\2:00", ts)
    dt = datetime.fromisoformat(ts)
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def match_date_utc(kickoff_utc: str) -> str:
    """Return the UTC date that predictions must be frozen by (the kickoff date in UTC)."""
    return kickoff_utc[:10]


def make_match_id(num: int, home_code, away_code, label: str) -> str:
    """md01-MEX-RSA for known teams; md73-R32 for knockouts."""
    prefix = f"md{num:03d}"
    if home_code and away_code:
        return f"{prefix}-{home_code}-{away_code}"
    # Use a short slug from the match label for knockouts
    slug = label.replace(" ", "").replace("vs", "-")[:12]
    return f"{prefix}-{slug}"


def main() -> None:
    teams = load_csv("teams.csv")
    cities = load_csv("host_cities.csv")
    stages = load_csv("tournament_stages.csv")

    fixtures = []
    errors = []

    with open(RAW / "matches.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            num = int(row["match_number"])
            city = cities[int(row["city_id"])]
            stage = stages[int(row["stage_id"])]

            home_id = row["home_team_id"].strip()
            away_id = row["away_team_id"].strip()
            home = teams[int(home_id)] if home_id else None
            away = teams[int(away_id)] if away_id else None

            try:
                kickoff_utc = to_utc_iso(row["kickoff_at"])
            except Exception as e:
                errors.append(f"match {num}: bad kickoff '{row['kickoff_at']}': {e}")
                continue

            match_id = make_match_id(
                num,
                home["fifa_code"] if home else None,
                away["fifa_code"] if away else None,
                row["match_label"],
            )

            venue = f"{city['venue_name']}, {city['city_name']}"

            # Stage label: for group stage use "Group X", for knockouts use stage name
            if stage["stage_name"] == "Group Stage":
                stage_label = row["match_label"]  # e.g. "Group A"
            else:
                stage_label = stage["stage_name"]  # e.g. "Round of 32"

            fixture = {
                "match_id": match_id,
                "match_number": num,
                "matchday_label": match_date_utc(kickoff_utc),
                "stage": stage_label,
                "home": home["team_name"] if home else None,
                "away": away["team_name"] if away else None,
                "home_code": home["fifa_code"] if home else None,
                "away_code": away["fifa_code"] if away else None,
                "is_placeholder": (
                    (home["is_placeholder"] == "True" if home else False)
                    or (away["is_placeholder"] == "True" if away else False)
                ),
                "kickoff_utc": kickoff_utc,
                "venue": venue,
                "city": city["city_name"],
                "country": city["country"],
            }
            fixtures.append(fixture)

    if errors:
        print("ERRORS:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(fixtures, f, indent=2, ensure_ascii=False)

    print(f"Written {len(fixtures)} fixtures to {OUT}")

    # Sanity checks
    assert len(fixtures) == 104, f"Expected 104 fixtures, got {len(fixtures)}"
    group_stage = [m for m in fixtures if not m["stage"].startswith(("Round", "Quarter", "Semi", "Third", "Final"))]
    knockout = [m for m in fixtures if m not in group_stage]
    assert len(group_stage) == 72, f"Expected 72 group stage matches, got {len(group_stage)}"
    assert len(knockout) == 32, f"Expected 32 knockout matches, got {len(knockout)}"
    print(f"  Group stage: {len(group_stage)}  Knockout: {len(knockout)}  Checks OK")

    # Date range check
    dates = sorted(set(m["matchday_label"] for m in fixtures))
    print(f"  Date range: {dates[0]} to {dates[-1]}")


if __name__ == "__main__":
    main()
