#!/usr/bin/env python3
"""
freeze.py — Daily pre-kickoff freeze.

Fetches odds, queries LLMs, ingests human picks from Google Sheet,
validates all triples, writes data/predictions/YYYY-MM-DD.json.

Run manually: python scripts/freeze.py
              python scripts/freeze.py --date 2026-06-11
              python scripts/freeze.py --dry-run
"""
from __future__ import annotations
import csv, json, logging, os, random, re, sys, time, unicodedata, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
FIXTURES_PATH = ROOT / "data" / "fixtures" / "fixtures.json"
PREDICTIONS_DIR = ROOT / "data" / "predictions"

# ── FINAL MODEL LINEUP — frozen at relaunch (see CHANGELOG.md 2026-06-11) ─────
# Single source of truth. The freeze aborts if the forecasts written disagree
# with this registry. Adding/removing a model after the experiment's first
# scored matchday invalidates the comparison — a CHANGELOG.md entry is
# mandatory to change this tuple, and only ever to RETIRE a model, never swap.
AI_LINEUP = ("gemini-flash", "llama-70b", "gemma", "gpt-oss-120b", "gpt-4o-mini")
NON_AI_FORECASTERS = ("market", "crowd")

# Picks close this long before the day's FIRST kickoff. First kickoffs range
# from 16:00Z to 22:00Z across the tournament, so no fixed clock time works —
# scheduled runs fire every 30 min and self-gate: too early → no-op, already
# frozen → no-op, inside the window → freeze. Keep in sync with
# FREEZE_LEAD_MS in site/picks.js and ops.html.
FREEZE_WINDOW = timedelta(hours=3)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── .env loader ───────────────────────────────────────────────────────────────
def load_dotenv():
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

# ── Slug (identical logic to picks.js toSlug) ────────────────────────────────
def to_slug(name: str) -> str:
    n = unicodedata.normalize("NFD", name.strip().lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", n).strip("-")

def is_knockout(stage: str) -> bool:
    """Any stage that isn't a group is a knockout (Round of 32 … Final + Third
    Place Playoff). From the knockout phase the question is who ADVANCES, not the
    90-minute result — see the _KO prompts and CLAUDE.md §7 two-phase scoring."""
    return bool(stage) and not stage.startswith("Group")

# ── Validation / normalisation ────────────────────────────────────────────────
def normalise(p_home: float, p_draw: float, p_away: float) -> list:
    """Clamp each prob to [0.01, 0.98] then renormalise to sum 1.0."""
    probs = [max(0.01, min(0.98, p)) for p in (p_home, p_draw, p_away)]
    total = sum(probs)
    return [round(p / total, 6) for p in probs]

# ── LLM response parser ───────────────────────────────────────────────────────
def parse_llm_response(raw: str):
    """
    Extract (p_home, p_draw, p_away, reasoning) from raw LLM output.
    Handles markdown fences, prose before/after the JSON, and reasoning-model
    output where earlier {...} blocks (e.g. inside <think> text) are not the
    answer: every brace-delimited candidate is tried, first one carrying the
    probability keys wins. Returns None on any parse failure — never a silent
    default.

    Two accepted shapes (auto-detected so call sites don't branch):
      • group:    {"p_home","p_draw","p_away", …}
      • knockout: {"p_home_advance","p_away_advance", …}  → returned as a triple
                  with p_draw=0.0 (a knockout forecast is just a draw-less
                  triple; normalise() then clamps it exactly like a human's
                  tug-of-war pick, keeping models and humans on identical footing).
    """
    if not raw:
        return None
    text = re.sub(r"```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    for m in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(m.group())
        except json.JSONDecodeError:
            continue
        reasoning = str(obj.get("reasoning", ""))[:200]
        try:
            return (float(obj["p_home"]), float(obj["p_draw"]),
                    float(obj["p_away"]), reasoning)
        except (KeyError, ValueError, TypeError):
            pass
        try:  # knockout advancement shape (no draw)
            return (float(obj["p_home_advance"]), 0.0,
                    float(obj["p_away_advance"]), reasoning)
        except (KeyError, ValueError, TypeError):
            continue
    return None

# ── HTTP helpers ──────────────────────────────────────────────────────────────
def http_get(url: str, headers: dict | None = None, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "python-requests/2.31.0",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def http_post(url: str, headers: dict, body: dict, timeout: int = 30) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "User-Agent": "python-requests/2.31.0",
        **headers,
    }, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

# ── Shared retry wrapper for all LLM providers ────────────────────────────────
# Transient free-tier failures (HTTP 503 overload, 429 rate-limit, gateway
# errors, timeouts) are common and clear in seconds. We retry those with
# exponential backoff + jitter, honouring Retry-After when present. Other 4xx
# (400 bad request, 401/403 bad key) are permanent — fail fast, don't hammer.
# temperature=0 makes each model deterministic, so retrying only affects
# WHETHER we receive the model's one fixed answer, never what it is.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# Backoff is bounded on purpose: a genuinely-dead endpoint must not balloon the
# freeze (a local dry-run with an unreachable provider burned ~30 min before we
# capped this). 4 attempts with an 8s cap → ≤ ~14s of waits per call, so even
# every model failing every match + the sweep stays a few minutes, inside 3h.
_BACKOFF_CAP = 8.0

def _sleep_for(attempt: int, retry_after: float | None) -> float:
    """Backoff for a given 0-based attempt: honour Retry-After, else
    exponential (2,4,8…) capped, with jitter. Pure → unit-testable."""
    if retry_after is not None and retry_after > 0:
        return min(retry_after, 30.0)
    base = min(2.0 * (2 ** attempt), _BACKOFF_CAP)
    return base + random.uniform(0, base * 0.25)

def _post_with_retries(url: str, headers: dict, body: dict, label: str,
                       max_attempts: int = 4) -> dict:
    """POST with retries on transient errors. Returns parsed JSON, or raises
    the last error after exhausting attempts. Fails fast on non-retryable 4xx."""
    last_err: Exception = RuntimeError("no attempt made")
    for attempt in range(max_attempts):
        try:
            return http_post(url, headers, body)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in _RETRYABLE_STATUS:
                log.warning(f"  {label}: HTTP {e.code} (non-retryable) — giving up")
                raise
            retry_after = None
            try:
                ra = e.headers.get("Retry-After") if e.headers else None
                retry_after = float(ra) if ra and ra.isdigit() else None
            except Exception:
                retry_after = None
            log.warning(f"  {label} attempt {attempt + 1}/{max_attempts}: HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            log.warning(f"  {label} attempt {attempt + 1}/{max_attempts}: {e}")
            retry_after = None
        if attempt < max_attempts - 1:
            time.sleep(_sleep_for(attempt, retry_after))
    raise last_err

# ── LLM prompt ───────────────────────────────────────────────────────────────
_PROMPT_ENRICHED = (
    "You are forecasting a FIFA World Cup 2026 match.\n"
    "Use the statistical context below to give a calibrated probability estimate.\n\n"
    "{context_block}\n\n"
    "Give your probability estimate for the 90-minute result "
    "(a draw is a valid outcome, even in knockout rounds).\n"
    'Respond with ONLY this JSON, no other text:\n'
    '{{"p_home": <float>, "p_draw": <float>, "p_away": <float>, "reasoning": "<max 50 words>"}}\n'
    "The three probabilities must sum to 1.0."
)

_PROMPT_FALLBACK = (
    "You are forecasting a FIFA World Cup 2026 match.\n"
    "Match: {home} vs {away}, {stage}, {venue}, {date}.\n"
    "Give your honest probability estimate for the 90-minute result.\n"
    'Respond with ONLY this JSON, no other text:\n'
    '{{"p_home": <float>, "p_draw": <float>, "p_away": <float>, "reasoning": "<max 50 words>"}}\n'
    "The three probabilities must sum to 1.0."
)

# Knockout phase: the question is who ADVANCES (extra time + penalties decide a
# 90-minute tie), so there is no draw. Same enriched context block as the group
# prompt — only the question changes. Stored as a draw-less triple. See
# CLAUDE.md §5/§7 and methodology.html (pre-registered 2026-06-28).
_PROMPT_ENRICHED_KO = (
    "You are forecasting a FIFA World Cup 2026 KNOCKOUT match.\n"
    "Use the statistical context below to give a calibrated estimate.\n\n"
    "{context_block}\n\n"
    "This is a knockout tie: if level after 90 minutes it is decided by extra "
    "time and then penalties, so there is NO draw — exactly one team advances.\n"
    "Estimate the probability that each team ADVANCES to the next round. The two "
    "team labels are listing order only; the venue is neutral (no home advantage) "
    "unless a host nation (USA, Mexico, Canada) is playing in its own country.\n"
    'Respond with ONLY this JSON, no other text:\n'
    '{{"p_home_advance": <float>, "p_away_advance": <float>, "reasoning": "<max 50 words>"}}\n'
    "The two probabilities must sum to 1.0 (p_home_advance is the first-listed team)."
)

_PROMPT_FALLBACK_KO = (
    "You are forecasting a FIFA World Cup 2026 KNOCKOUT match.\n"
    "Match: {home} vs {away}, {stage}, {venue}, {date}.\n"
    "If level after 90 minutes it is decided by extra time then penalties — there "
    "is no draw, exactly one team advances. The venue is neutral (no home "
    "advantage) unless a host nation plays at home.\n"
    "Estimate the probability that {home} advances and that {away} advances.\n"
    'Respond with ONLY this JSON, no other text:\n'
    '{{"p_home_advance": <float>, "p_away_advance": <float>, "reasoning": "<max 50 words>"}}\n'
    "The two probabilities must sum to 1.0 (p_home_advance is {home})."
)

def make_prompt(match: dict) -> str:
    ko = is_knockout(match.get("stage", ""))
    enriched_tmpl = _PROMPT_ENRICHED_KO if ko else _PROMPT_ENRICHED
    fallback_tmpl = _PROMPT_FALLBACK_KO if ko else _PROMPT_FALLBACK
    try:
        from context_builder import get_context_block
        ctx = get_context_block(match)
        if ctx:
            return enriched_tmpl.format(context_block=ctx)
    except Exception:
        pass
    return fallback_tmpl.format(
        home=match["home"], away=match["away"],
        stage=match["stage"], venue=match["venue"],
        date=match["kickoff_utc"][:10],
    )

# ── LLM providers ────────────────────────────────────────────────────────────
def _parse_and_normalise(raw: str, forecaster: str) -> dict:
    parsed = parse_llm_response(raw)
    if not parsed:
        log.warning(f"  {forecaster}: could not parse response")
        return {"status": "failed", "raw": raw[:2000]}
    ph, pd, pa, reasoning = parsed
    normed = normalise(ph, pd, pa)
    # raw kept near-verbatim (4k cap) — it feeds the bias lab and post-hoc
    # analysis; reasoning models put their substance before the JSON.
    return {
        "p_home": normed[0], "p_draw": normed[1], "p_away": normed[2],
        "reasoning": reasoning, "raw": raw[:4000], "status": "ok",
    }

def query_gemini(match: dict, api_key: str, model: str = "gemini-2.5-flash") -> dict:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": make_prompt(match)}]}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 512,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        resp = _post_with_retries(url, {}, body, "gemini-flash")
        raw = resp["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_and_normalise(raw, "gemini-flash")
    except Exception as e:
        return {"status": "failed", "error": str(e)[:200]}

def query_groq(match: dict, api_key: str, model: str = "llama-3.3-70b-versatile",
               forecaster_id: str = "llama-70b", max_tokens: int = 300) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": make_prompt(match)}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    try:
        resp = _post_with_retries(
            "https://api.groq.com/openai/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}"}, body, forecaster_id,
        )
        raw = resp["choices"][0]["message"]["content"]
        return _parse_and_normalise(raw, forecaster_id)
    except Exception as e:
        return {"status": "failed", "error": str(e)[:200]}

def query_openrouter(match: dict, api_key: str,
                     model: str = "google/gemma-4-31b-it:free",
                     forecaster_id: str = "gemma",
                     max_tokens: int = 512) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": make_prompt(match)}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    try:
        resp = _post_with_retries(
            "https://openrouter.ai/api/v1/chat/completions",
            {
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/dakshjagwani/forecasting-arena-wc26",
                "X-Title": "Forecasting Arena WC 2026",
            },
            body, forecaster_id,
        )
        raw = resp["choices"][0]["message"]["content"]
        return _parse_and_normalise(raw, forecaster_id)
    except Exception as e:
        return {"status": "failed", "error": str(e)[:200]}

def query_github_models(match: dict, token: str,
                        model: str = "openai/gpt-4o-mini",
                        forecaster_id: str = "gpt-4o-mini") -> dict:
    """GitHub Models free inference tier — OpenAI-compatible, auth via a
    GitHub token with models:read (the built-in GITHUB_TOKEN works in CI)."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": make_prompt(match)}],
        "temperature": 0.0,
        "max_tokens": 300,
    }
    try:
        resp = _post_with_retries(
            "https://models.github.ai/inference/chat/completions",
            {"Authorization": f"Bearer {token}"}, body, forecaster_id,
        )
        raw = resp["choices"][0]["message"]["content"]
        return _parse_and_normalise(raw, forecaster_id)
    except Exception as e:
        return {"status": "failed", "error": str(e)[:200]}

# ── Odds API ──────────────────────────────────────────────────────────────────
ODDS_SPORT = "soccer_fifa_world_cup"

def fetch_odds(api_key: str) -> dict:
    """Returns {(home_name, away_name): {p_home, p_draw, p_away, raw_odds}}."""
    url = (
        f"https://api.the-odds-api.com/v4/sports/{ODDS_SPORT}/odds/"
        f"?apiKey={api_key}&regions=eu&markets=h2h&oddsFormat=decimal"
    )
    try:
        raw = http_get(url, timeout=20)
        events = json.loads(raw)
    except Exception as e:
        log.warning(f"Odds API failed: {e}")
        return {}

    result = {}
    for ev in events:
        home = ev.get("home_team", "")
        away = ev.get("away_team", "")
        best: dict[str, float] = {}
        for bk in ev.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt["key"] == "h2h":
                    for o in mkt.get("outcomes", []):
                        n, p = o["name"], float(o["price"])
                        if n not in best or p < best[n]:
                            best[n] = p
        # Expect exactly 3 outcomes: home, away, Draw
        draw_price = best.get("Draw") or best.get("draw")
        if not (home in best and away in best and draw_price):
            continue
        try:
            raw_odds = [best[home], draw_price, best[away]]
            implied = [1 / o for o in raw_odds]
            total = sum(implied)
            normed = normalise(implied[0] / total, implied[1] / total, implied[2] / total)
            result[(home, away)] = {
                "p_home": normed[0], "p_draw": normed[1], "p_away": normed[2],
                "raw_odds": raw_odds,
            }
        except (ZeroDivisionError, KeyError):
            continue
    return result

# Bookmakers name some teams differently from our fixtures. Substring
# matching alone misses these entirely (e.g. "USA" vs "United States"),
# which silently drops the market forecast for that match.
ODDS_ALIASES = {
    "USA":                    ["United States", "United States of America"],
    "South Korea":            ["Korea Republic"],
    "IR Iran":                ["Iran"],
    "Côte d'Ivoire":          ["Ivory Coast", "Cote d'Ivoire"],
    "Cabo Verde":             ["Cape Verde"],
    "Curaçao":                ["Curacao"],
    "Czechia":                ["Czech Republic"],
    "Türkiye":                ["Turkey", "Turkiye"],
    "Bosnia and Herzegovina": ["Bosnia & Herzegovina", "Bosnia-Herzegovina"],
    "DR Congo":               ["Congo DR", "Democratic Republic of the Congo"],
}

def _names_match(fixture_name: str, odds_name: str) -> bool:
    odds_l = odds_name.lower()
    for cand in [fixture_name] + ODDS_ALIASES.get(fixture_name, []):
        c = cand.lower()
        if c in odds_l or odds_l in c:
            return True
    return False

def match_odds(match: dict, odds_map: dict) -> dict | None:
    """Fuzzy match a fixture to an odds entry (alias-aware)."""
    home, away = match["home"], match["away"]
    if (home, away) in odds_map:
        return odds_map[(home, away)]
    for (h, a), data in odds_map.items():
        if _names_match(home, h) and _names_match(away, a):
            return data
    return None

# ── Human picks ingestion ─────────────────────────────────────────────────────
def ingest_human_picks(csv_url: str, freeze_utc: datetime,
                       today_match_ids: set) -> dict:
    """
    Fetch Google Sheet CSV, return {slug: {match_id: {p_home, p_draw, p_away, name}}}.
    Only rows submitted before freeze_utc, only today's match IDs.
    Per (slug, match_id), latest submission wins.
    """
    try:
        raw = http_get(csv_url, timeout=20)
        text = raw.decode("utf-8")
    except Exception as e:
        log.warning(f"Google Sheet fetch failed: {e}")
        return {}

    valid = []
    for row in csv.DictReader(text.splitlines()):
        mid = (row.get("match_id") or "").strip()
        if mid not in today_match_ids:
            continue
        try:
            submitted = datetime.fromisoformat(
                row["submitted_at"].replace("Z", "+00:00")
            )
        except (KeyError, ValueError):
            continue
        if submitted >= freeze_utc:
            continue
        valid.append((submitted, row))

    valid.sort(key=lambda x: x[0])  # ascending → later rows overwrite

    picks: dict[str, dict] = {}
    for _, row in valid:
        try:
            name = (row.get("name") or "").strip()
            slug = (row.get("slug") or "").strip() or to_slug(name)
            if not slug:
                continue
            # Moderation rule (pre-registered, CHANGELOG 2026-06-12): slugs
            # named "test" or prefixed "test-" are dev/test submissions and
            # are never ingested.
            if slug == "test" or slug.startswith("test-"):
                log.info(f"  excluded test slug: {slug}")
                continue
            mid = row["match_id"].strip()
            ph, pd, pa = float(row["p_home"]), float(row["p_draw"]), float(row["p_away"])
            # Handle percentage-scale submissions (sum ~100)
            if abs(ph + pd + pa - 100) < 5:
                ph, pd, pa = ph / 100, pd / 100, pa / 100
            normed = normalise(ph, pd, pa)
            picks.setdefault(slug, {})[mid] = {
                "p_home": normed[0], "p_draw": normed[1], "p_away": normed[2],
                "name": name,
            }
        except (KeyError, ValueError):
            continue

    return picks

# ── Crowd forecaster ──────────────────────────────────────────────────────────
def compute_crowd(human_picks: dict, match_id: str) -> dict | None:
    triples = [v[match_id] for v in human_picks.values() if match_id in v]
    if not triples:
        return None
    n = len(triples)
    avg_h = sum(t["p_home"] for t in triples) / n
    avg_d = sum(t["p_draw"] for t in triples) / n
    avg_a = sum(t["p_away"] for t in triples) / n
    normed = normalise(avg_h, avg_d, avg_a)
    return {"p_home": normed[0], "p_draw": normed[1], "p_away": normed[2], "n": n}

# ── Rescue mode helper ────────────────────────────────────────────────────────
def split_remaining(matches: list, now: datetime, grace_min: int = 2):
    """--remaining rescue mode: split a day's matches into (freezable, voided)
    by whether kickoff is still at least grace_min away. Voided matches are
    void for ALL forecasters that day (fair) per the incident playbook."""
    cutoff = now + timedelta(minutes=grace_min)
    freezable, voided = [], []
    for m in matches:
        ko = datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00"))
        (freezable if ko > cutoff else voided).append(m)
    return freezable, voided

# ── Date grouping (UTC-8, matching picks.js) ──────────────────────────────────
_UTC8 = timedelta(hours=8)

def group_by_utc8(fixtures: list) -> dict:
    day_map: dict[str, list] = {}
    for f in fixtures:
        dt = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
        day = (dt - _UTC8).strftime("%Y-%m-%d")
        day_map.setdefault(day, []).append(f)
    return day_map

# ── Post-freeze validation ────────────────────────────────────────────────────
def validate_output(match_results: list) -> None:
    allowed = set(AI_LINEUP) | set(NON_AI_FORECASTERS)
    for mr in match_results:
        for forecaster, fc in mr["forecasts"].items():
            # Lineup gate: any forecaster outside the frozen registry means
            # code and registry have drifted — abort rather than commit.
            if forecaster not in allowed and not forecaster.startswith("human:"):
                log.error(
                    f"Forecaster {forecaster!r} not in frozen lineup {AI_LINEUP}"
                )
                sys.exit(4)
            if fc.get("status") == "failed":
                continue
            for k in ("p_home", "p_draw", "p_away"):
                if k not in fc:
                    log.error(f"Missing {k} in {forecaster}/{mr['match_id']}")
                    sys.exit(4)
            s = fc["p_home"] + fc["p_draw"] + fc["p_away"]
            if abs(s - 1.0) > 1e-4:
                log.error(
                    f"Probs don't sum to 1 for {forecaster}/{mr['match_id']}: {s:.6f}"
                )
                sys.exit(4)

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse

    load_dotenv()

    parser = argparse.ArgumentParser(description="Freeze daily predictions")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (UTC-8 calendar day)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print JSON to stdout, do not write files or commit")
    parser.add_argument("--remaining", action="store_true",
                        help="Rescue mode: freeze only matches that haven't "
                             "kicked off; passed matches are void for everyone")
    parser.add_argument("--force", action="store_true",
                        help="Freeze even if the 3h pre-kickoff window hasn't "
                             "opened yet (scheduled runs never pass this)")
    args = parser.parse_args()

    # ── Pre-flight: required secrets ─────────────────────────────────────────
    # GITHUB_TOKEN drives gpt-4o-mini via the free GitHub Models tier:
    # built-in in Actions (permissions: models: read), a PAT locally.
    required = ["GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
                "ODDS_API_KEY", "SHEET_CSV_URL", "GITHUB_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.error(f"Missing secrets: {missing}")
        sys.exit(1)

    freeze_utc = datetime.now(timezone.utc)
    log.info(f"Freeze time: {freeze_utc.isoformat()}")

    # ── Load fixtures ─────────────────────────────────────────────────────────
    fixtures = json.loads(FIXTURES_PATH.read_text())
    day_map = group_by_utc8(fixtures)

    target_date = args.date or (freeze_utc - _UTC8).strftime("%Y-%m-%d")
    all_today = day_map.get(target_date, [])
    if not all_today:
        log.error(f"No fixtures found for {target_date}")
        sys.exit(1)

    # ── Placeholder gate: never forecast an unknown opponent ─────────────────
    skipped = [m for m in all_today if m.get("is_placeholder")]
    today_matches = [m for m in all_today if not m.get("is_placeholder")]
    for m in skipped:
        log.warning(
            f"SKIP (placeholder): {m['match_id']} {m['home']} vs {m['away']} — "
            f"update fixtures.json with the resolved team to include it"
        )
    if not today_matches:
        log.error(f"All {len(skipped)} fixtures for {target_date} are "
                  f"placeholders — nothing to freeze")
        sys.exit(1)

    # ── Already frozen? Clean no-op (exit 0), checked FIRST so post-kickoff
    # scheduled runs on a successfully frozen day stay green. Immutability
    # holds — this path never writes.
    out_path = PREDICTIONS_DIR / f"{target_date}.json"
    if out_path.exists() and not args.dry_run:
        log.info(f"Already frozen: {out_path} exists — nothing to do")
        sys.exit(0)

    # ── Rescue mode: drop matches that already kicked off ────────────────────
    if args.remaining:
        today_matches, voided = split_remaining(today_matches, freeze_utc)
        for m in voided:
            log.warning(f"VOID (already kicked off): {m['match_id']} "
                        f"{m['home']} vs {m['away']} — unscored for everyone")
        if not today_matches:
            log.error("Rescue mode: every match has already kicked off — "
                      "nothing left to freeze")
            sys.exit(1)

    earliest = min(
        datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00"))
        for m in today_matches
    )

    # ── Too early? No-op: scheduled attempts fire all afternoon; only the
    # ones inside [first kickoff − FREEZE_WINDOW, first kickoff) act.
    # Explicit --date / --force / --remaining means a human decided.
    if not (args.date or args.force or args.remaining):
        window_open = earliest - FREEZE_WINDOW
        if freeze_utc < window_open:
            log.info(
                f"Too early: window opens {window_open.strftime('%H:%M')}Z "
                f"(first kickoff {earliest.strftime('%H:%M')}Z) — nothing to do"
            )
            sys.exit(0)

    log.info(f"Freezing {len(today_matches)} matches for {target_date}")
    for m in today_matches:
        log.info(f"  {m['kickoff_utc'][:16]}Z  {m['home']} vs {m['away']}")

    # ── Pre-flight: must be before earliest kickoff ───────────────────────────
    if freeze_utc >= earliest:
        log.error(
            f"ABORT: freeze_utc {freeze_utc.isoformat()} >= earliest kickoff "
            f"{earliest.isoformat()}. A late prediction is a void prediction. "
            f"(Use --remaining to rescue later matches of the day.)"
        )
        sys.exit(2)

    log.info(f"OK: {freeze_utc.strftime('%H:%M')}Z < {earliest.strftime('%H:%M')}Z ✓")

    # ── Fetch odds ────────────────────────────────────────────────────────────
    log.info("Fetching odds from The Odds API...")
    odds_map = fetch_odds(os.environ["ODDS_API_KEY"])
    log.info(f"  Got odds for {len(odds_map)} events")

    # ── Build context data (enriches LLM prompts) ────────────────────────────
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from context_builder import build_match_contexts_json
        build_match_contexts_json(fixtures)
        log.info("Match contexts JSON written for picks UI")
    except Exception as e:
        log.warning(f"context_builder unavailable: {e} — using basic prompts")

    # ── Ingest human picks ────────────────────────────────────────────────────
    log.info("Fetching human picks from Google Sheet...")
    today_ids = {m["match_id"] for m in today_matches}
    human_picks = ingest_human_picks(os.environ["SHEET_CSV_URL"], freeze_utc, today_ids)
    log.info(f"  Got picks from {len(human_picks)} humans")

    # ── Query LLMs and build output ───────────────────────────────────────────
    # AI lineup — dispatch table keyed by the frozen registry, built once and
    # reused by the per-match loop AND the retry sweep below. gpt-oss-120b is a
    # reasoning model: its thinking can spill into the completion, so it gets a
    # larger token budget than the instruct models.
    ai_queries = {
        "gemini-flash": lambda m: query_gemini(m, os.environ["GEMINI_API_KEY"]),
        "llama-70b":    lambda m: query_groq(m, os.environ["GROQ_API_KEY"]),
        "gemma":        lambda m: query_openrouter(
            m, os.environ["OPENROUTER_API_KEY"],
            model="google/gemma-4-31b-it:free", forecaster_id="gemma"),
        "gpt-oss-120b": lambda m: query_groq(
            m, os.environ["GROQ_API_KEY"],
            model="openai/gpt-oss-120b", forecaster_id="gpt-oss-120b",
            max_tokens=2048),
        "gpt-4o-mini":  lambda m: query_github_models(
            m, os.environ["GITHUB_TOKEN"]),
    }
    assert set(ai_queries) == set(AI_LINEUP), "dispatch/lineup drift"

    match_results = []

    for match in today_matches:
        label = f"{match['home']} vs {match['away']}"
        log.info(f"\n{'─'*50}")
        log.info(f"Match: {label}  ({match['match_id']})")
        forecasts: dict = {}

        # Market odds
        odds = match_odds(match, odds_map)
        if odds:
            forecasts["market"] = odds
            log.info(
                f"  market: {odds['p_home']:.3f} / {odds['p_draw']:.3f} / {odds['p_away']:.3f}"
            )
        else:
            log.warning(f"  market: no odds found")

        for forecaster_id in AI_LINEUP:
            log.info(f"  Querying {forecaster_id}...")
            forecasts[forecaster_id] = ai_queries[forecaster_id](match)
            log.info(f"  {forecaster_id}: {forecasts[forecaster_id].get('status', '?')}")
            time.sleep(1)

        # Human picks for this match
        for slug, slug_picks in human_picks.items():
            if match["match_id"] in slug_picks:
                p = slug_picks[match["match_id"]]
                forecasts[f"human:{slug}"] = {
                    "p_home": p["p_home"], "p_draw": p["p_draw"], "p_away": p["p_away"],
                    "name": p.get("name", slug),
                }
        n_humans = sum(1 for k in forecasts if k.startswith("human:"))
        log.info(f"  humans: {n_humans}")

        # Crowd (mean of humans)
        crowd = compute_crowd(human_picks, match["match_id"])
        if crowd:
            forecasts["crowd"] = crowd
            log.info(f"  crowd: {crowd['n']} humans averaged")

        match_results.append({"match_id": match["match_id"], "forecasts": forecasts})

    log.info(f"\n{'═'*50}")

    # ── Retry sweep: re-query any model that still failed ─────────────────────
    # Transient free-tier blips (503/429) often clear within a minute. We have
    # the whole 3h window, so wait once and retry only the failed (match, model)
    # slots before committing — recovering them WITHIN the same freeze (one
    # cutoff for everyone), rather than backfilling later. Same prompt, temp 0.
    by_id = {m["match_id"]: m for m in today_matches}
    stragglers = [
        (mr["match_id"], fid)
        for mr in match_results
        for fid in AI_LINEUP
        if mr["forecasts"].get(fid, {}).get("status") == "failed"
    ]
    if stragglers:
        log.info(f"Retry sweep: {len(stragglers)} failed forecast(s) — "
                 f"waiting 40s for transient errors to clear...")
        time.sleep(40)
        recovered = 0
        for match_id, fid in stragglers:
            log.info(f"  Retrying {fid} for {match_id}...")
            res = ai_queries[fid](by_id[match_id])
            fc_map = next(mr["forecasts"] for mr in match_results
                          if mr["match_id"] == match_id)
            fc_map[fid] = res
            if res.get("status") == "ok":
                recovered += 1
            time.sleep(1)
        log.info(f"Retry sweep: recovered {recovered}/{len(stragglers)}")

    # ── Validate all triples ──────────────────────────────────────────────────
    validate_output(match_results)
    log.info("Validation passed ✓")

    # ── Build final output ────────────────────────────────────────────────────
    output = {
        "date": target_date,
        "freeze_utc": freeze_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "matches": match_results,
    }

    if args.dry_run:
        print(json.dumps(output, indent=2))
        log.info("Dry run — not written")
        return

    # ── Write to disk ─────────────────────────────────────────────────────────
    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    log.info(f"Written: {out_path}")

    # ── Summarise ─────────────────────────────────────────────────────────────
    ai_ok = sum(
        1 for mr in match_results for m in AI_LINEUP
        if mr["forecasts"].get(m, {}).get("status") == "ok"
    )
    log.info(
        f"Summary: {len(today_matches)} matches | "
        f"{ai_ok}/{len(today_matches)*len(AI_LINEUP)} AI forecasts OK | "
        f"{len(human_picks)} humans"
    )
    log.info("DONE — commit data/predictions/ and push to git")


if __name__ == "__main__":
    main()
