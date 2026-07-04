"""
test_resolve.py — knockout fixture resolution (scripts/resolve_fixtures.py).

The resolve/pin logic is pure (no network, no disk): tests feed it synthetic
fixtures + canned football-data.org match dicts and assert exact output. A
sanitised snapshot of the live API response (tests/golden/wc_matches_sample.json)
backs an end-to-end golden test over the real bracket shape.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import resolve_fixtures as rf

ROOT = Path(__file__).parent.parent
GOLDEN = ROOT / "tests" / "golden"
NOW = datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc)  # before all knockouts


# ── builders ──────────────────────────────────────────────────────────────────
def ko_fixture(num, stage, mid, kickoff):
    return {"match_id": mid, "match_number": num, "matchday_label": kickoff[:10],
            "stage": stage, "home": None, "away": None, "home_code": None,
            "away_code": None, "is_placeholder": True, "kickoff_utc": kickoff,
            "venue": "X", "city": "X", "country": "X"}


def group_fixture(num, mid, home, hc, away, ac, kickoff):
    return {"match_id": mid, "match_number": num, "matchday_label": kickoff[:10],
            "stage": "Group A", "home": home, "away": away, "home_code": hc,
            "away_code": ac, "is_placeholder": False, "kickoff_utc": kickoff,
            "venue": "X", "city": "X", "country": "X"}


def api_match(fd_id, stage, kickoff, home=None, away=None, htla=None, atla=None,
              status="TIMED"):
    def team(n, t):
        return {"name": n, "tla": t} if n else {"name": None, "tla": None}
    return {"id": fd_id, "stage": stage, "utcDate": kickoff, "status": status,
            "homeTeam": team(home, htla), "awayTeam": team(away, atla),
            "score": {}}


# ── pinning ───────────────────────────────────────────────────────────────────
def test_pin_is_bijection():
    fixtures = [
        ko_fixture(73, "Round of 32", "md073", "2026-06-28T22:00:00Z"),
        ko_fixture(74, "Round of 32", "md074", "2026-06-29T18:00:00Z"),
    ]
    api = [
        api_match(901, "LAST_32", "2026-06-29T17:00:00Z"),
        api_match(902, "LAST_32", "2026-06-28T19:00:00Z"),
    ]
    pins = rf.pin_fixtures(fixtures, api)
    # local sorted by match_number, API by kickoff -> 73->earliest api (902), 74->901
    assert pins == {"md073": 902, "md074": 901}
    assert len(set(pins.values())) == 2  # distinct fd_ids


def test_pin_aborts_on_count_mismatch():
    fixtures = [ko_fixture(73, "Round of 32", "md073", "2026-06-28T22:00:00Z")]
    api = [api_match(901, "LAST_32", "2026-06-29T17:00:00Z"),
           api_match(902, "LAST_32", "2026-06-28T19:00:00Z")]
    with pytest.raises(ValueError):
        rf.pin_fixtures(fixtures, api)


def test_pin_is_idempotent():
    fixtures = [ko_fixture(73, "Round of 32", "md073", "2026-06-28T22:00:00Z")]
    fixtures[0]["fd_id"] = 902
    api = [api_match(902, "LAST_32", "2026-06-28T19:00:00Z")]
    # already pinned -> no new pins proposed
    assert rf.pin_fixtures(fixtures, api) == {}


# ── name + code mapping ───────────────────────────────────────────────────────
@pytest.mark.parametrize("api_name,expected", [
    ("United States", "USA"),
    ("Ivory Coast", "Côte d'Ivoire"),
    ("Cape Verde Islands", "Cabo Verde"),
    ("Congo DR", "DR Congo"),
    ("Bosnia-Herzegovina", "Bosnia and Herzegovina"),
    ("Brazil", "Brazil"),          # already canonical -> unchanged
])
def test_name_normalisation(api_name, expected):
    assert rf.canonical_name(api_name) == expected


def test_code_derivation_uses_existing_codes():
    fixtures = [group_fixture(1, "md001", "South Africa", "RSA", "Canada", "CAN",
                              "2026-06-11T21:00:00Z")]
    code_map = rf.build_code_map(fixtures)
    assert code_map["South Africa"] == "RSA"
    assert code_map["Canada"] == "CAN"


# ── resolve ───────────────────────────────────────────────────────────────────
def _base():
    """A group row (for codes) + one unpinned R32 placeholder + its API match."""
    fixtures = [
        group_fixture(1, "md001", "South Africa", "RSA", "Canada", "CAN",
                      "2026-06-11T21:00:00Z"),
        ko_fixture(73, "Round of 32", "md073", "2026-06-28T22:00:00Z"),
    ]
    api = [api_match(902, "LAST_32", "2026-06-28T19:00:00Z",
                     home="South Africa", away="Canada", htla="RSA", atla="CAN")]
    return fixtures, api


def test_resolve_fills_team_and_time():
    fixtures, api = _base()
    out, changes = rf.resolve_fixtures(fixtures, api, NOW, frozen_ids=set())
    row = next(f for f in out if f["match_id"] == "md073")
    assert row["home"] == "South Africa" and row["away"] == "Canada"
    assert row["home_code"] == "RSA" and row["away_code"] == "CAN"
    assert row["is_placeholder"] is False
    assert row["kickoff_utc"] == "2026-06-28T19:00:00Z"   # corrected from 22:00
    assert row["fd_id"] == 902
    # 22:00Z and 19:00Z both fall on the same UTC-8 campaign day, but the field
    # is recomputed regardless.
    assert row["matchday_label"] == "2026-06-28"


def test_resolve_corrects_time_even_when_unresolved():
    fixtures = [ko_fixture(89, "Round of 16", "md089", "2026-07-04T18:00:00Z")]
    api = [api_match(537376, "LAST_16", "2026-07-04T17:00:00Z")]  # teams still null
    out, changes = rf.resolve_fixtures(fixtures, api, NOW, frozen_ids=set())
    row = out[0]
    assert row["is_placeholder"] is True          # no teams yet
    assert row["home"] is None
    assert row["kickoff_utc"] == "2026-07-04T17:00:00Z"   # but time corrected
    assert any("time md089" in c for c in changes)


def test_resolve_skips_frozen_match():
    fixtures, api = _base()
    fixtures[1]["fd_id"] = 902  # already pinned
    before = json.dumps(fixtures[1], sort_keys=True)
    out, changes = rf.resolve_fixtures(fixtures, api, NOW, frozen_ids={"md073"})
    after = json.dumps(out[1], sort_keys=True)
    assert before == after                         # untouched
    assert not any("md073" in c for c in changes)


def test_resolve_skips_past_kickoff_for_teams():
    fixtures, api = _base()
    # "now" is AFTER the API kickoff -> don't backfill teams, but time still synced
    now_after = datetime(2026, 6, 28, 20, 0, tzinfo=timezone.utc)
    out, changes = rf.resolve_fixtures(fixtures, api, now_after, frozen_ids=set())
    row = next(f for f in out if f["match_id"] == "md073")
    assert row["is_placeholder"] is True           # not resolved (kicked off)
    assert row["home"] is None
    assert row["kickoff_utc"] == "2026-06-28T19:00:00Z"   # time still corrected


def test_resolve_is_idempotent():
    fixtures, api = _base()
    out1, _ = rf.resolve_fixtures(fixtures, api, NOW, frozen_ids=set())
    out2, changes2 = rf.resolve_fixtures(out1, api, NOW, frozen_ids=set())
    assert out1 == out2                            # second pass is a no-op
    assert changes2 == []


# ── golden end-to-end over the real bracket shape ─────────────────────────────
def test_golden_real_bracket():
    """Run resolve against the live API snapshot + the real fixtures file.
    Asserts the whole bracket pins 1:1 and the R32 resolves with canonical names."""
    api = json.loads((GOLDEN / "wc_matches_sample.json").read_text())
    fixtures = json.loads((ROOT / "data" / "fixtures" / "fixtures.json").read_text())
    # Reset knockouts to their placeholder state so this test is deterministic and
    # independent of live tournament progress (R16+ resolve as the real bracket
    # advances; the golden API snapshot still has them null).
    for f in fixtures:
        if f["stage"] in rf.KNOCKOUT_STAGES:
            f["home"] = f["away"] = f["home_code"] = f["away_code"] = None
            f["is_placeholder"] = True
            f.pop("fd_id", None)

    out, changes = rf.resolve_fixtures(fixtures, api, NOW, frozen_ids=set())

    ko = [f for f in out if f["stage"] in rf.KNOCKOUT_STAGES]
    assert len(ko) == 32
    # every knockout row is pinned to a distinct fd_id (bijection held)
    fd_ids = [f["fd_id"] for f in ko]
    assert all(fd_ids) and len(set(fd_ids)) == 32
    # all 16 R32 resolved with non-null canonical teams; R16+ still placeholders
    r32 = [f for f in out if f["stage"] == "Round of 32"]
    assert all(not f["is_placeholder"] and f["home"] and f["away"] for f in r32)
    later = [f for f in out if f["stage"] in
             {"Round of 16", "Quarterfinals", "Semifinals", "Final",
              "Third Place Playoff"}]
    assert all(f["is_placeholder"] for f in later)
    # the 5 tricky names normalised, none left in raw API form
    names = {f["home"] for f in r32} | {f["away"] for f in r32}
    for raw in ("United States", "Ivory Coast", "Cape Verde Islands",
                "Congo DR", "Bosnia-Herzegovina"):
        assert raw not in names
    assert {"USA", "Côte d'Ivoire", "Cabo Verde", "DR Congo",
            "Bosnia and Herzegovina"} <= names
