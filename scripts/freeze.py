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
import csv, json, logging, os, re, sys, time, unicodedata, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
FIXTURES_PATH = ROOT / "data" / "fixtures" / "fixtures.json"
PREDICTIONS_DIR = ROOT / "data" / "predictions"

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
    Handles markdown fences and prose before the JSON object.
    Returns None on any parse failure.
    """
    text = re.sub(r"```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    m = re.search(r"\{[^}]+\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    try:
        ph = float(obj["p_home"])
        pd = float(obj["p_draw"])
        pa = float(obj["p_away"])
    except (KeyError, ValueError, TypeError):
        return None
    reasoning = str(obj.get("reasoning", ""))[:200]
    return ph, pd, pa, reasoning

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

def make_prompt(match: dict) -> str:
    try:
        from context_builder import get_context_block
        ctx = get_context_block(match)
        if ctx:
            return _PROMPT_ENRICHED.format(context_block=ctx)
    except Exception:
        pass
    return _PROMPT_FALLBACK.format(
        home=match["home"], away=match["away"],
        stage=match["stage"], venue=match["venue"],
        date=match["kickoff_utc"][:10],
    )

# ── LLM providers ────────────────────────────────────────────────────────────
def _parse_and_normalise(raw: str, forecaster: str) -> dict:
    parsed = parse_llm_response(raw)
    if not parsed:
        log.warning(f"  {forecaster}: could not parse response")
        return {"status": "failed", "raw": raw[:500]}
    ph, pd, pa, reasoning = parsed
    normed = normalise(ph, pd, pa)
    return {
        "p_home": normed[0], "p_draw": normed[1], "p_away": normed[2],
        "reasoning": reasoning, "raw": raw[:1000], "status": "ok",
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
    for attempt in range(3):
        try:
            resp = http_post(url, {}, body)
            raw = resp["candidates"][0]["content"]["parts"][0]["text"]
            return _parse_and_normalise(raw, "gemini-flash")
        except Exception as e:
            log.warning(f"  gemini-flash attempt {attempt + 1}: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return {"status": "failed"}

def query_groq(match: dict, api_key: str, model: str = "llama-3.3-70b-versatile") -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": make_prompt(match)}],
        "temperature": 0.0,
        "max_tokens": 300,
    }
    for attempt in range(3):
        try:
            resp = http_post(
                "https://api.groq.com/openai/v1/chat/completions",
                {"Authorization": f"Bearer {api_key}"},
                body,
            )
            raw = resp["choices"][0]["message"]["content"]
            return _parse_and_normalise(raw, "llama-70b")
        except Exception as e:
            log.warning(f"  llama-70b attempt {attempt + 1}: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return {"status": "failed"}

def query_claude(match: dict, api_key: str,
                 model: str = "claude-haiku-4-5-20251001") -> dict:
    body = {
        "model": model,
        "max_tokens": 512,
        "temperature": 0,
        "messages": [{"role": "user", "content": make_prompt(match)}],
    }
    for attempt in range(3):
        try:
            resp = http_post(
                "https://api.anthropic.com/v1/messages",
                {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                body,
            )
            raw = resp["content"][0]["text"]
            return _parse_and_normalise(raw, "claude")
        except Exception as e:
            log.warning(f"  claude attempt {attempt + 1}: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return {"status": "failed"}

def query_openai(match: dict, api_key: str, model: str = "gpt-4o-mini") -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": make_prompt(match)}],
        "temperature": 0.0,
        "max_tokens": 300,
    }
    for attempt in range(3):
        try:
            resp = http_post(
                "https://api.openai.com/v1/chat/completions",
                {"Authorization": f"Bearer {api_key}"},
                body,
            )
            raw = resp["choices"][0]["message"]["content"]
            return _parse_and_normalise(raw, "gpt-4o-mini")
        except Exception as e:
            log.warning(f"  gpt-4o-mini attempt {attempt + 1}: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return {"status": "failed"}

def query_openrouter(match: dict, api_key: str,
                     model: str = "google/gemma-4-31b-it:free") -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": make_prompt(match)}],
        "temperature": 0.0,
        "max_tokens": 300,
    }
    for attempt in range(3):
        try:
            resp = http_post(
                "https://openrouter.ai/api/v1/chat/completions",
                {
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://github.com/dakshjagwani/forecasting-arena-wc26",
                    "X-Title": "Forecasting Arena WC 2026",
                },
                body,
            )
            raw = resp["choices"][0]["message"]["content"]
            return _parse_and_normalise(raw, "gemma")
        except Exception as e:
            log.warning(f"  gemma attempt {attempt + 1}: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return {"status": "failed"}

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

def match_odds(match: dict, odds_map: dict) -> dict | None:
    """Fuzzy match a fixture to an odds entry."""
    home, away = match["home"], match["away"]
    if (home, away) in odds_map:
        return odds_map[(home, away)]
    home_l, away_l = home.lower(), away.lower()
    for (h, a), data in odds_map.items():
        if (home_l in h.lower() or h.lower() in home_l) and \
           (away_l in a.lower() or a.lower() in away_l):
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
    for mr in match_results:
        for forecaster, fc in mr["forecasts"].items():
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
    args = parser.parse_args()

    # ── Pre-flight: required secrets ─────────────────────────────────────────
    required = ["GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
                "ODDS_API_KEY", "SHEET_CSV_URL"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.error(f"Missing secrets: {missing}")
        sys.exit(1)

    # Optional models — warn but don't abort if keys missing
    optional_models = {"ANTHROPIC_API_KEY": "claude", "OPENAI_API_KEY": "gpt-4o-mini"}
    for k, name in optional_models.items():
        if not os.environ.get(k):
            log.warning(f"  {k} not set — {name} will be skipped")

    freeze_utc = datetime.now(timezone.utc)
    log.info(f"Freeze time: {freeze_utc.isoformat()}")

    # ── Load fixtures ─────────────────────────────────────────────────────────
    fixtures = json.loads(FIXTURES_PATH.read_text())
    day_map = group_by_utc8(fixtures)

    target_date = args.date or (freeze_utc - _UTC8).strftime("%Y-%m-%d")
    today_matches = day_map.get(target_date, [])
    if not today_matches:
        log.error(f"No fixtures found for {target_date}")
        sys.exit(1)

    log.info(f"Freezing {len(today_matches)} matches for {target_date}")
    for m in today_matches:
        log.info(f"  {m['kickoff_utc'][:16]}Z  {m['home']} vs {m['away']}")

    # ── Pre-flight: must be before earliest kickoff ───────────────────────────
    earliest = min(
        datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00"))
        for m in today_matches
    )
    if freeze_utc >= earliest:
        log.error(
            f"ABORT: freeze_utc {freeze_utc.isoformat()} >= earliest kickoff "
            f"{earliest.isoformat()}. A late prediction is a void prediction."
        )
        sys.exit(2)

    # ── Pre-flight: no double-freeze ──────────────────────────────────────────
    out_path = PREDICTIONS_DIR / f"{target_date}.json"
    if out_path.exists() and not args.dry_run:
        log.error(f"ABORT: predictions already exist at {out_path}")
        sys.exit(3)

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

        # Gemini Flash
        log.info("  Querying gemini-flash...")
        forecasts["gemini-flash"] = query_gemini(match, os.environ["GEMINI_API_KEY"])
        log.info(f"  gemini-flash: {forecasts['gemini-flash'].get('status', '?')}")
        time.sleep(1)

        # Groq Llama-70B
        log.info("  Querying llama-70b (Groq)...")
        forecasts["llama-70b"] = query_groq(match, os.environ["GROQ_API_KEY"])
        log.info(f"  llama-70b: {forecasts['llama-70b'].get('status', '?')}")
        time.sleep(1)

        # OpenRouter Gemma
        log.info("  Querying gemma (OpenRouter)...")
        forecasts["gemma"] = query_openrouter(match, os.environ["OPENROUTER_API_KEY"])
        log.info(f"  gemma: {forecasts['gemma'].get('status', '?')}")
        time.sleep(1)

        # Claude (optional)
        if os.environ.get("ANTHROPIC_API_KEY"):
            log.info("  Querying claude...")
            forecasts["claude"] = query_claude(match, os.environ["ANTHROPIC_API_KEY"])
            log.info(f"  claude: {forecasts['claude'].get('status', '?')}")
            time.sleep(1)

        # OpenAI GPT-4o-mini (optional)
        if os.environ.get("OPENAI_API_KEY"):
            log.info("  Querying gpt-4o-mini...")
            forecasts["gpt-4o-mini"] = query_openai(match, os.environ["OPENAI_API_KEY"])
            log.info(f"  gpt-4o-mini: {forecasts['gpt-4o-mini'].get('status', '?')}")
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
    ai_models = ["gemini-flash", "llama-70b", "gemma", "claude", "gpt-4o-mini"]
    active_ai = [m for m in ai_models if any(
        mr["forecasts"].get(m) for mr in match_results
    )]
    ai_ok = sum(
        1 for mr in match_results for m in active_ai
        if mr["forecasts"].get(m, {}).get("status") == "ok"
    )
    log.info(
        f"Summary: {len(today_matches)} matches | "
        f"{ai_ok}/{len(today_matches)*len(active_ai)} AI forecasts OK | "
        f"{len(human_picks)} humans"
    )
    log.info("DONE — commit data/predictions/ and push to git")


if __name__ == "__main__":
    main()
