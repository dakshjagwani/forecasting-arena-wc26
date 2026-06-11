#!/usr/bin/env python3
"""
context_builder.py — Build enriched match context for LLM prompts and picks UI.

Reads:
  data/reference/international_results.csv  (martj42 dataset)
  data/reference/elo_ratings.json
  data/reference/venue_metadata.json
  data/results/results.json                 (our WC 2026 results so far)

Exports:
  get_context_block(match)     → enriched prompt string (or None if data missing)
  build_match_contexts_json()  → writes data/reference/match_contexts.json for picks UI
"""
from __future__ import annotations
import csv, json, logging
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
REF_DIR = ROOT / "data" / "reference"

log = logging.getLogger(__name__)

# Normalise team names from our fixtures to martj42 names
FIXTURE_TO_MARTJ42: dict[str, str] = {
    "USA":            "United States",
    "Côte d'Ivoire":  "Ivory Coast",
    "Cabo Verde":     "Cape Verde",
    "IR Iran":        "Iran",
    "Curaçao":        "Curacao",
}

# Reverse map built at module level
_MARTJ42_TO_FIXTURE = {v: k for k, v in FIXTURE_TO_MARTJ42.items()}

def _to_martj42(name: str) -> str:
    return FIXTURE_TO_MARTJ42.get(name, name)

# ── Lazy-loaded data cache ────────────────────────────────────────────────────
_results:  Optional[list[dict]] = None
_elo:      Optional[dict[str, float]] = None
_venue:    Optional[dict[str, dict]] = None
_wc_res:   Optional[dict[str, dict]] = None

def _load_all() -> bool:
    """Load reference data. Returns False if any required file is missing."""
    global _results, _elo, _venue, _wc_res

    csv_path = REF_DIR / "international_results.csv"
    elo_path = REF_DIR / "elo_ratings.json"
    venue_path = REF_DIR / "venue_metadata.json"

    if not csv_path.exists() or not elo_path.exists():
        log.warning(
            "Context data missing — run: python scripts/fetch_context_data.py"
        )
        return False

    if _results is None:
        log.info("Loading historical results...")
        with open(csv_path, encoding="utf-8") as f:
            _results = list(csv.DictReader(f))
        log.info(f"  {len(_results):,} historical matches loaded")

    if _elo is None:
        _elo = json.loads(elo_path.read_text())

    if _venue is None and venue_path.exists():
        _venue = json.loads(venue_path.read_text())

    # Overlay WC 2026 results we've already recorded
    wc_path = ROOT / "data" / "results" / "results.json"
    if wc_path.exists() and wc_path.stat().st_size > 2:
        _wc_res = {r["match_id"]: r for r in json.loads(wc_path.read_text())}
    else:
        _wc_res = {}

    return True

# ── H2H ───────────────────────────────────────────────────────────────────────
def _get_h2h(home_m42: str, away_m42: str) -> dict:
    assert _results is not None
    meetings = []
    for r in _results:
        h, a = r["home_team"], r["away_team"]
        if (h == home_m42 and a == away_m42) or (h == away_m42 and a == home_m42):
            meetings.append(r)
    meetings.sort(key=lambda r: r["date"], reverse=True)

    home_wins = draws = away_wins = 0
    for m in meetings:
        try:
            sh, sa = int(m["home_score"]), int(m["away_score"])
        except (ValueError, TypeError):
            continue
        if m["home_team"] == home_m42:
            if sh > sa:   home_wins += 1
            elif sh == sa: draws += 1
            else:          away_wins += 1
        else:
            if sa > sh:   home_wins += 1
            elif sh == sa: draws += 1
            else:          away_wins += 1

    last_5 = []
    for m in meetings[:5]:
        try:
            sh, sa = int(m["home_score"]), int(m["away_score"])
        except (ValueError, TypeError):
            continue
        h_disp = _MARTJ42_TO_FIXTURE.get(m["home_team"], m["home_team"])
        a_disp = _MARTJ42_TO_FIXTURE.get(m["away_team"], m["away_team"])
        last_5.append(
            f"{m['date'][:4]} ({m['tournament'][:20]}) — {h_disp} {sh}-{sa} {a_disp}"
        )

    return {
        "played": len(meetings),
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "last_5": last_5,
    }

# ── Recent form ───────────────────────────────────────────────────────────────
def _get_form(team_m42: str, n: int = 10) -> dict:
    assert _results is not None
    team_matches = [
        r for r in _results
        if r["home_team"] == team_m42 or r["away_team"] == team_m42
    ]
    team_matches.sort(key=lambda r: r["date"], reverse=True)
    recent = team_matches[:n]

    form: list[str] = []
    gf = ga = 0
    for m in recent:
        try:
            sh, sa = int(m["home_score"]), int(m["away_score"])
        except (ValueError, TypeError):
            continue
        if m["home_team"] == team_m42:
            gf += sh; ga += sa
            form.append("W" if sh > sa else ("D" if sh == sa else "L"))
        else:
            gf += sa; ga += sh
            form.append("W" if sa > sh else ("D" if sh == sa else "L"))

    n_played = len(form)
    return {
        "results": form,           # most recent first
        "n": n_played,
        "w": form.count("W"),
        "d": form.count("D"),
        "l": form.count("L"),
        "gf": gf,
        "ga": ga,
        "gpg": round(gf / n_played, 2) if n_played else 0.0,
        "gapg": round(ga / n_played, 2) if n_played else 0.0,
    }

# ── Venue ─────────────────────────────────────────────────────────────────────
def _get_venue(venue_str: str) -> Optional[dict]:
    if not _venue:
        return None
    # Exact match first
    if venue_str in _venue:
        return _venue[venue_str]
    # Partial match on venue name
    for k, v in _venue.items():
        if k.split(",")[0].lower() in venue_str.lower():
            return v
    return None

# ── Context block (for LLM prompt) ───────────────────────────────────────────
def get_context_block(match: dict) -> Optional[str]:
    """
    Build the full context string to inject into the LLM prompt.
    Returns None if reference data hasn't been downloaded yet.
    """
    if not _load_all():
        return None

    home = match["home"]
    away = match["away"]

    # Skip placeholder teams
    for placeholder in ("Winner", "Runner", "Playoff", "TBD"):
        if placeholder in home or placeholder in away:
            return None

    home_m42 = _to_martj42(home)
    away_m42 = _to_martj42(away)

    home_elo = _elo.get(home_m42, _elo.get(home, 1500.0)) if _elo else 1500.0
    away_elo = _elo.get(away_m42, _elo.get(away, 1500.0)) if _elo else 1500.0

    home_form = _get_form(home_m42)
    away_form = _get_form(away_m42)

    h2h = _get_h2h(home_m42, away_m42)
    venue_info = _get_venue(match.get("venue", ""))

    lines: list[str] = []

    # Header
    date_str = match.get("kickoff_utc", "")[:10]
    lines.append(f"=== MATCH CONTEXT ===")
    lines.append(
        f"{home} vs {away} | {match.get('stage','?')} | "
        f"{match.get('venue','?')} | {date_str}"
    )
    lines.append("")

    # Venue
    if venue_info:
        alt = venue_info["altitude_m"]
        if alt > 800:
            lines.append(f"VENUE ALTITUDE: {alt}m — {venue_info['altitude_note']}")
        else:
            lines.append(f"VENUE: {venue_info['climate_note']}")
        if venue_info["june_temp_c"] >= 30:
            lines.append(
                f"  Heat warning: {venue_info['june_temp_c']}°C, "
                f"{venue_info['june_humidity']} humidity in June"
            )
        lines.append("")

    # Team profiles
    def form_str(f: dict, n: int = 5) -> str:
        r = f["results"][:n]
        return " ".join(r) if r else "N/A"

    def record_str(f: dict) -> str:
        return f"W{f['w']} D{f['d']} L{f['l']}"

    lines.append(f"{home.upper()} (home)")
    lines.append(f"  Elo: {home_elo:.0f}")
    lines.append(f"  Last 5:  {form_str(home_form)}")
    if home_form["n"] >= 8:
        lines.append(
            f"  Last 10: {record_str(home_form)} | "
            f"GF/GA per game: {home_form['gpg']:.1f} / {home_form['gapg']:.1f}"
        )
    lines.append("")

    lines.append(f"{away.upper()} (away)")
    lines.append(f"  Elo: {away_elo:.0f}")
    lines.append(f"  Last 5:  {form_str(away_form)}")
    if away_form["n"] >= 8:
        lines.append(
            f"  Last 10: {record_str(away_form)} | "
            f"GF/GA per game: {away_form['gpg']:.1f} / {away_form['gapg']:.1f}"
        )
    lines.append("")

    # H2H
    if h2h["played"] > 0:
        lines.append(
            f"HEAD-TO-HEAD (all-time): Played {h2h['played']} | "
            f"{home} {h2h['home_wins']}W {h2h['draws']}D {h2h['away_wins']}L"
        )
        for meeting in h2h["last_5"]:
            lines.append(f"  {meeting}")
    else:
        lines.append(f"HEAD-TO-HEAD: No recorded meetings between these teams.")
    lines.append("")

    # Tournament context
    stage = match.get("stage", "")
    lines.append(f"TOURNAMENT: {stage}")

    if "Group" in stage:
        lines.append(
            "  Stakes: Group stage — a loss is recoverable with matches remaining; "
            "a win puts team in a strong position to advance."
        )
    else:
        lines.append(
            "  Stakes: Knockout — elimination match. A draw after 90 minutes leads "
            "to extra time, then penalties."
        )

    return "\n".join(lines)

# ── match_contexts.json (for picks UI) ───────────────────────────────────────
def build_match_contexts_json(fixtures: list[dict]) -> None:
    """
    Write data/reference/match_contexts.json — compact context for each match,
    consumed by picks.html to show the Intel panel.
    """
    if not _load_all():
        log.warning("Skipping match_contexts.json — reference data not available")
        return

    out: dict[str, dict] = {}
    for match in fixtures:
        mid = match["match_id"]
        home = match["home"]
        away = match["away"]

        home = home or ""
        away = away or ""
        # Skip placeholder teams
        if any(p in home or p in away for p in ("Winner", "Runner", "Playoff", "TBD")):
            out[mid] = {"placeholder": True}
            continue

        home_m42 = _to_martj42(home)
        away_m42 = _to_martj42(away)

        home_elo = _elo.get(home_m42, _elo.get(home, 1500.0)) if _elo else 1500.0
        away_elo = _elo.get(away_m42, _elo.get(away, 1500.0)) if _elo else 1500.0

        hf = _get_form(home_m42)
        af = _get_form(away_m42)
        h2h = _get_h2h(home_m42, away_m42)
        venue_info = _get_venue(match.get("venue", ""))

        out[mid] = {
            "home_elo":      round(home_elo),
            "away_elo":      round(away_elo),
            "home_form5":    hf["results"][:5],
            "away_form5":    af["results"][:5],
            "home_w10":      hf["w"],
            "home_d10":      hf["d"],
            "home_l10":      hf["l"],
            "away_w10":      af["w"],
            "away_d10":      af["d"],
            "away_l10":      af["l"],
            "home_gpg":      hf["gpg"],
            "away_gpg":      af["gpg"],
            "h2h_played":    h2h["played"],
            "h2h_home_wins": h2h["home_wins"],
            "h2h_draws":     h2h["draws"],
            "h2h_away_wins": h2h["away_wins"],
            "altitude_m":    venue_info["altitude_m"] if venue_info else 0,
            "altitude_note": venue_info["altitude_note"] if venue_info and venue_info.get("altitude_note") else None,
            "climate_note":  venue_info["climate_note"] if venue_info else None,
        }

    dest = REF_DIR / "match_contexts.json"
    REF_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2))
    log.info(f"Written match_contexts.json ({len(out)} matches) → {dest}")
