#!/usr/bin/env python3
"""
make_calendar.py — generate site/picks.ics: one calendar reminder per
remaining matchday, firing before the pick deadline.

Players tap "Add reminders" on the picks site and get a native alarm for every
upcoming matchday — no login, no app, no contact info. The deadline is the same
absolute instant for everyone (first kickoff − 3h), written in UTC, so each
calendar app localises it correctly worldwide (DST-safe).

Run: python scripts/make_calendar.py   →  writes site/picks.ics
Idempotent; regenerated daily by score.yml so it always reflects the schedule.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
FIXTURES_PATH = ROOT / "data" / "fixtures" / "fixtures.json"
OUT_PATH = ROOT / "site" / "picks.ics"

PICKS_URL = "https://dakshjagwani.github.io/forecasting-arena-wc26/site/picks.html"
FREEZE_WINDOW = timedelta(hours=3)   # picks close 3h before first kickoff (matches freeze.py)
ALARM_LEAD = "-PT3H"                  # remind ~3h before the deadline
_UTC8 = timedelta(hours=8)           # UTC-8 campaign-day grouping (matches freeze.py/picks.js)

SUMMARY = "⚽ Your move vs the AI — predict now"
DESCRIPTION = (
    "5 AI models and the bookies have locked in today's forecasts. "
    "Make yours before kickoff and see who's actually calibrated — " + PICKS_URL
)


def group_by_utc8(fixtures: list) -> dict:
    """Group fixtures into campaign days, (kickoff - 8h).date(). Mirrors freeze.py."""
    day_map: dict[str, list] = {}
    for f in fixtures:
        dt = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
        day = (dt - _UTC8).strftime("%Y-%m-%d")
        day_map.setdefault(day, []).append(f)
    return day_map


def _fmt(dt: datetime) -> str:
    """iCal UTC timestamp, e.g. 20260626T160000Z."""
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escape(text: str) -> str:
    """RFC 5545 text escaping for SUMMARY/DESCRIPTION."""
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 content-line folding: ≤75 octets/line, continuations begin
    with a space. Octet-aware so multibyte chars (emoji, —) aren't split."""
    if len(line.encode("utf-8")) <= 75:
        return line
    chunks, cur, cur_b, first = [], "", 0, True
    for ch in line:
        clen = len(ch.encode("utf-8"))
        limit = 75 if first else 74          # continuation lines carry a leading space
        if cur_b + clen > limit:
            chunks.append(cur)
            cur, cur_b, first = ch, clen, False
        else:
            cur += ch
            cur_b += clen
    chunks.append(cur)
    return "\r\n ".join(chunks)


def build_ics(fixtures: list, now: datetime | None = None) -> str:
    """Pure generator: returns the full VCALENDAR text (CRLF line endings).
    One VEVENT per campaign day whose deadline is still in the future."""
    now = now or datetime.now(timezone.utc)
    dtstamp = _fmt(now)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Forecasting Arena//WC2026 Pick Deadlines//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:World Cup 2026 — Pick Deadlines",
        "X-WR-CALDESC:Reminders to submit your picks before each matchday.",
        "X-PUBLISHED-TTL:PT12H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
    ]

    day_map = group_by_utc8(fixtures)
    for day in sorted(day_map):
        matches = [m for m in day_map[day] if not m.get("is_placeholder")]
        if not matches:
            continue
        first_ko = min(
            datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00"))
            for m in matches
        )
        deadline = first_ko - FREEZE_WINDOW
        if deadline <= now:            # only future deadlines → re-downloads never re-add past days
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:md-{day}@forecasting-arena",      # stable per matchday → re-import updates, no dupes
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{_fmt(deadline)}",            # UTC → localises correctly worldwide
            f"DTEND:{_fmt(deadline + timedelta(minutes=15))}",
            f"SUMMARY:{_escape(SUMMARY)}",
            f"DESCRIPTION:{_escape(DESCRIPTION)}",
            f"URL:{PICKS_URL}",
            f"LOCATION:{PICKS_URL}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape(SUMMARY)}",
            f"TRIGGER:{ALARM_LEAD}",
            "END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def main() -> None:
    fixtures = json.loads(FIXTURES_PATH.read_text())
    ics = build_ics(fixtures)
    OUT_PATH.write_text(ics, encoding="utf-8")
    n_events = ics.count("BEGIN:VEVENT")
    print(f"Wrote {OUT_PATH} with {n_events} upcoming matchday reminder(s)")


if __name__ == "__main__":
    main()
