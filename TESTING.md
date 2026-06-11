# TESTING.md — Validation Standard for The Forecasting Arena

This document is the **binding test and validation standard** for this project.
It supersedes CLAUDE.md §13 where they conflict (notably: frontend smoke tests
ARE now in scope — decided 2026-06-11 after shipping visible UI defects).
Claude Code: read this before building or modifying any module. No module is
"done" until it passes the gates defined here.

---

## 1. Core rule: the module lifecycle

Every change, no matter how small, follows this loop. No step is skipped
because a change "looks trivial" — the two UI bugs shipped on 2026-06-11
(theme toggle overlapping the user chip; default slider values auto-saved as
real picks) were both "trivial" changes.

```
1. REQUIREMENT   What must be true when this is done? Write it down
                 (one sentence per behaviour, in the PR/commit body).
2. TEST FIRST    For T0/T1 modules (see §2): write or update the failing
                 test BEFORE the implementation. For T2: write the
                 verification checklist entry before implementing.
3. IMPLEMENT     Smallest diff that makes the tests pass.
4. VERIFY        Run the full relevant gate (§4), not just the new test.
                 For UI: actually open the page at 375px AND 1440px,
                 light AND dark, and look at it.
5. FREEZE        Commit. A frozen module is only reopened with a new
                 requirement — never casually edited "while passing by".
```

**Definition of frozen**: tests exist, tests pass in CI, behaviour is
documented (here or in CLAUDE.md), and the commit is pushed. Frozen modules
(scoring math, prompt, freeze gates, model lineup) additionally require a
CHANGELOG.md entry to reopen.

**Regression rule**: every bug that reaches `main` gets a reproducing test
(or checklist item, for visual bugs) committed in the same PR as the fix.
No silent fixes.

---

## 2. Risk tiers — where the effort goes

Effort is allocated by *unrecoverability*, not coverage %.

| Tier | What | Failure cost | Standard |
|------|------|--------------|----------|
| **T0** | Freeze timing/immutability, Brier math, outcome derivation, qualification rule | Experiment invalidated, publicly | Unit + golden + runtime gates. Test-first, byte-exact where possible |
| **T1** | LLM parsing, odds de-vig, picks ingestion/dedup, crowd calc, schema of every committed JSON | Silent data corruption discovered weeks later | Unit + schema validation in CI + adversarial fixtures |
| **T2** | picks.html / index.html behaviour (submission, slider, freeze banner, rendering of edge-case data) | Lost/garbage human picks, lost trust | Automated smoke (§3.5) + manual checklist (§3.6) |
| **T3** | Visual polish, animations, copy | Embarrassment only | Eyeball during VERIFY; no automated tests |

---

## 3. Test layers

### 3.1 L1 — Unit tests (`tests/`, pytest)

Pure-logic functions only. Current suite covers Brier, normalise, parse,
slug, crowd, outcome, qualification. **Required additions** (gaps as of
2026-06-11):

- [ ] `group_by_utc8`: the 02:00–08:00 UTC kickoff boundary cases, and parity
      with the JS implementation (shared fixture file of input→expected pairs
      consumed by both pytest and the JS smoke test, so the two
      implementations can never drift).
- [ ] `parse_llm_response`: nested braces inside `reasoning`; reasoning-model
      output where prose/`<think>` text precedes the JSON and itself contains
      `{...}`; JSON split across the 512-token truncation point (must return
      None, never a partial parse).
- [ ] `ingest_human_picks`: post-freeze row rejected; duplicate (slug, match)
      latest-wins; percentage-scale rows; malformed timestamp skipped; row
      for non-today match ignored. (Currently untested — it's T1 and handles
      untrusted input.)
- [ ] `match_odds` fuzzy matching: must NOT match a placeholder fixture
      ("Winner UEFA Playoff D") to any real odds event.
- [ ] `fetch_odds` de-vig from a canned API payload (record one real response
      as a fixture).

### 3.2 L2 — Schema/contract validation (`scripts/validate_data.py`)

**Must be built — referenced by CLAUDE.md §14.1 but does not exist.**
Validates the entire `/data` tree; runs in CI on every push and as the last
step of both workflows. Checks:

- Every predictions file: matches schema; every triple sums to 1.0 ± 1e-4;
  every p in (0, 1); every match_id exists in fixtures.json; `freeze_utc`
  earlier than every kickoff in the file; no forecaster key outside the
  registered lineup + `human:*` + `crowd` + `market`.
- results.json: outcome consistent with score; status in known set; match_id
  referential integrity.
- leaderboard.json / calibration.json: no NaN/None where numbers expected;
  mean_brier in [0, 2]; row count == distinct forecasters scored.
- fixtures.json: 104 unique match_ids; kickoffs parse as UTC; placeholder
  flags consistent (`is_placeholder` ⇔ a TBD-style team name).
- Exit non-zero on any violation → red CI run = data incident.

### 3.3 L3 — Golden-file integration test (`tests/golden/`)

**Currently a stub that always passes — this is worse than no test.**
Make it real:

- `score.py` reads its root from `FORECASTING_ROOT` env var (defaulting to
  the repo root) so the test can point it at a temp tree.
- Commit synthetic `fixtures.json`, one predictions file (incl. a `failed`
  forecast, a human, crowd, market, and a forecaster below the 60% line),
  `results.json` (incl. a draw, an away win, a non-FT match that must be
  excluded).
- Run score.py; **byte-compare** output against committed
  `expected_leaderboard.json` and `expected_calibration.json`
  (excluding the `updated_utc` line).
- This is the regression net for any refactor of scoring. It runs in CI on
  every push.

### 3.4 L4 — Runtime gates (already partly in place)

Pre-flight in freeze.py (secrets present, freeze < earliest kickoff,
no double-freeze, output triples validated) — keep, and add:

- [ ] **Lineup gate**: the set of AI forecaster IDs queried must equal the
      registered lineup constant (single source of truth, one place in the
      repo). Freeze aborts if code and registry disagree — this would have
      caught the claude/deepseek-r1/gemma churn.
- [ ] **Placeholder gate**: explicit policy flag per fixture — placeholder
      matches are either excluded from the freeze or logged prominently;
      never silently forecast.
- Post-score gates in score.py — keep; move the repo-wide checks into
  validate_data.py (§3.2) so they also run on manual data edits.

### 3.5 L5 — Frontend smoke tests (Playwright, free, CI on every push)

Minimal, behavioural, viewport-aware. NOT pixel-perfect screenshots.
One spec file, ~10 checks, run at **375×667 (mobile)** and **1440×900
(desktop)**, in **light and dark** themes:

1. **No-overlap invariant**: for the fixed-position theme toggle vs every
   header element (`user-chip`, `progress-text`): bounding boxes must not
   intersect. (Catches the bug shipped 2026-06-11.)
2. **No-truncation invariant**: for `.team-name` and `.pct-lbl` of the
   longest real value ("Winner UEFA Playoff D"): element must either fully
   display text or have an explicit, designed truncation (title/tooltip
   attribute present). Assert `scrollWidth <= clientWidth` where full
   display is required.
3. **No phantom picks**: load the cards screen, swipe/scroll through all
   cards, do NOT touch any slider → `localStorage.fa_picks` must be empty
   and submit card must read "0 / N matches filled". (Catches the
   auto-save-on-bind bug shipped 2026-06-11.)
4. **Pick round-trip**: move one slider → values shown sum to 100, pick
   persisted, reload page → values restored, badge shows filled.
5. **Freeze banner truth**: banner countdown derives from the same constant
   the freeze actually uses; when mocked clock is past freeze time, sliders
   are disabled and submit is blocked.
6. **Submission payload**: intercept the POST → payload contains only
   touched matches, probabilities sum to 1.0, slug matches the Python
   `to_slug` golden pairs.
7. **Placeholder fixture rendering**: a `is_placeholder` match renders a
   clear "opponent TBD" treatment, not a broken white flag + truncated name.
8. **Leaderboard page**: loads with empty leaderboard.json (day 0), with a
   populated one, and with a missing file (graceful error, not blank page).

Runner: `npx playwright test` against a `python -m http.server` of `/site`
with fixture JSONs stubbed — no backend, no secrets, runs in <2 min in CI.

### 3.6 L6 — Manual pre-matchday checklist (until L5 exists, this is the gate)

Run on a phone + a laptop **before sharing any link or freezing any day**
after a frontend change:

- [ ] Landing → name → cards → submit → confirmation, both themes.
- [ ] Every card on today's matchday visually correct (names, flags, venue,
      local kickoff time vs official schedule).
- [ ] Untouched cards show NOT filled; submit count correct.
- [ ] Banner freeze time agrees with the actual cron/manual freeze plan.
- [ ] Submit, then check the Google Sheet row arrived with correct values.
- [ ] index.html renders current leaderboard.json.

---

## 4. CI wiring (the enforcement)

- `.github/workflows/test.yml`: pytest (L1 + L3) + validate_data.py (L2) on
  **every push and PR**. Red = do not freeze, do not share links.
- freeze.yml: runs its gates internally (L4); final step runs
  validate_data.py over the file it just wrote, *before* the git commit step.
- score.yml: validate_data.py as final step (already specified in CLAUDE.md
  §14.1 — now actually enforced).
- L5 Playwright job runs on pushes touching `site/**`.

A workflow that doesn't run in CI is documentation, not a test.

---

## 5. What we deliberately do NOT test

- Pixel-perfect rendering, animation timing, confetti, copy tone (T3).
- Third-party uptime (Odds API, Groq, Sheets) — handled by runtime
  fallbacks + ntfy alerts, not tests.
- LLM output *quality* — that's the experiment itself.

---

## 6. Gap inventory — priority order (2026-06-11)

| # | Gap | Tier | Status |
|---|-----|------|--------|
| 1 | No CI test workflow at all (CLAUDE.md claims tests are "wired into CI" — false) | T0 | CLOSED 2026-06-11 (test.yml) |
| 2 | Phantom default picks saved on card render (noUiSlider `update` fires on bind) | T1 | CLOSED 2026-06-11 (picks recorded on `slide` only) |
| 3 | Freeze banner time vs actual freeze time can disagree (banner says open after freeze.py already ran) | T0 | CLOSED 2026-06-11 (banner derives from cron schedule, `freezeTimeFor`) |
| 4 | validate_data.py missing | T1 | CLOSED 2026-06-11 (runs in test.yml, freeze.yml, score.yml) |
| 5 | Golden test is a no-op stub | T0 | CLOSED 2026-06-11 (real byte-compare vs expected files) |
| 6 | Placeholder fixtures forecast as if real (md002 frozen vs "Winner UEFA Playoff D") | T0 policy | CLOSED 2026-06-11 (freeze.py skips placeholders; picks UI shows TBD; voided file archived) |
| 7 | Theme toggle overlaps header chip; long team names truncated | T2 | CLOSED 2026-06-11 (header padding; wider labels + title attr) |
| 8 | No README.md / CHANGELOG.md (audit trail requirement) | T1 | CLOSED 2026-06-11 (+ methodology.html) |
| 9 | ingest_human_picks / odds fuzzy-match untested | T1 | CLOSED 2026-06-11 |
| 10 | UTC-8 grouping JS/Python parity untested | T1 | PARTIAL — Python side tested against tests/fixtures/utc8_cases.json; JS runner still pending (with the L5 Playwright suite) |
| 11 | L5 Playwright smoke suite (§3.5) | T2 | OPEN — manual checklist (§3.6) is the gate until then |
| 12 | fetch_results.py untested (manual-entry fallback is first-class, mitigates) | T1 | OPEN |
