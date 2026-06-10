# The Forecasting Arena — World Cup 2026

Humans vs AI models vs the betting market: who is actually calibrated?
A live, public, £0 experiment across all 104 matches of the FIFA World Cup 2026
(11 Jun – 19 Jul 2026).

This file is the project bible. Claude Code: read this fully before any task.
When in doubt, optimise for (1) experiment integrity, (2) shipping speed,
(3) zero cost — in that order. Polish is last.

---

## 1. Non-negotiable principles

1. **Frozen before kickoff.** Every forecast (model, market, human) must be
   committed to git BEFORE the day's first kickoff. Git history is the public
   audit trail. A late prediction is a void prediction — never backfill.
2. **Identical format for every forecaster.** One unit of play per match:
   a probability triple `p_home + p_draw + p_away = 1.0` for the
   **90-minute result** (draws are valid in knockouts too — matches market
   convention).
3. **£0 stack.** Free API tiers, GitHub Actions, GitHub Pages, Google Forms.
   If a step requires payment, find the free substitute or cut the feature.
4. **Probabilities, not picks.** No exact-score predictions feed the
   leaderboard. (Optional exact-score side-bet is display-only.)
5. **Honest scoring.** Multiclass Brier score, pre-registered rules, published
   methodology. Negative results are publishable results.

---

## 2. Architecture (all static, no backend)

```
Google Form ──> Google Sheet ──(published CSV)──┐
                                                 v
Free LLM APIs (Gemini, Groq, OpenRouter) ──> freeze.py ──> data/predictions/mdNN.json  (committed pre-kickoff)
Local Ollama model (runs on Daksh's Mac) ──> freeze_local.py ──> merged by freeze.py
The Odds API (free tier) ───────────────────────┘

After full time:
results fetch/entry ──> score.py ──> data/scores/leaderboard.json + calibration.json

GitHub Pages (static /site) reads the JSONs ──> leaderboard, match grid, personal cards
GitHub Actions cron drives freeze + scoring. Repo is public = open source + audit log.
```

## 3. Tech stack

- **Python 3.11+**, stdlib + `requests`, `pandas` (only if needed). No frameworks.
- **GitHub Actions**: two workflows — `freeze.yml` (daily, pre-kickoff) and
  `score.yml` (post-matches, late evening UTC + manual dispatch).
- **GitHub Pages** serving `/site` (plain HTML/CSS/JS + Chart.js from CDN).
  No build step. No React. Pages read JSON via fetch.
- **Human picks**: Google Form -> linked Sheet -> File > Share > Publish to
  web > CSV. `freeze.py` fetches the CSV URL at freeze time.
- **Secrets** (GitHub Actions secrets): `GEMINI_API_KEY`, `GROQ_API_KEY`,
  `OPENROUTER_API_KEY`, `ODDS_API_KEY`. Never commit keys.

## 4. The forecasters

| ID | Type | Source | Notes |
|----|------|--------|-------|
| `market` | benchmark | The Odds API free tier (500 req/mo) | h2h closing odds, de-vig (normalise implied probs) |
| `gemini-flash` | AI | Gemini API free tier | temp 0, fixed prompt |
| `llama-70b` | AI | Groq free tier | temp 0, fixed prompt |
| `deepseek` | AI | OpenRouter free models | temp 0, fixed prompt |
| `mistral` | AI | Mistral free API tier (optional 4th) | temp 0 |
| `qwen-laptop` | AI (local) | Ollama on M5 MacBook Air | the "runs on my laptop" storyline; may miss days — that's allowed and logged |
| `human:<slug>` | human | Google Form | slug = lowercased name, deduped to latest submission pre-freeze |
| `crowd` | derived | mean of all human triples per match, renormalised | computed at freeze, immutable after |

Model lineup is frozen after matchday 1. Adding/removing models mid-tournament
invalidates the experiment — do not do it.

## 5. Fixed LLM prompt (do not vary across models or days)

System/user prompt template (fill `{home}`, `{away}`, `{stage}`, `{venue}`, `{date}`):

```
You are forecasting a FIFA World Cup 2026 match.
Match: {home} vs {away}, {stage}, {venue}, {date}.
Give your honest probability estimate for the 90-minute result.
Respond with ONLY this JSON, no other text:
{"p_home": <float>, "p_draw": <float>, "p_away": <float>, "reasoning": "<max 50 words>"}
The three probabilities must sum to 1.0.
```

Rules:
- `temperature=0` (or provider minimum). One attempt + up to 2 retries on
  invalid JSON. Strip markdown fences before parsing.
- Validation: clamp each p to [0.01, 0.98], renormalise to sum 1.0. If still
  invalid after retries, record `"status": "failed"` for that model/match —
  never substitute a default.
- Store the raw response verbatim alongside the parsed triple (needed for the
  bias-lab and post-mortem analysis later).

## 6. Data schemas (JSON, committed to repo)

`data/fixtures/fixtures.json` — all 104 matches:
```json
{"match_id": "md01-MEX-RSA", "matchday_label": "2026-06-11",
 "stage": "Group A", "home": "Mexico", "away": "South Africa",
 "kickoff_utc": "2026-06-12T02:00:00Z", "venue": "Estadio Azteca, Mexico City"}
```

`data/predictions/2026-06-11.json` — one file per match-date:
```json
{"date": "2026-06-11", "freeze_utc": "2026-06-11T18:00:00Z",
 "matches": [{
   "match_id": "md01-MEX-RSA",
   "forecasts": {
     "market":      {"p_home": 0.62, "p_draw": 0.22, "p_away": 0.16, "raw_odds": [1.55, 4.2, 7.0]},
     "gemini-flash":{"p_home": 0.58, "p_draw": 0.24, "p_away": 0.18, "reasoning": "...", "raw": "..."},
     "human:sarah-k":{"p_home": 0.70, "p_draw": 0.20, "p_away": 0.10},
     "crowd":       {"p_home": 0.61, "p_draw": 0.23, "p_away": 0.16, "n": 38}
   }}]}
```

`data/results/results.json` — appended after FT:
```json
{"match_id": "md01-MEX-RSA", "score_home": 2, "score_away": 1,
 "outcome": "home", "status": "FT"}
```

`data/scores/leaderboard.json` — regenerated by score.py:
```json
{"updated_utc": "...", "matches_scored": 3,
 "rows": [{"forecaster": "market", "type": "market", "n_predicted": 3,
           "mean_brier": 0.171, "vs_market": 0.0, "qualified": true}]}
```

`data/scores/calibration.json` — per forecaster: buckets of predicted prob
(0.0–0.1, ... 0.9–1.0) vs actual frequency, plus n per bucket. Drives the
personal cards.

## 7. Scoring spec (pre-registered — do not change after matchday 1)

- **Multiclass Brier** per match: `sum over o in {home,draw,away} of (p_o - y_o)^2`
  where y is the one-hot actual outcome. Range 0 (perfect) to 2 (certain and wrong).
- Leaderboard metric: **mean Brier over matches predicted** (not over all matches).
- **Qualification for final rankings**: predicted >= 60% of matches available
  since the forecaster's first submission. Unqualified rows shown greyed out.
- Reference rows always shown: `market`, `crowd`, and a `uniform` baseline
  (1/3,1/3,1/3 -> Brier 0.667) for context.
- Knockout matches: scored on 90-minute result (draw possible). State this on
  the site to avoid confusion.
- Abandoned/postponed matches: excluded from scoring entirely.

## 8. Repo layout

```
/CLAUDE.md                  <- this file
/README.md                  <- public-facing: what, why, methodology, how to reproduce
/scripts/
  freeze.py                 <- fetch odds + query LLMs + ingest form CSV + write predictions JSON
  freeze_local.py           <- runs on the Mac: query Ollama, commit local-model triples
  score.py                  <- fetch/read results, compute Brier + calibration, write scores JSONs
  fetch_results.py          <- football-data.org free tier; fallback: manual entry into results.json
  make_fixtures.py          <- one-off: build fixtures.json for all 104 matches
  bias_lab.py               <- Phase 3: anonymised-team counterfactual experiment
/data/
  fixtures/ predictions/ results/ scores/
/site/
  index.html                <- leaderboard + today's frozen grid
  card.html?name=sarah-k    <- personal calibration card (shareable, html2canvas export to PNG)
  methodology.html          <- scoring rules, prompt, freeze policy (publish day 1 = pre-registration)
  app.js  style.css
/.github/workflows/
  freeze.yml  score.yml
```

## 9. Build phases & acceptance criteria

### Phase 0 — TONIGHT (before first kickoff, 11 Jun)
Goal: matchday 1 predictions frozen and committed. Nothing else matters.
- [ ] Repo created (public), this file at root, README stub.
- [ ] `make_fixtures.py` -> fixtures.json (hand-verify matchday 1 rows).
- [ ] Google Form live: Name field + per match two number fields
      ("{home} win %", "Draw %"); away = 100 − sum, validated in freeze.py.
      Sheet published as CSV; URL in repo config.
- [ ] `freeze.py` working end-to-end for >= 2 LLM providers + odds + form CSV.
      Run it MANUALLY tonight if the cron isn't ready — manual run is fine,
      the commit timestamp is the integrity proof.
- [ ] WhatsApp message sent with form link.
- [ ] Full dress rehearsal: run the entire pipeline once on a FAKE fixture
      (freeze -> fake result -> score) before the real freeze. See section 13.
Acceptance: `data/predictions/2026-06-11.json` committed before first kickoff.

### Phase 1 — Days 1–3
- [ ] `freeze.yml` cron (daily, 1h before earliest kickoff that day; kickoffs
      are US/MX/CA local -> mostly 16:00–03:00 UTC; single daily freeze at a
      fixed UTC time before the first match is acceptable and simpler).
- [ ] `score.py` + `score.yml` (run 06:00 UTC daily, scores yesterday's FT matches).
- [ ] pytest suite for scoring + validation (section 13) wired into CI on every push.
- [ ] `site/index.html` v1: leaderboard table (rank, badge HUM/AI/MKT, name,
      n, mean Brier, vs market) + today's frozen grid. Static fetch of JSONs.
- [ ] Daily WhatsApp matchday card = screenshot of a `/site/daily.html` panel
      (auto-generated text: best/worst forecaster, one-line summary).
Acceptance: a colleague can submit picks, see them frozen, and find their rank
next morning without Daksh touching anything.

### Phase 2 — Week 1
- [ ] `card.html`: personal calibration curve + rank + plain-English one-liner
      ("overconfident on favourites by Xpp"), PNG export via html2canvas.
- [ ] `crowd` forecaster computed at freeze.
- [ ] `freeze_local.py` on the Mac via launchd (daily, before CI freeze) for
      the Ollama model. Missed days logged as not-predicted.
- [ ] methodology.html complete (this is the pre-registration artifact).

### Phase 3 — Mid-tournament (~24 Jun, matchday 3 window)
- [ ] `bias_lab.py`: re-ask each model the same fixtures with teams anonymised
      ("Team A: Elo 2054, FIFA rank 5..." vs real names). Output: per-model
      name-premium chart. Publish as `/site/biaslab.html` + LinkedIn post.

### Phase 4 — Post-final (19 Jul+)
- [ ] Freeze the arena. Final write-up page: headline findings, full dataset
      download links, reproduction instructions.
- [ ] Final personal cards for all qualified humans.

## 10. Ops runbook (daily, ~10 min)

1. Morning: check score.yml ran; spot-check yesterday's results vs BBC.
2. If results API failed: hand-edit `data/results/results.json`, re-run score.py.
3. Post matchday card to WhatsApp groups.
4. Check freeze.yml scheduled time vs today's earliest kickoff (config file
   holds per-date freeze times; verify once at setup, not daily).
5. Sunday: generate weekly chart, write 3-sentence LinkedIn post.

## 11. Risks & fallbacks

- **Free tier outage / rate limit** -> retry with backoff; if a model misses a
  day, log not-predicted (the 60% rule absorbs it). Never backfill.
- **Odds API quota** -> 104 matches x 1 snapshot fits in 500/mo; if exhausted,
  manually log closing odds from a bookmaker site (5 min).
- **Results API flaky** -> manual entry path must stay first-class.
- **Form vandalism / joke names** -> moderate the sheet; slugs are stable IDs.
- **Timezone bugs** -> everything stored UTC; the only local-time rendering is
  in the UI. Test the 02:00 UTC kickoff case (Mexico evening games).
- **Scope creep** -> any feature not in phases 0–4 goes to a BACKLOG.md, not
  the build.

## 12. Style notes for the site

Plain, fast, mobile-first (audience opens links from WhatsApp on phones).
One accent colour per forecaster type: humans purple, AI teal, market grey.
No login, no cookies, no analytics beyond a simple page-view counter if free.
The two surfaces that must be excellent: the pick form and the personal card.
Everything else can be ugly-but-clear.

---

## 13. Testing strategy (risk-weighted — do not gold-plate)

Testing effort is allocated by what is UNRECOVERABLE, not by coverage %.
Unrecoverable: a freeze after kickoff; wrong scoring math discovered weeks in.
Recoverable: everything on the site. Claude Code: do NOT write tests for HTML,
CSS, or page rendering. Budget: the whole suite is a few hours, written in
Phase 1, frozen alongside the scoring rules.

### 13.1 Unit tests (pytest, `/tests/`)
- **Brier scoring** against hand-computed cases:
  perfect prediction -> 0.0; uniform (1/3,1/3,1/3) -> 0.667 (any outcome);
  certain-and-wrong (1.0 on home, away wins) -> 2.0; one mixed case computed
  by hand in a comment.
- **Validation/normalisation**: triples clamped to [0.01, 0.98] and
  renormalised to sum 1.0 (tolerance 1e-9); negative or >1 inputs rejected.
- **LLM response parsing** with adversarial fixtures: markdown-fenced JSON,
  prose before the JSON, probabilities summing to 0.97, missing key, empty
  string. Each either parses correctly or returns status=failed — never a
  silent default.
- **Odds de-vig**: known odds triple -> known normalised probabilities.
- **Form ingestion**: duplicate submissions -> latest pre-freeze wins;
  post-freeze submissions ignored; slug generation stable ("Sarah K." ==
  "sarah k" -> `sarah-k`); away% = 100 − home − draw validated.
- **Qualification rule**: 60% threshold edge cases (exactly 60%, forecaster
  joining mid-tournament measured from first submission).
- **Outcome derivation**: score -> home/draw/away, including knockout draws.

### 13.2 Golden-file integration test
One synthetic matchday end-to-end committed to `/tests/golden/`:
fake fixtures + fake forecasts + fake results -> run score.py -> output must
byte-match the expected leaderboard.json and calibration.json. This is the
regression net for any later refactor. Runs in CI on every push.

### 13.3 Pre-flight gates (run INSIDE freeze.py, abort + alert on failure)
- All required secrets present before any API call.
- `freeze_utc < earliest kickoff_utc today` — hard assert. If violated, abort
  the freeze and notify; a late freeze must never be committed.
- Every committed triple passes schema + sum check.
- Predictions file for today does not already exist (no double-freeze).

### 13.4 Post-score gates (inside score.py)
- Every scored match has status FT; no NaN/None in any output JSON;
  referential integrity (every prediction match_id exists in fixtures.json,
  every result has a predictions entry); leaderboard row count == forecaster
  count.

### 13.5 Dress rehearsal (Phase 0, tonight, mandatory)
Run the full cycle once on a fake fixture dated in the past: freeze (with at
least one real LLM call) -> hand-write a fake result -> score -> open the
JSONs. This is the only "test" that must exist before matchday 1; the pytest
suite follows in Phase 1.

## 14. Maintenance, monitoring & incident playbook

### 14.1 Monitoring (all free)
- GitHub Actions failure emails are ON by default — do not silence them.
- Optional but recommended: a `curl` to ntfy.sh (free push notifications) as
  the final step of freeze.yml — one topic for SUCCESS, one for FAILURE — so a
  silent cron death is noticed before kickoff, not after.
- `validate_data.py` runs as the last step of score.yml across the whole
  /data tree (section 13.4 checks repo-wide). A red run = data problem.

### 14.2 Scheduled maintenance
- Daily (~10 min): the ops runbook in section 10.
- Sunday (~30 min): weekly data audit — spot-check 3 random scored matches
  against BBC results; review form sheet for junk names; confirm next week's
  freeze times against the official schedule (kickoff times CAN change).
- Dependencies pinned in requirements.txt; NO upgrades mid-tournament.
- Model lineup, prompt, and scoring rules are frozen (sections 4, 5, 7) —
  "maintenance" never includes changing the experiment.

### 14.3 Incident playbook
- **Freeze cron failed pre-kickoff**: run freeze.py manually from the laptop.
  If kickoff has passed for some matches, freeze only the remaining matches;
  the missed ones are void for ALL forecasters that day (fair) and logged.
- **Wrong result entered**: correct results.json, re-run score.py (idempotent
  by design — scoring always recomputes from raw data), add a line to
  CHANGELOG.md. Every data correction is publicly logged there. Never edit
  scores JSONs by hand.
- **LLM provider outage / model deprecated mid-tournament**: the model simply
  stops predicting (retires). It is never swapped for a replacement. Logged in
  CHANGELOG.md and shown on the site.
- **Form vandalism**: delete rows in the sheet pre-freeze; post-freeze, mark
  the slug excluded in a config list (exclusions logged in CHANGELOG.md).
- **Repo/Pages outage**: data is git — nothing is lost; site recovers on push.

### 14.4 End-of-life (post 19 Jul)
- Tag a final release; archive the repo (read-only) after the write-up ships.
- Final dataset exported as one zip (all predictions, results, scores, raw
  LLM responses) linked from the write-up — this is the lasting artifact.

