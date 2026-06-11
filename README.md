# The Forecasting Arena — World Cup 2026

**Humans vs AI models vs the betting market: who is actually calibrated?**

A live, public, zero-cost forecasting experiment across the FIFA World Cup 2026
(11 Jun – 19 Jul 2026, 104 matches). Every forecaster — five AI models, the
betting market, and a crowd of humans — submits the same thing for every match:
a probability triple for the 90-minute result (home / draw / away). Predictions
are committed to this repository **before kickoff**; the git history is the
tamper-proof audit trail. After full time, everyone is scored with the
multiclass Brier score. At the end, the data says who understood football and
who got lucky.

> **Status**: launching during the group stage — the experiment begins at its
> first frozen matchday (see [CHANGELOG.md](CHANGELOG.md)). Matches before the
> first freeze are not scored for anyone, which keeps the comparison fair.

## The forecasters

| ID | Type | Source |
|----|------|--------|
| `market` | benchmark | The Odds API closing h2h odds, de-vigged (normalised implied probabilities) |
| `gemini-flash` | AI | Gemini 2.5 Flash (Google AI free tier) |
| `llama-70b` | AI | Llama 3.3 70B (Groq free tier) |
| `gemma` | AI | Gemma (OpenRouter free tier) |
| `gpt-oss-120b` | AI | GPT-OSS-120B, OpenAI's open-weights reasoning model (Groq free tier) |
| `gpt-4o-mini` | AI | GPT-4o mini (GitHub Models free tier) |
| `human:<name>` | human | The [picks app](site/picks.html) — swipe cards, three-way probability slider |
| `crowd` | derived | Mean of all human triples per match, renormalised at freeze |
| `uniform` | baseline | (⅓, ⅓, ⅓) — Brier 0.667; if you can't beat this, you know nothing |

The lineup is **frozen** (scripts/freeze.py `AI_LINEUP`). A model that loses its
free tier retires — it is never swapped. Every model gets an identical prompt at
temperature 0 with identical statistical context (Elo, last-10 form, head-to-head,
venue). No browsing, no live news.

## Methodology (pre-registered)

Full details: [site/methodology.html](site/methodology.html). The short version:

- **Score**: multiclass Brier, `Σ (p_o − y_o)²` over {home, draw, away}.
  0 = perfect, 0.667 = uniform guess, 2 = certain and wrong.
- **Ranking**: mean Brier over matches *predicted* (not all matches).
- **Qualification**: predicted ≥ 60% of matches scored since your first
  submission. Unqualified rows are shown greyed out.
- **Knockouts** are scored on the 90-minute result — a draw is a valid outcome.
- **Late = void**: predictions are frozen and committed before the day's first
  kickoff. There is no backfilling, for anyone, ever.
- **Corrections**: raw data fixes are logged in [CHANGELOG.md](CHANGELOG.md);
  scores are always recomputed from raw data, never hand-edited.

## How it works

```
picks app ──POST──▶ Apps Script ──▶ Google Sheet ──CSV──▶ ┐
free LLM APIs ─────────────────────────────────────────▶ freeze.py ──▶ data/predictions/YYYY-MM-DD.json
The Odds API ──────────────────────────────────────────▶ ┘            (committed pre-kickoff, daily cron)

full time ──▶ fetch_results.py ──▶ results.json ──▶ score.py ──▶ leaderboard.json + calibration.json
GitHub Pages serves /site, which reads the JSONs. Total cost: £0.
```

## Reproduce it

```bash
git clone https://github.com/dakshjagwani/forecasting-arena-wc26
pip install -r requirements.txt
pytest tests/                       # scoring math, parsing, golden pipeline
python scripts/validate_data.py    # audit every committed data file
python scripts/score.py            # recompute the leaderboard from raw data
```

Every prediction ever made is in `data/predictions/` with its freeze timestamp,
and every commit is timestamped by GitHub. To verify nobody cheated: check that
each prediction file's commit predates the kickoffs inside it.

## Repository guide

- `scripts/` — freeze.py (daily prediction freeze), score.py (Brier + calibration),
  fetch_results.py, validate_data.py (data audit), context_builder.py (Elo/form/H2H)
- `data/` — fixtures, frozen predictions, results, scores. Append-only audit log.
- `site/` — the picks app and leaderboard (plain HTML/CSS/JS, no build step)
- `tests/` — pytest suite + golden-file integration test ([TESTING.md](TESTING.md))
- [CLAUDE.md](CLAUDE.md) — full project spec; [TESTING.md](TESTING.md) — validation standard
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — full system diagrams: every API, script, and data flow
- [ROADMAP.md](ROADMAP.md) — what's left to build, in priority order
