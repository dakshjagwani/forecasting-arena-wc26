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
3. **£0 stack.** Free API tiers, GitHub Actions, GitHub Pages, Google Sheets + Apps Script.
   If a step requires payment, find the free substitute or cut the feature.
4. **Probabilities, not picks.** No exact-score predictions feed the
   leaderboard. (Optional exact-score side-bet is display-only.)
5. **Honest scoring.** Multiclass Brier score, pre-registered rules, published
   methodology. Negative results are publishable results.

---

## 2. Architecture (all static, no backend)

```
Custom web app (site/picks.html) ──POST──> Google Apps Script ──> Google Sheet ──(published CSV)──┐
                                                                                                    v
Free LLM APIs (Gemini, Groq, OpenRouter) ──────────────────────────────────────────────────> freeze.py ──> data/predictions/YYYY-MM-DD.json  (committed pre-kickoff)
Local Ollama model (runs on Daksh's Mac) ──> freeze_local.py ──> merged by freeze.py ────────────┘
The Odds API (free tier) ──────────────────────────────────────────────────────────────────────────┘

After full time:
fetch_results.py (football-data.org) ──> results.json ──> score.py ──> data/scores/leaderboard.json + calibration.json

GitHub Pages (static /site) reads the JSONs ──> leaderboard, match grid, personal cards
GitHub Actions cron drives freeze + scoring. Repo is public = open source + audit log.
```

**Key design decision**: The original Google Form was replaced with a custom mobile-first swipe-card
web app (site/picks.html). The Google Sheet remains as the storage backend — freeze.py still reads
the published CSV at `SHEET_CSV_URL`. Only the human-facing frontend changed.

**UTC-8 date grouping**: Campaign days are defined as `(kickoff_utc − 8h).date()` so late-night
US matches (e.g. 04:00 UTC June 12) stay with their correct campaign day (June 11). This logic
is identical in both picks.js and freeze.py — do not change it independently.

## 3. Tech stack

- **Python 3.8+**, stdlib + `requests`. No pandas, no frameworks. Scripts must use
  `from __future__ import annotations` for union type hints to work on Python 3.8.
- **GitHub Actions**: three workflows — `freeze.yml` (window-gated: picks close 3h
  before the day's first kickoff; PRIMARY trigger is cron-job.org polling the
  dispatch API every 3 min, GitHub's own 30-min cron is unreliable backup —
  see docs/RELIABILITY.md), `score.yml` (crons 01:15 + 09:30 UTC + manual
  dispatch), `test.yml` (every push).
- **GitHub Pages** serving `/site` (plain HTML/CSS/JS + Chart.js from CDN).
  No build step. No React. Pages read JSON via `fetch()`.
- **Human picks**: Custom web app (`site/picks.html`) → POST (mode: no-cors) →
  Google Apps Script (`scripts/apps_script.gs`, deployed manually) → Google Sheet →
  published CSV → `freeze.py` fetches `SHEET_CSV_URL` at freeze time.
- **noUiSlider v15.8.1** (CDN): 3-way probability slider in picks.html. Two handles
  divide the track into Home/Draw/Away segments. Segment colours: purple/grey/teal.
- **Secrets** (`.env` locally, GitHub Actions secrets in CI):
  `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `ODDS_API_KEY`,
  `SHEET_CSV_URL`, `GITHUB_TOKEN` (GitHub Models free tier for gpt-4o-mini —
  built-in in Actions via `permissions: models: read`, a PAT locally),
  `FOOTBALL_DATA_API_KEY` (optional — fetch_results.py). Never commit keys.
- **Apps Script CORS note**: Picks are POSTed with `mode: 'no-cors'` and no
  Content-Type header to avoid preflight. The response is opaque but the write succeeds.

## 4. The forecasters

FINAL LINEUP — frozen 2026-06-11 (single source of truth: `AI_LINEUP` in
scripts/freeze.py; freeze.py aborts if its output drifts from it):

| ID | Type | Source | Notes |
|----|------|--------|-------|
| `market` | benchmark | The Odds API free tier (500 req/mo) | h2h closing odds, de-vig (normalise implied probs) |
| `gemini-flash` | AI | Gemini 2.5 Flash, Gemini API free tier | temp 0, fixed prompt |
| `llama-70b` | AI | Llama 3.3 70B, Groq free tier | temp 0, fixed prompt |
| `gemma` | AI | Gemma, OpenRouter free models | temp 0, fixed prompt |
| `gpt-oss-120b` | AI | GPT-OSS-120B (OpenAI open-weights, reasoning), Groq free tier | temp 0, max_tokens 2048 (reasoning can spill into completion) |
| `gpt-4o-mini` | AI | GitHub Models free tier (GITHUB_TOKEN) | temp 0, fixed prompt |
| `human:<slug>` | human | Custom web app (picks.html) | slug = lowercased name, deduped to latest submission pre-freeze |
| `crowd` | derived | mean of all human triples per match, renormalised | computed at freeze, immutable after |

Claude was removed pre-launch (no free tier — see CHANGELOG.md 2026-06-11).
From the first scored matchday onwards this lineup only ever SHRINKS (a model
that loses its free tier retires); it is never swapped or extended — doing so
invalidates the experiment.

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
/CLAUDE.md                  <- this file (project bible, read by Claude Code on every session)
/README.md                  <- public-facing: what, why, methodology, how to reproduce
/CHANGELOG.md               <- every data correction logged here (never edit scores JSONs by hand)
/requirements.txt           <- pinned: requests>=2.31.0, pytest>=7.0.0. NO mid-tournament upgrades.
/scripts/
  freeze.py          ✅     <- fetch odds + query LLMs + ingest picks CSV + write predictions JSON
  score.py           ✅     <- compute Brier + calibration from results, write leaderboard.json
  fetch_results.py   ✅     <- football-data.org free tier; falls back to manual entry
  resolve_fixtures.py ✅    <- fills knockout teams + corrects kickoff times from
                              football-data.org (pins each KO slot to an API match
                              1:1 per stage via stable fd_id); runs in freeze.yml
                              (pre-freeze) + score.yml. Never edits a frozen match.
  make_fixtures.py   ✅     <- one-off completed: generated fixtures.json for all 104 matches
  apps_script.gs     ✅     <- Google Apps Script source (deployed manually); POST endpoint for picks
  freeze_local.py    ←      <- PENDING Phase 2: Ollama on Mac for qwen-laptop forecaster
  bias_lab.py        ←      <- PENDING Phase 3: anonymised-team counterfactual experiment
/data/
  fixtures/          ✅     <- fixtures.json (104 matches, all fields)
  predictions/              <- YYYY-MM-DD.json per campaign day (written by freeze.py pre-kickoff)
  results/                  <- results.json (appended by fetch_results.py or manual entry)
  scores/                   <- leaderboard.json + calibration.json (written by score.py)
/site/
  picks.html         ✅     <- mobile-first swipe-card picks UI (replaced Google Form)
  picks.js           ✅     <- matchday detection, noUiSlider, Apps Script POST, localStorage
  style.css          ✅     <- shared styles; scroll-snap layout, card styles, noUiSlider theming
  index.html         ✅     <- live leaderboard (fetches leaderboard.json)
  card.html          ←      <- PENDING Phase 2: personal calibration card, html2canvas PNG export
  methodology.html   ←      <- PENDING Phase 2: pre-registration artifact (publish asap)
/.github/workflows/
  freeze.yml         ✅     <- window-gated; cron-job.org polls dispatch every 3 min (primary), GitHub 30-min cron is backup; ntfy + healthcheck
  score.yml          ✅     <- crons 01:15 + 09:30 UTC + manual dispatch
/tests/
  conftest.py        ✅     <- sys.path setup so scripts/ is importable
  test_scoring.py    ✅     <- 37 unit tests passing (Brier, normalise, parse, slug, crowd, etc.)
  golden/            ←      <- PENDING Phase 1: synthetic golden-file data for integration test
```

## 9. Build phases & acceptance criteria

> **RELAUNCH (2026-06-11)**: launch was moved past matchday 1 to fix
> data-integrity defects (see CHANGELOG.md). The experiment starts at the
> first clean frozen matchday — Daksh enables the freeze cron when fixtures
> placeholders are resolved and the dress rehearsal passes. Earlier matches
> are unscored for everyone.

### Phase 0 — TONIGHT (before first kickoff, 11 Jun) — STATUS: COMPLETE ✅
Goal: matchday 1 predictions frozen and committed. Nothing else matters.
- [x] Repo created (public), this file at root, README stub.
- [x] `make_fixtures.py` → fixtures.json (104 matches, hand-verified matchday 1 rows).
- [x] Custom web app live (picks.html + picks.js + style.css) — replaced Google Form.
      Sheet published as CSV; URL in .env as SHEET_CSV_URL.
- [x] Apps Script deployed; source committed at scripts/apps_script.gs.
- [x] `freeze.py` working end-to-end for Gemini, Groq, OpenRouter, Odds API, picks CSV.
- [x] WhatsApp message sent with picks.html link.
- [ ] **Full dress rehearsal** — STILL PENDING (run before scoring any real results):
      `python scripts/freeze.py --date 2026-06-10 --dry-run` then hand-write a fake result
      → `python scripts/score.py` → verify leaderboard.json output.
Acceptance: `data/predictions/2026-06-11.json` committed before first kickoff at 21:00 UTC.

### Phase 1 — Days 1–3 — STATUS: MOSTLY COMPLETE ✅
- [x] `freeze.yml` cron (18:00 UTC daily — before the earliest kickoff each day).
- [x] `score.py` + `score.yml` (cron 06:00 UTC daily, scores yesterday's FT matches).
- [x] pytest suite: 37 unit tests passing, wired into CI on every push.
      Golden-file integration test PENDING (tests/golden/ has only .gitkeep).
- [x] `site/index.html` v1: leaderboard table (rank, badge HUM/AI/MKT, name,
      n, mean Brier, vs market). Static fetch of JSONs.
- [ ] Daily WhatsApp matchday card (site/daily.html or screenshot of index.html).
      Pending — generate manually until automated.
Acceptance: a colleague can submit picks, see them frozen, and find their rank
next morning without Daksh touching anything. ← needs first real freeze to verify.

### Phase 2 — Week 1 — STATUS: PENDING
- [ ] `card.html`: personal calibration curve + rank + plain-English one-liner
      ("overconfident on favourites by Xpp"), PNG export via html2canvas.
- [x] `crowd` forecaster computed at freeze (already in freeze.py).
- [ ] `freeze_local.py` on the Mac via launchd (daily, before CI freeze) for
      the Ollama model. Missed days logged as not-predicted.
- [ ] methodology.html complete (this is the pre-registration artifact — publish ASAP).

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
4. Freeze timing is self-adapting (3h before first kickoff — RELIABILITY.md);
   nothing to check daily unless FIFA moves a kickoff before 15:00 UTC.
5. Sunday: generate weekly chart, write 3-sentence LinkedIn post.

## 11. Risks & fallbacks

- **Free tier outage / rate limit** -> freeze.py's `_post_with_retries` rides
  out transient 5xx/429 (bounded backoff + jitter, honours Retry-After), and an
  end-of-pass retry sweep re-queries stragglers after ~40s — all within the
  same freeze (one cutoff). Failures store a reason. If a model still misses,
  log not-predicted (the 60% rule absorbs it). Never backfill across cutoffs.
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

**Navigation**: a shared bottom tab bar (`.tabbar` in style.css) links the
three public surfaces — ⚽ Picks / 🏆 Leaderboard / 📖 How it works — on
index.html, methodology.html, and the picks landing + confirmation screens.
The immersive cards screen omits the bar (it would fight the slider/scroll-snap)
and instead carries a 🏆 leaderboard link in its header. ops.html is private —
never in the public nav.

**Leaderboard presentation** (index.html): the pre-registered Brier score is
kept as the canonical number but presented for non-experts — colour bands vs
the 0.667 coin-flip line (green beats random, red worse), a persistent
plain-English explainer, "beats market"/"behind" word cues, and the Uniform
row tagged as the coin-flip line. Two views via tabs: **Overall** (cumulative —
the official ranking) and **By match** (per-match boards from match_scores.json,
display-only). Presentation only — the scoring spec (§7) is unchanged.

---

## 13. Testing strategy (risk-weighted — do not gold-plate)

**AUTHORITATIVE STANDARD: see /TESTING.md** (added 2026-06-11). It defines the
module lifecycle (requirement → test → implement → verify → freeze), risk
tiers, all test layers including frontend smoke tests, and CI enforcement.
Where this section conflicts with TESTING.md, TESTING.md wins — in particular,
frontend behavioural smoke tests ARE in scope (the "do NOT test HTML/CSS" rule
below is superseded; pixel-perfect/visual-polish testing remains out of scope).

Testing effort is allocated by what is UNRECOVERABLE, not by coverage %.
Unrecoverable: a freeze after kickoff; wrong scoring math discovered weeks in.
Budget: the suite stays small and behavioural, frozen alongside the scoring
rules.

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

### 14.1 Monitoring (all free) — see docs/RELIABILITY.md for the full stack
- GitHub Actions failure emails are ON by default — do not silence them.
- ntfy.sh push (topic `forecasting-arena-daksh`) fires from freeze.yml only
  when a freeze actually happens — SUCCESS confirmation before kickoff.
- **Dead-man's switch (live since 2026-06-14)**: freeze.yml pings a
  healthchecks.io check (`daily-freeze`, secret `HEALTHCHECK_URL`) on every
  successful freeze; if no day gets frozen by ~21:00 UTC it alarms by email —
  the one failure no error-email can catch (nothing ran to fail).
- **cron-job.org is the PRIMARY freeze trigger** — it polls the dispatch API
  every 3 min (12:00–20:57 UTC), so the freeze lands within ~3 min of the
  deadline. GitHub's own cron is unreliable (fired 6/18 runs, ~90 min apart, on
  2026-06-13/14) and is now only backup. PAT expires 2026-10-08.
- `validate_data.py` runs as the last step of score.yml across the whole
  /data tree (section 13.4 checks repo-wide). A red run = data problem.
- **Daily health digest (digest.yml, 10:00 UTC)**: runs verify_cycle's checks,
  triages each via scripts/ops_playbook.py (known issue → "auto-handled, no
  action" / "action needed: <how>" / unknown → needs a human), pushes one
  plain-English verdict to ntfy, and on action-needed/unknown opens a GitHub
  issue with an evidence pack (findings + stored model error reasons + run
  links). This replaces dashboard-watching: act only when the digest says so.
  Read-only — it never edits data or code (no auto-fix, by design).

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

