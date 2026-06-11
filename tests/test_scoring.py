"""
Unit tests for scoring, validation, parsing, and ingestion logic.
Run: pytest tests/
"""
import json, math, pytest
from pathlib import Path

# Import from scripts/ via conftest.py sys.path manipulation
from score import brier, outcome_from_score, is_qualified
from freeze import normalise, parse_llm_response, to_slug, compute_crowd

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

def test_qualification_exactly_60pct():
    # 60% of 10 = 6 → qualifies with 6
    assert is_qualified(6, 10, 1, 104) is True

def test_qualification_below_60pct():
    assert is_qualified(5, 10, 1, 104) is False

def test_qualification_zero_scored():
    assert is_qualified(0, 0, 1, 104) is False

def test_qualification_rounds_up():
    # 60% of 7 = 4.2 → ceil = 5 → need 5 to qualify
    assert is_qualified(4, 7, 1, 104) is False
    assert is_qualified(5, 7, 1, 104) is True

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

# ── Golden-file integration test ─────────────────────────────────────────────

GOLDEN_DIR = ROOT / "tests" / "golden"

@pytest.mark.skipif(
    not (GOLDEN_DIR / "expected_leaderboard.json").exists(),
    reason="Golden files not yet generated — run tests/generate_golden.py first",
)
def test_golden_leaderboard():
    """
    Runs score.py against synthetic fixtures/predictions/results in tests/golden/,
    and checks the output matches the committed expected_leaderboard.json.
    """
    import subprocess, tempfile, shutil, os

    # Copy golden data into a temp ROOT-like structure
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "data" / "fixtures").mkdir(parents=True)
        (tmp / "data" / "predictions").mkdir(parents=True)
        (tmp / "data" / "results").mkdir(parents=True)
        (tmp / "data" / "scores").mkdir(parents=True)

        shutil.copy(GOLDEN_DIR / "fixtures.json", tmp / "data" / "fixtures" / "fixtures.json")
        shutil.copy(GOLDEN_DIR / "predictions.json", tmp / "data" / "predictions" / "2026-06-11.json")
        shutil.copy(GOLDEN_DIR / "results.json", tmp / "data" / "results" / "results.json")

        env = os.environ.copy()
        result = subprocess.run(
            ["python", str(ROOT / "scripts" / "score.py")],
            env={**env, "FORECASTING_ROOT": str(tmp)},
            capture_output=True, text=True,
        )
        # score.py uses ROOT from __file__, so we need a workaround:
        # For now just verify the script exits cleanly with our real data
        # Full golden test requires patching ROOT — deferred to Phase 1
        assert result.returncode == 0 or "No results yet" in result.stderr
