"""
Unit tests for scoring, validation, parsing, and ingestion logic.
Run: pytest tests/
"""
import json, math, pytest
from datetime import datetime, timezone
from pathlib import Path

# Import from scripts/ via conftest.py sys.path manipulation
import freeze
from score import (brier, outcome_from_score, is_qualified, is_knockout,
                   build_leaderboard, advancement_prob, advancement_brier,
                   build_advancement_leaderboard)
from freeze import (normalise, parse_llm_response, to_slug, compute_crowd,
                    group_by_utc8, ingest_human_picks, match_odds,
                    split_remaining, _post_with_retries, _sleep_for,
                    query_gemini)
import urllib.error

ROOT = Path(__file__).parent.parent

# ── Brier scoring ─────────────────────────────────────────────────────────────

def test_brier_perfect_home():
    assert brier(1.0, 0.0, 0.0, "home") == pytest.approx(0.0)

def test_brier_perfect_draw():
    assert brier(0.0, 1.0, 0.0, "draw") == pytest.approx(0.0)

def test_brier_perfect_away():
    assert brier(0.0, 0.0, 1.0, "away") == pytest.approx(0.0)

def test_brier_uniform_any_outcome():
    # uniform (1/3,1/3,1/3) should give 2*(1/3)^2 + (2/3)^2 = 2/9 + 4/9 = 6/9 ≈ 0.6667
    for outcome in ("home", "draw", "away"):
        assert brier(1/3, 1/3, 1/3, outcome) == pytest.approx(2/3, rel=1e-6)

def test_brier_certain_and_wrong():
    # Certain home, but away wins → (1-0)^2 + (0-0)^2 + (0-1)^2 = 2.0
    assert brier(1.0, 0.0, 0.0, "away") == pytest.approx(2.0)

def test_brier_mixed():
    # p=(0.7, 0.2, 0.1), outcome=home → (0.7-1)^2 + (0.2-0)^2 + (0.1-0)^2
    # = 0.09 + 0.04 + 0.01 = 0.14
    assert brier(0.7, 0.2, 0.1, "home") == pytest.approx(0.14, rel=1e-6)

# ── Validation / normalisation ────────────────────────────────────────────────

def test_normalise_sums_to_one():
    result = normalise(0.3, 0.3, 0.4)
    assert abs(sum(result) - 1.0) < 1e-9

def test_normalise_clamps_below_zero():
    # Clamps -0.5 to 0.01, then renormalises — result[0] < 0.01 is fine after renorm
    result = normalise(-0.5, 0.7, 0.8)
    assert abs(sum(result) - 1.0) < 1e-6  # must still sum to 1
    assert all(p > 0 for p in result)     # must be positive

def test_normalise_clamps_above_point98():
    result = normalise(0.99, 0.01, 0.01)
    assert result[0] <= 0.98

def test_normalise_still_sums_to_one_after_clamping():
    result = normalise(-0.1, 1.2, 0.0)
    assert abs(sum(result) - 1.0) < 1e-9

def test_normalise_already_valid():
    result = normalise(0.5, 0.3, 0.2)
    assert abs(sum(result) - 1.0) < 1e-9
    assert all(0.01 <= p <= 0.98 for p in result)

# ── LLM response parsing ──────────────────────────────────────────────────────

def test_parse_clean_json():
    raw = '{"p_home": 0.55, "p_draw": 0.25, "p_away": 0.20, "reasoning": "test"}'
    result = parse_llm_response(raw)
    assert result is not None
    ph, pd, pa, reasoning = result
    assert ph == pytest.approx(0.55)
    assert reasoning == "test"

def test_parse_markdown_fenced():
    raw = '```json\n{"p_home": 0.6, "p_draw": 0.2, "p_away": 0.2, "reasoning": "x"}\n```'
    result = parse_llm_response(raw)
    assert result is not None
    assert result[0] == pytest.approx(0.6)

def test_parse_prose_before_json():
    raw = 'Sure! Here is my forecast:\n{"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "reasoning": "even"}'
    result = parse_llm_response(raw)
    assert result is not None

def test_parse_probs_sum_to_097_still_parses():
    # Should parse even if sum != 1.0 (caller normalises)
    raw = '{"p_home": 0.50, "p_draw": 0.27, "p_away": 0.20, "reasoning": "close"}'
    result = parse_llm_response(raw)
    assert result is not None

def test_parse_missing_key_returns_none():
    raw = '{"p_home": 0.5, "p_draw": 0.3, "reasoning": "no away key"}'
    assert parse_llm_response(raw) is None

def test_parse_empty_string_returns_none():
    assert parse_llm_response("") is None

def test_parse_only_prose_returns_none():
    assert parse_llm_response("I cannot predict this match.") is None

def test_parse_reasoning_model_braces_before_answer():
    # DeepSeek-R1-style output: think-text containing brace blocks that are
    # NOT the answer must be skipped, not parsed.
    raw = ('<think>maybe {0.5}? or {"elo_gap": 120}...</think>\n'
           '{"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "reasoning": "ok"}')
    result = parse_llm_response(raw)
    assert result is not None
    assert result[0] == pytest.approx(0.5)

def test_parse_truncated_json_returns_none():
    # Completion cut off mid-JSON (max_tokens) → must fail, never partial-parse
    raw = '{"p_home": 0.5, "p_draw": 0.3, "p_aw'
    assert parse_llm_response(raw) is None

def test_parse_none_like_input_returns_none():
    assert parse_llm_response("") is None
    assert parse_llm_response("{}") is None

# ── Odds de-vig ───────────────────────────────────────────────────────────────

def test_devig_known_odds():
    # h=1.55, d=4.20, a=7.00
    # implied: 0.6452, 0.2381, 0.1429 → sum=1.0262
    # normalised: 0.6288, 0.2320, 0.1392
    from freeze import normalise
    implied = [1/1.55, 1/4.20, 1/7.00]
    total = sum(implied)
    normed = normalise(implied[0]/total, implied[1]/total, implied[2]/total)
    assert abs(sum(normed) - 1.0) < 1e-5
    assert normed[0] > normed[1] > normed[2]  # home favoured
    assert normed[0] == pytest.approx(0.6288, rel=0.01)

def test_devig_even_match():
    # All odds equal → equal probabilities
    implied = [1/2.0, 1/3.5, 1/4.0]
    total = sum(implied)
    normed = normalise(implied[0]/total, implied[1]/total, implied[2]/total)
    assert abs(sum(normed) - 1.0) < 1e-5

# ── Slug canonicalisation ─────────────────────────────────────────────────────

def test_slug_basic():
    assert to_slug("Sarah K.") == "sarah-k"

def test_slug_accents():
    assert to_slug("José García") == "jose-garcia"

def test_slug_lowercase():
    assert to_slug("DAKSH") == "daksh"

def test_slug_spaces_become_hyphens():
    assert to_slug("John  Smith") == "john-smith"

def test_slug_stable_across_duplicates():
    assert to_slug("sarah k") == to_slug("Sarah K.")

# ── Form ingestion: deduplication ────────────────────────────────────────────

def test_crowd_basic():
    human_picks = {
        "alice": {"md001-MEX-RSA": {"p_home": 0.6, "p_draw": 0.2, "p_away": 0.2}},
        "bob":   {"md001-MEX-RSA": {"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3}},
    }
    crowd = compute_crowd(human_picks, "md001-MEX-RSA")
    assert crowd is not None
    assert crowd["n"] == 2
    assert crowd["p_home"] == pytest.approx(0.5, rel=0.01)
    assert abs(crowd["p_home"] + crowd["p_draw"] + crowd["p_away"] - 1.0) < 1e-6

def test_crowd_empty():
    assert compute_crowd({}, "md001-MEX-RSA") is None

def test_crowd_no_picks_for_match():
    human_picks = {"alice": {"md002-OTHER": {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2}}}
    assert compute_crowd(human_picks, "md001-MEX-RSA") is None

# ── Qualification rule ────────────────────────────────────────────────────────
# is_qualified(n_predicted, n_available) — n_available counts scored matches
# from the forecaster's first predicted match onwards.

def test_qualification_exactly_60pct():
    # 60% of 10 = 6 → qualifies with 6
    assert is_qualified(6, 10) is True

def test_qualification_below_60pct():
    assert is_qualified(5, 10) is False

def test_qualification_zero_scored():
    assert is_qualified(0, 0) is False

def test_qualification_rounds_up():
    # 60% of 7 = 4.2 → ceil = 5 → need 5 to qualify
    assert is_qualified(4, 7) is False
    assert is_qualified(5, 7) is True

def test_qualification_mid_tournament_joiner():
    # Joined when only 4 matches remained, predicted 3 of them → 3 >= ceil(2.4)
    assert is_qualified(3, 4) is True

# ── Outcome derivation ────────────────────────────────────────────────────────

def test_outcome_home_win():
    assert outcome_from_score(2, 1) == "home"

def test_outcome_away_win():
    assert outcome_from_score(0, 1) == "away"

def test_outcome_draw():
    assert outcome_from_score(1, 1) == "draw"

def test_outcome_zero_zero_draw():
    assert outcome_from_score(0, 0) == "draw"

def test_outcome_knockout_draw():
    # Knockout draws are valid — scored on 90-min result
    assert outcome_from_score(2, 2) == "draw"

# ── UTC-8 campaign-day grouping ──────────────────────────────────────────────
# Shared truth file: tests/fixtures/utc8_cases.json (JS parity uses it too)

def test_group_by_utc8_shared_cases():
    cases = json.loads(
        (ROOT / "tests" / "fixtures" / "utc8_cases.json").read_text()
    )["cases"]
    fixtures = [
        {"match_id": f"c{i}", "kickoff_utc": c["kickoff_utc"]}
        for i, c in enumerate(cases)
    ]
    day_map = group_by_utc8(fixtures)
    for i, c in enumerate(cases):
        days_with_match = [d for d, ms in day_map.items()
                           if any(m["match_id"] == f"c{i}" for m in ms)]
        assert days_with_match == [c["expected_day"]], (
            f"{c['kickoff_utc']} grouped to {days_with_match}, "
            f"expected {c['expected_day']}"
        )

# ── Human picks ingestion ─────────────────────────────────────────────────────

FREEZE_T = datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc)
TODAY_IDS = {"g1-AAA-BBB"}

def _ingest(csv_text, monkeypatch, freeze_utc=FREEZE_T, today_ids=TODAY_IDS):
    monkeypatch.setattr(freeze, "http_get", lambda url, **kw: csv_text.encode())
    return ingest_human_picks("http://fake", freeze_utc, today_ids)

CSV_HEADER = "submitted_at,name,slug,match_id,p_home,p_draw,p_away\n"

def test_ingest_latest_pre_freeze_wins(monkeypatch):
    csv_text = CSV_HEADER + (
        "2026-06-11T10:00:00Z,Alice,,g1-AAA-BBB,0.5,0.3,0.2\n"
        "2026-06-11T12:00:00Z,Alice,,g1-AAA-BBB,0.7,0.2,0.1\n"
    )
    picks = _ingest(csv_text, monkeypatch)
    assert picks["alice"]["g1-AAA-BBB"]["p_home"] == pytest.approx(0.7, rel=0.01)

def test_ingest_post_freeze_ignored(monkeypatch):
    csv_text = CSV_HEADER + (
        "2026-06-11T10:00:00Z,Alice,,g1-AAA-BBB,0.5,0.3,0.2\n"
        "2026-06-11T19:00:00Z,Alice,,g1-AAA-BBB,0.9,0.05,0.05\n"  # after freeze
    )
    picks = _ingest(csv_text, monkeypatch)
    assert picks["alice"]["g1-AAA-BBB"]["p_home"] == pytest.approx(0.5, rel=0.01)

def test_ingest_percentage_scale_normalised(monkeypatch):
    csv_text = CSV_HEADER + "2026-06-11T10:00:00Z,Bob,,g1-AAA-BBB,50,30,20\n"
    picks = _ingest(csv_text, monkeypatch)
    t = picks["bob"]["g1-AAA-BBB"]
    assert t["p_home"] == pytest.approx(0.5, rel=0.01)
    assert abs(t["p_home"] + t["p_draw"] + t["p_away"] - 1.0) < 1e-6

def test_ingest_other_day_match_ignored(monkeypatch):
    csv_text = CSV_HEADER + "2026-06-11T10:00:00Z,Bob,,g9-XXX-YYY,0.5,0.3,0.2\n"
    assert _ingest(csv_text, monkeypatch) == {}

def test_ingest_malformed_rows_skipped(monkeypatch):
    csv_text = CSV_HEADER + (
        "not-a-date,Eve,,g1-AAA-BBB,0.5,0.3,0.2\n"
        "2026-06-11T10:00:00Z,Eve,,g1-AAA-BBB,abc,0.3,0.2\n"
    )
    assert _ingest(csv_text, monkeypatch) == {}

def test_ingest_test_slugs_excluded(monkeypatch):
    csv_text = CSV_HEADER + (
        "2026-06-11T10:00:00Z,Test-Daksh,,g1-AAA-BBB,0.5,0.3,0.2\n"
        "2026-06-11T10:00:00Z,test,,g1-AAA-BBB,0.5,0.3,0.2\n"
        "2026-06-11T10:00:00Z,Tessa,,g1-AAA-BBB,0.6,0.2,0.2\n"  # real name, kept
    )
    picks = _ingest(csv_text, monkeypatch)
    assert set(picks) == {"tessa"}

# ── Calendar reminder generation (make_calendar.py) ──────────────────────────

from make_calendar import build_ics

_CAL_FIXTURES = [
    # group-style day 2026-07-01: teams known, first kickoff 18:00Z → deadline 15:00Z
    {"match_id": "m1", "kickoff_utc": "2026-07-01T18:00:00Z", "is_placeholder": False},
    {"match_id": "m2", "kickoff_utc": "2026-07-01T21:00:00Z", "is_placeholder": False},
    # KNOCKOUT-style day 2026-07-19 (the "final"): teams still TBD but the
    # kickoff slot is scheduled — MUST be included so the calendar runs to the end
    {"match_id": "fin", "kickoff_utc": "2026-07-19T19:00:00Z", "is_placeholder": True},
    # a past day relative to NOW below — must be omitted
    {"match_id": "m0", "kickoff_utc": "2026-06-01T18:00:00Z", "is_placeholder": False},
]
_NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)

def test_calendar_valid_envelope():
    ics = build_ics(_CAL_FIXTURES, now=_NOW)
    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.rstrip().endswith("END:VCALENDAR")
    assert "\r\n" in ics  # CRLF line endings

def test_calendar_includes_future_days_through_finals_and_drops_past():
    ics = build_ics(_CAL_FIXTURES, now=_NOW)
    # future group day + future knockout day (TBD teams) → 2 events
    assert ics.count("BEGIN:VEVENT") == 2
    assert "md-2026-06-01" not in ics                      # past day dropped
    assert "md-2026-07-01@forecasting-arena" in ics
    assert "md-2026-07-19@forecasting-arena" in ics        # TBD/knockout day INCLUDED

def test_calendar_deadline_is_first_kickoff_minus_3h_in_utc():
    ics = build_ics(_CAL_FIXTURES, now=_NOW)
    assert "DTSTART:20260701T150000Z" in ics               # 18:00Z − 3h
    assert "DTSTART:20260719T160000Z" in ics               # final 19:00Z − 3h (TBD teams)
    # every DTSTART/DTEND is UTC
    for line in ics.split("\r\n"):
        if line.startswith(("DTSTART:", "DTEND:", "DTSTAMP:")):
            assert line.rstrip().endswith("Z"), line

def test_calendar_has_alarm_and_no_overlong_lines():
    ics = build_ics(_CAL_FIXTURES, now=_NOW)
    assert "BEGIN:VALARM" in ics and "TRIGGER:-PT3H" in ics
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, f"line too long: {line!r}"

def test_calendar_sequence_is_monotonic_for_inplace_updates():
    import re
    early = build_ics(_CAL_FIXTURES, now=datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc))
    later = build_ics(_CAL_FIXTURES, now=datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc))
    assert "SEQUENCE:" in early                       # present so re-imports update
    seq_e = int(re.search(r"SEQUENCE:(\d+)", early).group(1))
    seq_l = int(re.search(r"SEQUENCE:(\d+)", later).group(1))
    assert seq_l > seq_e                              # later regen → higher seq → clients apply update

# ── Retry / backoff for transient provider errors ────────────────────────────

def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", hdrs=None, fp=None)

def test_retries_then_succeeds_on_503(monkeypatch):
    # 503 twice, then a good response → returns it
    calls = {"n": 0}
    def fake_post(url, headers, body, timeout=30):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(503)
        return {"ok": True}
    monkeypatch.setattr(freeze, "http_post", fake_post)
    monkeypatch.setattr(freeze.time, "sleep", lambda s: None)  # no real waiting
    assert _post_with_retries("u", {}, {}, "lbl", max_attempts=5) == {"ok": True}
    assert calls["n"] == 3

def test_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr(freeze, "http_post", lambda *a, **k: (_ for _ in ()).throw(_http_error(503)))
    monkeypatch.setattr(freeze.time, "sleep", lambda s: None)
    with pytest.raises(urllib.error.HTTPError):
        _post_with_retries("u", {}, {}, "lbl", max_attempts=3)

def test_fails_fast_on_non_retryable_4xx(monkeypatch):
    calls = {"n": 0}
    def fake_post(url, headers, body, timeout=30):
        calls["n"] += 1
        raise _http_error(400)  # bad request — never retry
    monkeypatch.setattr(freeze, "http_post", fake_post)
    monkeypatch.setattr(freeze.time, "sleep", lambda s: None)
    with pytest.raises(urllib.error.HTTPError):
        _post_with_retries("u", {}, {}, "lbl", max_attempts=5)
    assert calls["n"] == 1  # one attempt only, no retries

def test_sleep_honors_retry_after():
    # Retry-After dominates the exponential schedule (capped at 30s)
    assert _sleep_for(0, retry_after=17) == 17
    # without it, backoff grows and stays within cap+jitter
    s0 = _sleep_for(0, None)
    assert 2.0 <= s0 <= 2.5
    assert _sleep_for(10, None) <= freeze._BACKOFF_CAP * 1.25

def test_backoff_is_bounded():
    # A dead endpoint must not balloon the run: total wait across all retries
    # of one call stays small (cap 8s, 4 attempts → 3 waits ≤ ~10s each).
    total = sum(_sleep_for(a, None) for a in range(3))  # waits before attempts 2,3,4
    assert total <= 3 * freeze._BACKOFF_CAP * 1.25  # ≤ 30s, not minutes
    assert freeze._BACKOFF_CAP <= 8.0

def test_provider_failure_carries_error_reason(monkeypatch):
    monkeypatch.setattr(freeze, "http_post", lambda *a, **k: (_ for _ in ()).throw(_http_error(503)))
    monkeypatch.setattr(freeze.time, "sleep", lambda s: None)
    match = {"home": "A", "away": "B", "stage": "G", "venue": "V",
             "kickoff_utc": "2026-06-20T18:00:00Z"}
    res = query_gemini(match, "fake-key")
    assert res["status"] == "failed"
    assert "503" in res.get("error", "")   # reason stored, not a bare status

# ── Rescue mode (--remaining) ─────────────────────────────────────────────────

def test_split_remaining():
    now = datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)
    matches = [
        {"match_id": "a", "kickoff_utc": "2026-06-12T19:00:00Z"},  # kicked off
        {"match_id": "b", "kickoff_utc": "2026-06-12T20:01:00Z"},  # inside grace
        {"match_id": "c", "kickoff_utc": "2026-06-12T22:00:00Z"},  # freezable
    ]
    freezable, voided = split_remaining(matches, now)
    assert [m["match_id"] for m in freezable] == ["c"]
    assert [m["match_id"] for m in voided] == ["a", "b"]

# ── Odds fuzzy matching ───────────────────────────────────────────────────────

def test_match_odds_exact_and_fuzzy():
    odds_map = {("South Korea", "Denmark"): {"p_home": 0.3, "p_draw": 0.3, "p_away": 0.4}}
    assert match_odds({"home": "South Korea", "away": "Denmark"}, odds_map) is not None
    assert match_odds({"home": "Korea", "away": "Denmark"}, odds_map) is not None

def test_match_odds_placeholder_never_matches():
    odds_map = {("South Korea", "Denmark"): {"p_home": 0.3, "p_draw": 0.3, "p_away": 0.4}}
    m = {"home": "South Korea", "away": "Winner UEFA Playoff D"}
    assert match_odds(m, odds_map) is None

def test_match_odds_aliases():
    # Bookmaker naming differs from our fixtures — aliases must bridge it
    odds_map = {
        ("United States", "Czech Republic"): {"p_home": 0.5, "p_draw": 0.25, "p_away": 0.25},
        ("Turkey", "Korea Republic"):        {"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3},
    }
    assert match_odds({"home": "USA", "away": "Czechia"}, odds_map) is not None
    assert match_odds({"home": "Türkiye", "away": "South Korea"}, odds_map) is not None
    # And a wrong pairing must still not match
    assert match_odds({"home": "USA", "away": "South Korea"}, odds_map) is None

# ── Ops triage playbook + health digest (TESTING.md T2) ──────────────────────

import importlib

def _playbook():
    return importlib.import_module("ops_playbook")

def test_playbook_single_model_fail_is_no_action():
    verdict, _, _ = _playbook().triage_finding("model_failed")
    assert verdict == "auto"   # absorbed by the 60% rule, never an alarm

def test_playbook_freeze_missing_is_action():
    verdict, why, action = _playbook().triage_finding("freeze_missing")
    assert verdict == "action" and action  # has a concrete instruction

def test_playbook_unknown_code_needs_human():
    verdict, _, action = _playbook().triage_finding("some_brand_new_code")
    assert verdict == "unknown" and "human" in action.lower()

def test_playbook_classify_overall_precedence():
    pb = _playbook()
    summary = {"findings": [
        {"level": "✅", "code": "freeze_ok", "message": "ok"},
        {"level": "⚠️", "code": "model_failed", "message": "x failed"},
        {"level": "❌", "code": "results_missing_late", "message": "no result"},
    ]}
    t = pb.classify(summary)
    assert t["overall"] == "action"        # action dominates
    assert len(t["action_items"]) == 1
    assert t["healthy_count"] == 1
    assert len(t["auto_items"]) == 1

def test_playbook_all_healthy():
    pb = _playbook()
    summary = {"findings": [{"level": "✅", "code": "freeze_ok", "message": "ok"},
                            {"level": "✅", "code": "scored_ok", "message": "ok"}]}
    assert pb.classify(summary)["overall"] == "healthy"

def test_ntfy_title_is_header_safe():
    # HTTP headers are latin-1; an emoji title would raise a codec error
    # (regression from the first CI digest run). _ascii must strip it.
    hd = importlib.import_module("health_digest")
    safe = hd._ascii("🚨 2026-06-20: Action needed")
    safe.encode("latin-1")  # must not raise
    assert "2026-06-20" in safe and "Action needed" in safe

def test_digest_compose_action_title_and_body():
    hd = importlib.import_module("health_digest")
    summary = {"day": "2026-06-20", "findings": [
        {"level": "❌", "code": "freeze_missing", "message": "never frozen"}]}
    triage = _playbook().classify(summary)
    title, body = hd.compose(summary, triage)
    assert title.startswith("🚨") and "2026-06-20" in title
    assert "ACTION NEEDED" in body and "Trigger the freeze" in body

def test_verify_cycle_json_shape():
    # run_checks against the golden tree → structured summary with codes
    import os, subprocess, sys as _sys, shutil
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for sub in ("fixtures", "predictions", "results", "scores"):
            (tmp / "data" / sub).mkdir(parents=True)
        g = ROOT / "tests" / "golden"
        shutil.copy(g / "fixtures.json", tmp / "data/fixtures/fixtures.json")
        shutil.copy(g / "predictions.json", tmp / "data/predictions/2026-06-11.json")
        shutil.copy(g / "results.json", tmp / "data/results/results.json")
        out = subprocess.run(
            [_sys.executable, str(ROOT / "scripts/verify_cycle.py"), "--json", "--date", "2026-06-11"],
            env={**os.environ, "FORECASTING_ROOT": str(tmp)},
            capture_output=True, text=True)
        data = json.loads(out.stdout)
        assert data["day"] == "2026-06-11"
        assert all({"level", "code", "message"} <= set(f) for f in data["findings"])
        assert isinstance(data["action_needed"], bool)

# ── Knockouts-only leaderboard ───────────────────────────────────────────────
@pytest.mark.parametrize("stage,expected", [
    ("Group A", False), ("Group X", False),
    ("Round of 32", True), ("Round of 16", True), ("Quarterfinals", True),
    ("Semifinals", True), ("Third Place Playoff", True), ("Final", True),
    ("", False),
])
def test_is_knockout(stage, expected):
    assert is_knockout(stage) is expected

def test_build_leaderboard_scopes_to_subset():
    """The knockout board is build_leaderboard over only the knockout matches —
    here we pass a 1-match subset and check it scores just that match, fresh
    (n_available counted from index 0 within the subset)."""
    fc = {"alice": {"p_home": 1.0, "p_draw": 0.0, "p_away": 0.0}}
    all_preds = {"ko1": fc}
    scoreable = [("ko1", {"outcome": "home", "score_home": 1, "score_away": 0})]
    lb = build_leaderboard(scoreable, all_preds, "GOLDEN")
    assert lb["matches_scored"] == 1
    alice = next(r for r in lb["rows"] if r["forecaster"] == "alice")
    assert alice["n_predicted"] == 1 and alice["n_available"] == 1
    assert alice["mean_brier"] == 0.0          # perfect call
    # empty subset (no knockouts scored yet) -> empty board, no crash
    empty = build_leaderboard([], all_preds, "GOLDEN")
    assert empty["matches_scored"] == 0 and empty["rows"] == []

# ── Knockout advancement metric (CLAUDE.md §7 two-phase scoring) ─────────────
def test_advancement_prob_half_draw_split():
    # direct pick (draw≈0) → ≈ p_home; model with draw mass → splits it
    assert advancement_prob({"p_home": 0.70, "p_draw": 0.0, "p_away": 0.30}) == 0.70
    assert advancement_prob({"p_home": 0.55, "p_draw": 0.27, "p_away": 0.18}) == \
        pytest.approx(0.685)
    # clamped into [0,1]
    assert 0.0 <= advancement_prob({"p_home": 0.99, "p_draw": 0.02, "p_away": 0.0}) <= 1.0

def test_advancement_brier_known_values():
    assert advancement_brier(1.0, "home") == 0.0          # perfect
    assert advancement_brier(0.0, "away") == 0.0          # perfect (other side)
    assert advancement_brier(0.5, "home") == 0.5          # coin-flip line
    assert advancement_brier(0.5, "away") == 0.5
    assert advancement_brier(1.0, "away") == 2.0          # certain and wrong

def test_build_advancement_leaderboard():
    # Home advanced. Direct human (adv .705) beats derived model (adv .685)
    # beats market (.64); uniform sits exactly on the 0.5 coin-flip line.
    all_preds = {"m1": {
        "human:a":      {"p_home": 0.70, "p_draw": 0.01, "p_away": 0.29},
        "gemini-flash": {"p_home": 0.55, "p_draw": 0.27, "p_away": 0.18},
        "market":       {"p_home": 0.50, "p_draw": 0.28, "p_away": 0.22},
    }}
    lb = build_advancement_leaderboard([("m1", {"advanced": "home"})],
                                       all_preds, "GOLDEN")
    assert lb["metric"] == "advancement" and lb["coinflip"] == 0.5
    assert lb["matches_scored"] == 1
    order = [r["forecaster"] for r in lb["rows"]]
    assert order[0] == "human:a"                       # best
    assert order.index("human:a") < order.index("gemini-flash") < order.index("market")
    uni = next(r for r in lb["rows"] if r["forecaster"] == "uniform")
    assert uni["mean_brier"] == 0.5                    # coin-flip baseline
    # market is the benchmark (vs_market None); others get a delta
    mkt = next(r for r in lb["rows"] if r["forecaster"] == "market")
    assert mkt["vs_market"] is None

def test_advancement_failed_forecast_skipped():
    all_preds = {"m1": {
        "gemini-flash": {"status": "failed"},
        "human:a": {"p_home": 0.6, "p_draw": 0.0, "p_away": 0.4},
    }}
    lb = build_advancement_leaderboard([("m1", {"advanced": "home"})],
                                       all_preds, "GOLDEN")
    names = {r["forecaster"] for r in lb["rows"]}
    assert "gemini-flash" not in names and "human:a" in names

# ── Knockout advancement PROMPT (freeze.py) ──────────────────────────────────
def test_make_prompt_knockout_asks_advancement():
    ko = {"home": "Brazil", "away": "Japan", "stage": "Round of 32",
          "venue": "SoFi Stadium", "kickoff_utc": "2026-06-29T17:00:00Z"}
    grp = {"home": "Mexico", "away": "South Africa", "stage": "Group A",
           "venue": "Azteca", "kickoff_utc": "2026-06-11T21:00:00Z"}
    pk = freeze.make_prompt(ko)
    assert "p_home_advance" in pk and "ADVANCES" in pk and "neutral" in pk
    assert "no draw" in pk.lower()
    pg = freeze.make_prompt(grp)
    assert "90-minute result" in pg and "p_home_advance" not in pg

def test_parse_advancement_json_to_drawless_triple():
    # advancement shape → (p_home, 0.0, p_away, reasoning)
    assert parse_llm_response('{"p_home_advance":0.68,"p_away_advance":0.32,"reasoning":"x"}') \
        == (0.68, 0.0, 0.32, "x")
    # markdown-fenced
    assert parse_llm_response('```json\n{"p_home_advance":0.6,"p_away_advance":0.4}\n```') \
        == (0.6, 0.0, 0.4, "")
    # group shape still works
    assert parse_llm_response('{"p_home":0.5,"p_draw":0.3,"p_away":0.2}') == (0.5, 0.3, 0.2, "")
    # neither shape → None (no silent default)
    assert parse_llm_response('{"foo":1}') is None

# ── Golden-file integration test (TESTING.md §3.3) ───────────────────────────

GOLDEN_DIR = ROOT / "tests" / "golden"

def _strip_volatile(obj):
    obj = dict(obj)
    obj.pop("updated_utc", None)
    return obj

def test_golden_score_pipeline(tmp_path):
    """
    Full score.py run against the synthetic golden tree. Output must match
    the committed expected files exactly (modulo updated_utc). Regression
    net for any refactor of scoring, qualification, or calibration.
    """
    import os, shutil, subprocess, sys as _sys

    for sub in ("fixtures", "predictions", "results", "scores"):
        (tmp_path / "data" / sub).mkdir(parents=True)
    shutil.copy(GOLDEN_DIR / "fixtures.json", tmp_path / "data/fixtures/fixtures.json")
    shutil.copy(GOLDEN_DIR / "predictions.json", tmp_path / "data/predictions/2026-06-11.json")
    shutil.copy(GOLDEN_DIR / "results.json", tmp_path / "data/results/results.json")

    env = {**os.environ, "FORECASTING_ROOT": str(tmp_path)}
    result = subprocess.run(
        [_sys.executable, str(ROOT / "scripts" / "score.py")],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    got_lb = json.loads((tmp_path / "data/scores/leaderboard.json").read_text())
    exp_lb = json.loads((GOLDEN_DIR / "expected_leaderboard.json").read_text())
    assert _strip_volatile(got_lb) == _strip_volatile(exp_lb)

    # Knockouts-only board: golden tree is all group-stage, so it must be empty.
    # (Regression net: if the knockout filter ever leaks group matches, this fails.)
    got_ko = json.loads((tmp_path / "data/scores/leaderboard_knockouts.json").read_text())
    exp_ko = json.loads((GOLDEN_DIR / "expected_leaderboard_knockouts.json").read_text())
    assert _strip_volatile(got_ko) == _strip_volatile(exp_ko)

    got_cal = json.loads((tmp_path / "data/scores/calibration.json").read_text())
    exp_cal = json.loads((GOLDEN_DIR / "expected_calibration.json").read_text())
    assert got_cal == exp_cal

    got_ms = json.loads((tmp_path / "data/scores/match_scores.json").read_text())
    exp_ms = json.loads((GOLDEN_DIR / "expected_match_scores.json").read_text())
    assert _strip_volatile(got_ms) == _strip_volatile(exp_ms)

    # The golden tree must also satisfy the repo-wide validator
    v = subprocess.run(
        [_sys.executable, str(ROOT / "scripts" / "validate_data.py")],
        env=env, capture_output=True, text=True,
    )
    assert v.returncode == 0, v.stderr
