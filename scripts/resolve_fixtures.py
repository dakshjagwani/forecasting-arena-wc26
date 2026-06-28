#!/usr/bin/env python3
"""
resolve_fixtures.py — Fill knockout team names + correct kickoff times from
football-data.org, the same source fetch_results.py uses for results.

Why this exists
---------------
The knockout bracket in data/fixtures/fixtures.json ships as placeholders
(home/away null, is_placeholder true) because the teams aren't known until the
feeding round finishes. football-data.org's /competitions/WC/matches endpoint
returns the WHOLE bracket and fills knockout teams automatically as each round
completes — so we read the bracket from there instead of hand-editing fixtures.

It ALSO corrects kickoff times: our hand-built schedule has drifted from the
real one (e.g. an R32 listed at 22:00Z that actually kicks off at 19:00Z).
Since the freeze window is "3h before first kickoff" computed from these times,
a stale time would freeze a match AFTER it has kicked off and void it. The API
carries the authoritative time, so we overwrite ours with it.

The join (why it's safe)
------------------------
The API shares no id with us — not the FIFA match number, not our bracket-slot
labels ("md073-2A-2B"), and its `matchday` is null for knockouts. So on first
run we PIN each local knockout row to an API match 1:1 within its stage (16 R32,
8 R16, 4 QF, 2 SF, 1 third-place, 1 final), local ordered by match_number and
API by kickoff. Equal counts per stage => a bijection: no match can be dropped
or duplicated. We staple football-data's stable `id` onto each row as `fd_id`;
every later update flows in by that id with zero ambiguity. Run with --dry-run
once and eyeball the printed pin table against the official bracket.

Integrity
---------
- A match_id that already appears in any data/predictions/*.json is FROZEN and
  is never edited here (one-cutoff rule).
- A match whose kickoff has already passed is never (re)resolved (no backfill).
- The file is rewritten only when something actually changed (stable diffs).
"""
from __future__ import annotations
import argparse, json, os, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
FIXTURES_PATH = ROOT / "data" / "fixtures" / "fixtures.json"
PREDICTIONS_DIR = ROOT / "data" / "predictions"

FOOTBALL_DATA_URL = "https://api.football-data.org/v4/competitions/WC/matches"

# Reuse freeze.py's .env loader, alias table, fuzzy matcher and UTC-8 day rule
# rather than duplicating them (single source of truth).
sys.path.insert(0, str(Path(__file__).parent))
from freeze import _names_match, ODDS_ALIASES, load_dotenv, _UTC8  # noqa: E402

# football-data.org stage code -> our fixtures.json stage label.
STAGE_API_TO_LOCAL = {
    "LAST_32":        "Round of 32",
    "LAST_16":        "Round of 16",
    "QUARTER_FINALS": "Quarterfinals",
    "SEMI_FINALS":    "Semifinals",
    "THIRD_PLACE":    "Third Place Playoff",
    "FINAL":          "Final",
}
KNOCKOUT_STAGES = set(STAGE_API_TO_LOCAL.values())


# ── helpers ───────────────────────────────────────────────────────────────────
def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def campaign_day(kickoff_utc: str) -> str:
    """UTC-8 campaign day — identical rule to freeze.py.group_by_utc8 / picks.js."""
    return (_parse_utc(kickoff_utc) - _UTC8).strftime("%Y-%m-%d")


def canonical_name(api_name: str) -> str:
    """Map a football-data.org team name to our canonical fixtures name using the
    same alias table the market/results join uses. Returns api_name unchanged if
    no alias applies (i.e. the API name is already canonical)."""
    # Only consult the alias keys: _names_match(canon, api_name) treats `canon`
    # and its aliases as candidates, so "USA" matches API "United States".
    for canon in ODDS_ALIASES:
        if _names_match(canon, api_name):
            return canon
    return api_name


def build_code_map(fixtures: list) -> dict:
    """canonical team name -> 3-letter code, learned from already-resolved rows so
    knockout codes match the convention already in the file (RSA, CAN, ...)."""
    code_map: dict[str, str] = {}
    for f in fixtures:
        if f.get("home") and f.get("home_code"):
            code_map.setdefault(f["home"], f["home_code"])
        if f.get("away") and f.get("away_code"):
            code_map.setdefault(f["away"], f["away_code"])
    return code_map


def load_frozen_ids() -> set:
    """match_ids that already appear in a committed predictions file — frozen,
    never to be edited here."""
    frozen: set = set()
    if not PREDICTIONS_DIR.exists():
        return frozen
    for p in PREDICTIONS_DIR.glob("*.json"):
        try:
            doc = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        for m in doc.get("matches", []):
            if m.get("match_id"):
                frozen.add(m["match_id"])
    return frozen


# ── pure core (no network, no disk) ───────────────────────────────────────────
def pin_fixtures(fixtures: list, api_matches: list) -> dict:
    """Return {match_id: fd_id} for knockout rows that don't yet have an fd_id.

    Pins 1:1 within each stage (local by match_number, API by kickoff). Raises if
    a stage's local and API counts differ — we abort rather than pin partially.
    Rows already carrying an fd_id are left as-is (idempotent)."""
    api_by_stage: dict[str, list] = {}
    for m in api_matches:
        label = STAGE_API_TO_LOCAL.get(m.get("stage"))
        if label:
            api_by_stage.setdefault(label, []).append(m)

    pins: dict[str, str] = {}
    for stage in KNOCKOUT_STAGES:
        local = [f for f in fixtures
                 if f.get("stage") == stage and not f.get("fd_id")]
        if not local:
            continue
        # Don't reuse fd_ids already pinned to sibling rows in this stage.
        used = {f["fd_id"] for f in fixtures
                if f.get("stage") == stage and f.get("fd_id")}
        api = [m for m in api_by_stage.get(stage, []) if m["id"] not in used]

        local_all = [f for f in fixtures if f.get("stage") == stage]
        api_all = api_by_stage.get(stage, [])
        if len(local_all) != len(api_all):
            raise ValueError(
                f"Stage {stage!r}: {len(local_all)} local rows vs "
                f"{len(api_all)} API matches — refusing to pin (bracket mismatch)"
            )

        local.sort(key=lambda f: f["match_number"])
        api.sort(key=lambda m: m["utcDate"])
        for f, m in zip(local, api):
            pins[f["match_id"]] = m["id"]
    return pins


def resolve_fixtures(fixtures: list, api_matches: list, now_utc: datetime,
                     frozen_ids: set) -> tuple[list, list]:
    """Apply pins + fill teams + correct times. Pure: returns (new_fixtures,
    changes) and never touches disk. `changes` is a list of human-readable
    strings for logging / ntfy."""
    api_by_id = {m["id"]: m for m in api_matches}
    code_map = build_code_map(fixtures)
    out = [dict(f) for f in fixtures]   # shallow copies; we only set scalars
    changes: list[str] = []

    # 1) Stamp any new pins (never onto a frozen row).
    pins = pin_fixtures(out, api_matches)
    for f in out:
        if f["match_id"] in pins and f["match_id"] not in frozen_ids:
            f["fd_id"] = pins[f["match_id"]]
            changes.append(f"pin {f['match_id']} -> fd_id {f['fd_id']}")

    # 2) Sync time + teams from the API by fd_id.
    for f in out:
        fd_id = f.get("fd_id")
        if not fd_id or fd_id not in api_by_id:
            continue
        if f["match_id"] in frozen_ids:
            continue  # frozen — one-cutoff rule, never edit
        m = api_by_id[fd_id]
        api_kick = m["utcDate"]

        # Correct kickoff time (+ derived campaign-day label) regardless of
        # whether teams are known yet — the freeze window depends on it.
        if api_kick and api_kick != f.get("kickoff_utc"):
            old = f.get("kickoff_utc")
            f["kickoff_utc"] = api_kick
            f["matchday_label"] = campaign_day(api_kick)
            changes.append(f"time {f['match_id']}: {old} -> {api_kick}")

        # Don't resolve teams for a match that has already kicked off (no
        # backfill); the time correction above is harmless either way.
        if _parse_utc(api_kick) <= now_utc:
            continue

        ht = (m.get("homeTeam") or {}).get("name")
        at = (m.get("awayTeam") or {}).get("name")
        if not ht or not at:
            continue  # still unresolved upstream

        home = canonical_name(ht)
        away = canonical_name(at)
        if f.get("home") != home or f.get("away") != away or f.get("is_placeholder"):
            f["home"] = home
            f["away"] = away
            f["home_code"] = code_map.get(home) or (m["homeTeam"].get("tla"))
            f["away_code"] = code_map.get(away) or (m["awayTeam"].get("tla"))
            f["is_placeholder"] = False
            changes.append(f"teams {f['match_id']}: {home} vs {away}")

    return out, changes


# ── IO shell ──────────────────────────────────────────────────────────────────
def fetch_wc_matches(api_key: str) -> list:
    req = urllib.request.Request(
        FOOTBALL_DATA_URL,
        headers={"X-Auth-Token": api_key, "User-Agent": "python-requests/2.31.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("matches", [])


def print_pin_table(fixtures: list, api_matches: list) -> None:
    """One-time human verification aid: show how each local knockout slot pinned
    to a real API match (team names if resolved, else kickoff slot)."""
    api_by_id = {m["id"]: m for m in api_matches}
    rows = [f for f in fixtures if f.get("stage") in KNOCKOUT_STAGES]
    rows.sort(key=lambda f: f["match_number"])
    print("\n  Pin table (verify once against the official bracket):")
    print(f"  {'match_id':22} {'fd_id':8} {'kickoff (real)':21} matchup")
    for f in rows:
        m = api_by_id.get(f.get("fd_id"), {})
        ht = (m.get("homeTeam") or {}).get("name") or "—"
        at = (m.get("awayTeam") or {}).get("name") or "—"
        print(f"  {f['match_id']:22} {str(f.get('fd_id','')):8} "
              f"{m.get('utcDate','?'):21} {ht} vs {at}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + print changes but write nothing")
    args = ap.parse_args()

    load_dotenv()
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not api_key:
        print("No FOOTBALL_DATA_API_KEY set — cannot resolve knockouts.",
              file=sys.stderr)
        return 0  # non-fatal: leaves placeholders, freeze just skips them

    fixtures = json.loads(FIXTURES_PATH.read_text())
    try:
        api_matches = fetch_wc_matches(api_key)
    except Exception as e:  # noqa: BLE001 — network failure is non-fatal
        print(f"football-data.org fetch failed: {e}", file=sys.stderr)
        return 0

    frozen_ids = load_frozen_ids()
    now_utc = datetime.now(timezone.utc)
    updated, changes = resolve_fixtures(fixtures, api_matches, now_utc, frozen_ids)

    # Always show the pin table — it's the thing to eyeball on first run.
    print_pin_table(updated, api_matches)

    if not changes:
        print("No changes — fixtures already in sync with football-data.org.")
        return 0

    print(f"{len(changes)} change(s):")
    for c in changes:
        print(f"  - {c}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    FIXTURES_PATH.write_text(json.dumps(updated, indent=2) + "\n")
    print(f"\nWrote {FIXTURES_PATH.relative_to(ROOT)} ({len(changes)} change(s)).")

    # Rebuild the picks-UI Intel data (Elo/form/H2H) now that teams are known —
    # otherwise the card shows default 1500s until the next freeze regenerates it.
    try:
        from context_builder import build_match_contexts_json
        build_match_contexts_json(updated)
        print("Rebuilt data/reference/match_contexts.json for the picks UI.")
    except Exception as e:  # noqa: BLE001 — non-fatal; freeze.py also rebuilds it
        print(f"Could not rebuild match_contexts.json: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
