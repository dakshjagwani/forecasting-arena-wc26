"""
Unit tests for scoring, validation, parsing, and ingestion logic.
Run: pytest tests/
"""
import json, math, pytest
from datetime import datetime, timezone
from pathlib import Path

# Import from scripts/ via conftest.py sys.path manipulation
import freeze
from score import brier, outcome_from_score, is_qualified
from freeze import (normalise, parse_llm_response, to_slug, compute_crowd,
                    group_by_utc8, ingest_human_picks, match_odds)

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

    got_cal = json.loads((tmp_path / "data/scores/calibration.json").read_text())
    exp_cal = json.loads((GOLDEN_DIR / "expected_calibration.json").read_text())
    assert got_cal == exp_cal

    # The golden tree must also satisfy the repo-wide validator
    v = subprocess.run(
        [_sys.executable, str(ROOT / "scripts" / "validate_data.py")],
        env=env, capture_output=True, text=True,
    )
    assert v.returncode == 0, v.stderr
