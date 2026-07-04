# CHANGELOG — data corrections & experiment-affecting decisions

Every data correction and every decision that affects the experiment's
integrity is logged here, newest first. Scores JSONs are never hand-edited;
they are recomputed from raw data after any correction.

## 2026-07-04 — Accurate knockout result display + self-healing lag correction

- **Extra-time/penalty results now show the real score.** We score the 90-min
  result (correct + pre-registered), but the card was showing only that — e.g.
  Argentina–Cabo Verde displayed "1–1" though it finished **3–2 a.e.t.**
  fetch_results.py now also captures `final_score` (the extra-time score) and
  the shootout `pens`; the per-match board headlines the real result
  (`3–2 a.e.t.`, or `1–1 · 3–4 pens`) and the sub-line clarifies "scored on the
  1–1 at 90'". Display-only — scoring/advancement unchanged.
- **Data correction (self-healing):** md081 (Belgium–Senegal) was **2–2 at 90'**,
  Belgium winning 3–2 in extra time, but was stored as `3–2 / home / regular` —
  captured during the FINISHED-transition lag when the API briefly reports the ET
  score as "regular", then frozen by the idempotency guard. Its **advancement**
  score was always correct (Belgium advanced); only the 90-min score/display were
  wrong. fetch_results.py now reconciles knockout results to the authoritative,
  post-match-stable API each run, so this class of lag error auto-corrects
  (md081 fixed to 2–2/draw/extra_time). Group results remain immutable once FT.
- 104 tests green; leaderboards recomputed.

## 2026-06-29 — KNOCKOUT PHASE: scoring switches to "who advances" (pre-registered)

**Experiment-defining change. Pre-registered before the md074 freeze (≈14:00 UTC
2026-06-29), i.e. before any knockout result it scores except md073 (below).**

The knockout phase is now a **second, separate experiment** scored on
**advancement** ("did you back the team that went through?"), not the 90-minute
result. Why: knockouts have no draw in the way users experience them — every
human R32 pick for md073 came in at `p_draw ≈ 0` — so a 90-min "draw" felt
disconnected from the game. The group stage (72 matches) stays **exactly as it
was**, scored on the pre-registered 3-way 90-min Brier; nothing there is altered.

Design:
- **From md074 onward, every forecaster answers "who advances" directly.** Humans
  use a new 2-way "tug-of-war" slider (no draw); models get a **new frozen
  knockout prompt** (`_PROMPT_*_KO` in freeze.py — same enriched context block:
  Elo, form, H2H, venue; only the question changes). Both are stored as a
  draw-less triple `(x, 0, 1−x)`, so the predictions schema and pipeline are
  unchanged. The prompt states the venue is neutral (no home advantage bar host
  nations) and that home/away is listing order only.
- **md073** (the one R32 match already played, frozen as 3-way) is the single
  transitional match: scored via a one-off ½-split derivation
  (`p_home + ½·p_draw`). Disclosed here.
- **Metric:** binary advancement Brier `(p_adv_home−y)² + (p_adv_away−(1−y))²`
  (same [0,2] scale; coin-flip line **0.50**, not 0.667). `advanced` comes from
  football-data `score.winner` (after ET/penalties); the 90-min `outcome` is
  still recorded for the result line. The **market** stays derived from its 1X2
  (it's an odds feed, not promptable); the ½-split is justified — penalty
  shootouts are ≈ a coin toss.
- **Two boards:** `leaderboard.json` is now group-stage-only (knockouts excluded,
  so draw-less knockout picks are never judged on the 3-way metric);
  `leaderboard_knockouts.json` carries the advancement board (`metric`/`coinflip`
  keys). Site: leaderboard "Group" + "Knockouts" tabs; per-match board shows
  "1–1 after 90' · {team} advanced on penalties".
- This **deviates from the original single-metric pre-registration**; it's
  legitimate because the group phase is complete & unaltered and the knockout
  method is pre-registered before its results (md073 excepted, frozen pre-kickoff).
- 104 tests (6 new: advancement prob/Brier, advancement leaderboard, the new
  knockout prompt + advancement JSON parse).

## 2026-06-28 — Knockouts-only leaderboard tab + calendar re-download fix

- **New "Knockouts" leaderboard tab** (index.html), alongside Overall and By
  match. It's a separate cumulative board that starts fresh at the Round of 32 —
  group-stage results don't count. `score.py` now factors the leaderboard build
  into `build_leaderboard(scoreable, …)` and runs it twice: once over all scored
  matches (→ `leaderboard.json`, byte-identical to before) and once over only
  knockout matches (→ `leaderboard_knockouts.json`). Qualification's denominator
  is measured *within* the subset, so the knockout board's "since you joined" is
  counted from each forecaster's first knockout pick. Overall stats row stays
  driven by the overall board; the front-end shares one table renderer.
- **Calendar re-download fix.** Because the 2026-06-28 fixture resolution
  corrected knockout kickoff times, reminders already downloaded from the old
  `picks.ics` were stale. `make_calendar.py` now emits a monotonic `SEQUENCE`
  (+ `LAST-MODIFIED`) per VEVENT, so a re-downloaded/re-subscribed calendar
  *updates events in place* (same stable UID) instead of being ignored or
  duplicated. Added a plain-English "already added these? times changed — re-add
  to refresh" note by every calendar CTA on picks.html.
- 98 tests (11 new: `is_knockout`, `build_leaderboard` subset scoping, golden
  empty-knockout-board assertion, calendar SEQUENCE monotonicity).

## 2026-06-28 — Knockout fixtures auto-resolve from football-data.org

- The knockout bracket shipped as placeholders (`home/away` null), so the picks
  UI showed **TBD vs TBD** and the matches were unvotable. Worse, the hand-built
  kickoff times had **drifted from the real schedule** (e.g. today's first R32
  listed 22:00Z but actually 19:00Z; only 13/32 still matched). Since the freeze
  window is "3h before first kickoff" computed from those times, a stale time
  would have frozen a match *after* it kicked off and voided it for everyone.
- Fix: new `scripts/resolve_fixtures.py` reads the whole bracket from the same
  `football-data.org/v4/competitions/WC/matches` endpoint `fetch_results.py`
  already uses. It pins each local knockout slot to an API match **1:1 within
  each stage** (16 R32, 8 R16, 4 QF, 2 SF, 1+1) — equal counts ⇒ a bijection, no
  match dropped or duplicated — and staples football-data's stable `id` onto each
  row as `fd_id`; thereafter teams + corrected times flow in by that id. Team
  names normalised via freeze.py's existing `ODDS_ALIASES` (USA, Côte d'Ivoire,
  Cabo Verde, DR Congo, Bosnia and Herzegovina). Integrity guards: never edits a
  match already in a predictions file (one-cutoff rule); never backfills a match
  whose kickoff has passed; writes only on change.
- Bootstrap (this commit): pinned all 32, resolved the 16 R32 teams, corrected
  all knockout times. Verified every knockout day's freeze still lands inside the
  cron-job.org poll window (12–20 UTC). `site/picks.ics` regenerated.
- Wired into CI: resolve runs before `freeze.py` (freeze.yml) and after
  `fetch_results.py` (score.yml), committing fixtures + calendar when changed.
- 87 tests (16 new in `tests/test_resolve.py`: bijection, count-mismatch abort,
  idempotence, name/code mapping, frozen-skip, past-kickoff-skip, golden run over
  a sanitised live API snapshot in `tests/golden/wc_matches_sample.json`).

## 2026-06-16 — Self-running ops: auto-triage health digest + evidence packs

- New daily **health digest** (digest.yml, 10:00 UTC) collapses the manual
  "watch dashboard → screenshot → diagnose" loop. verify_cycle.py now emits
  structured findings (`--json`, each tagged with a `code`); scripts/
  ops_playbook.py maps each code to a verdict (healthy / auto-handled, no
  action / action-needed-with-how / unknown→human); scripts/health_digest.py
  composes one plain-English verdict, pushes it to ntfy, and on
  action-needed/unknown opens a GitHub issue with an evidence pack (findings +
  that day's stored model error reasons + run links). Healthy days: ntfy ✅
  only. £0 (Actions + ntfy + issues, no LLM). Read-only — never edits data or
  code (auto-fix deliberately out of scope to preserve audit integrity).
- 66 tests (7 new: playbook triage table, classify precedence, digest
  composition, verify_cycle --json shape).

## 2026-06-15 (later) — Model query resilience (transient-error retries)

- On the 2026-06-15 freeze, gemini-flash failed one match (KSA–URU) with
  `HTTP 503` then `429` — Google's free endpoint transiently overloaded/rate-
  limited, not a bug. Our retry was too weak (3 tries over ~3s).
- Fix (transport-only, **no protocol change** — temp 0 means each model's
  answer is deterministic, so retrying only affects whether we *receive* it):
  a shared `_post_with_retries` with bounded exponential backoff + jitter,
  honouring `Retry-After`, failing fast on non-retryable 4xx; all 5 providers
  use it; failures now store the reason (`{"status":"failed","error":...}`);
  and an **end-of-pass retry sweep** re-queries any still-failed model after a
  ~40s cooldown — recovering transient blips *within the same freeze* (one
  cutoff), never backfilling later. Retry budget is bounded (4 attempts, 8s
  cap) so a genuinely-dead endpoint can't balloon the run.
- The 2026-06-15 gemini KSA–URU miss is left as an honest, logged miss (not
  backfilled); the 60% qualification rule absorbs it.

## 2026-06-15 — Freeze trigger re-architected: cron-job.org primary @ 3 min

- **Problem:** the first three matchdays (06-12/13/14) were all frozen
  manually. Root cause was latency, not failure. GitHub's scheduled cron is
  unreliable — configured for ~18 runs/day it fired only 6, ~90 min apart, on
  both 06-13 and -14 (matches GitHub's documented "delayed/dropped under load"
  behaviour). The cron-job.org backup only polled hourly, so worst-case
  deadline→freeze latency was ~60 min; seeing the deadline pass with no freeze,
  Daksh kept triggering it by hand (e.g. 06-14: window opened 15:00, manual
  freeze 15:05, the automatic 15:15 trigger would have done it 10 min later).
- **Fix:** cron-job.org is now the PRIMARY trigger, polling the freeze.yml
  dispatch API **every 3 minutes** (`*/3 12-20 * * *` UTC). The freeze now
  lands within ~3 min of the deadline. GitHub's 30-min cron is demoted to
  best-effort backup. No-op attempts exit before any API call, so frequent
  polling is free. The ops dashboard now shows "window open — freeze fires
  automatically, do not intervene" during the gap, removing the manual reflex.
- **Unchanged:** the 3h-before-kickoff deadline, immutability (never
  overwrites), late-freeze abort, scoring, lineup. Pure trigger-reliability fix.

## 2026-06-26 — Calendar pick-reminders (retention) + clearer wording

- New `scripts/make_calendar.py` generates `site/picks.ics`: one native
  calendar reminder per remaining matchday, firing ~3h before the deadline
  (first kickoff − 3h). Players tap "📅 add reminders" on the picks site
  (confirmation + landing) to download it, or use the `webcal://` subscribe
  link for auto-updates. No login, no contact info, £0 — the phone's own
  calendar fires the alarm. Deadlines are written in **UTC**, so each device
  localises them correctly worldwide (DST-safe). Regenerated daily by
  score.yml. Reminder copy: "⚽ Your move vs the AI — predict now".
  Covers **every remaining matchday through the Final**, including knockout
  days whose teams are still TBD — their kickoff slots are already scheduled,
  so the deadline (kickoff − 3h) is known regardless of which teams advance.
- Player-facing banner wording de-jargoned: "Freeze in X" → "closes in X",
  "Picks locked" → "Picks closed for today". ("Freeze" stays in
  internal/ops/methodology contexts where it's the accurate term.)

## 2026-06-14 — Full reliability stack operational

- Failover layers 2 and 3 (docs/RELIABILITY.md) are now configured and
  verified end-to-end: **cron-job.org** independent hourly trigger
  (`arena-freeze-backup`, 12:15–19:15 UTC, PAT expires 2026-10-08) and the
  **healthchecks.io** dead-man's switch (`daily-freeze`, `0 12 * * *` UTC /
  9 h grace, ping URL in repo secret `HEALTHCHECK_URL`, email alerts). A test
  freeze confirmed the workflow pings the check. All five layers — GitHub
  crons, external trigger, dead-man's switch, and manual/rescue — are live.

## 2026-06-13 (later) — Leaderboard UX overhaul + site navigation

- **Fixed a scroll bug** on the leaderboard: it inherited the picks-app's
  `html,body{overflow:hidden}` and so couldn't scroll past the fold — which
  also hid the per-match boards entirely. Added the one-line override
  index.html was missing.
- **Brier made legible** (presentation only — scoring unchanged): colour bands
  vs the 0.667 coin-flip line, a persistent plain-English explainer, "beats
  market"/"behind" cues, column relabelled "Score (lower = better) ⓘ", Uniform
  row tagged as the coin-flip line.
- **Overall / By-match tabs**: the cumulative ranking (official) and the
  per-match boards are now two tabs instead of stacked, so both are obvious
  and reachable.
- **Site navigation**: shared bottom tab bar (Picks / Leaderboard / How it
  works) on index, methodology, and the picks landing + confirmation screens;
  a 🏆 link in the cards-screen header. Fixes "the only way to the leaderboard
  was to submit picks first".

## 2026-06-13 — Adaptive freeze window; scoring schedule fixed; per-match boards

- **Freeze is now window-gated, not clock-fixed**: picks close **3 hours
  before the day's first kickoff**. Fixture analysis showed first kickoffs
  range 16:00–22:00 UTC (e.g. 18:00 UTC on 2026-06-14, 16:00 UTC on
  2026-06-15) — any fixed freeze time would have been after kickoff or had
  no retry margin on those days. Scheduled attempts run every 30 min
  (12:00–20:30 UTC) and self-gate: too early → no-op, already frozen →
  no-op, inside the window → freeze. Same information cutoff for models,
  market and humans, as pre-registered. Applied BEFORE any affected matchday.
- **Scoring crons moved to 01:15 + 09:30 UTC** (was 06:00): last kickoffs
  run as late as 07:00 UTC, whose results the old time missed by ~22h.
  Evening games now score the same night.
- **Per-match boards added** (`data/scores/match_scores.json` + a section on
  the leaderboard page): a mini-leaderboard for every scored match, grouped
  by matchday. Display-only — the cumulative table remains the official
  pre-registered ranking (golden test confirms it is byte-identical).
- Raw LLM responses now stored up to 4000 chars (was 1000) for the bias lab
  and post-hoc analysis. Additive only; no scored field changes.

## 2026-06-12 (later) — Six-layer freeze failover (docs/RELIABILITY.md)

- Third GitHub cron (18:40 UTC); external independent trigger via
  cron-job.org → workflow dispatch API (18:30 UTC); healthchecks.io
  dead-man's switch (alerts if no successful freeze pinged by ~18:50);
  rescue mode `--remaining` freezes only not-yet-kicked-off matches after a
  late incident (passed matches void for everyone — the pre-registered rule).
- Picks page now rolls to the next matchday automatically in open tabs and
  keeps the freeze countdown live (30s watcher) — previously required a
  reload after the day's first kickoff.
- The advertised pick deadline is now uniformly **17:45 UTC (18:45 UK)**
  across the banner, dashboard, and all documentation.

## 2026-06-12 — First freeze: cron skipped, manual freeze ran in time; schedule hardened

- GitHub dropped the 18:00 UTC scheduled run entirely (a known weakness of
  on-the-hour cron slots). The freeze was dispatched manually at 18:29 UTC
  and completed at ~18:31 UTC — **before the 19:00 UTC first kickoff**, so
  the freeze is valid: 2 matches, 5/5 models ok on both, market odds on
  both, 9 human forecasters. The experiment's first matchday stands.
- Hardening so this cannot recur: primary cron moved to the off-peak minute
  **17:45 UTC** with an automatic retry at **18:20 UTC**; an already-frozen
  day is now a clean no-op (exit 0) instead of an error, so the retry adds
  no false alarms while immutability is unchanged (the no-op path never
  writes). The picks-page banner and ops dashboard now show 17:45 UTC.

- Submissions whose slug is `test` or starts with `test-` are never ingested
  at freeze time. Registered before the first freeze so it cannot
  retroactively affect any scored forecaster.

## 2026-06-12 — EXPERIMENT LIVE from the 2026-06-12 campaign day

- Freeze cron re-enabled (18:00 UTC daily). The first scored matchday is
  **2026-06-12** (Canada vs Bosnia and Herzegovina, USA vs Paraguay).
  Matches 1–2 (Mexico–South Africa, South Korea–Czechia) predate the launch
  and are unscored for all forecasters. The lineup is now permanently frozen.
- `FOOTBALL_DATA_API_KEY` added to Actions secrets — results fetch + scoring
  (06:00 UTC daily) is now fully automated, with manual entry as fallback.

## 2026-06-11 (later still) — deepseek-r1 replaced pre-launch

- The dress rehearsal revealed OpenRouter has **discontinued its free
  DeepSeek R1 tier** (404 on `deepseek/deepseek-r1:free`; no free DeepSeek
  models remain on the platform). Because the experiment has not yet scored
  a single match, a pre-launch replacement is legitimate: `deepseek-r1` →
  **`gpt-oss-120b`** (OpenAI's open-weights reasoning model, Groq free
  tier), keeping a reasoning model in the lineup. Verified working with a
  live call. This is the LAST lineup change — from the first scored
  matchday, models can only retire.

## 2026-06-11 (later) — Playoff placeholders resolved; freeze cron paused

- **Six group-stage placeholder slots resolved** with the March 2026 playoff
  results (verified against worldcupwiki.com and NBC Sports; group
  assignments cross-checked against our fixture groups):
  UEFA Path A → Bosnia and Herzegovina (Group B), Path B → Sweden (Group F),
  Path C → Türkiye (Group D), Path D → Czechia (Group A),
  Inter-confederation 1 → DR Congo (Group K), 2 → Iraq (Group I).
  18 fixture team slots updated; `is_placeholder` flipped to false.
- **Odds matching made alias-aware** (latent bug: bookmaker names like
  "United States"/"Czech Republic" would never fuzzy-match fixture names
  "USA"/"Czechia", silently dropping the market forecast for those matches).
- **freeze.yml cron paused** (manual dispatch still available) while the site
  is in early-tester mode. The experiment clock starts when the cron is
  re-enabled for the chosen relaunch matchday.

## 2026-06-11 — Relaunch decision & final model lineup

- **Launch moved into the group stage.** The original plan was to go live for
  matchday 1 (11 Jun). During final review we found defects that could have
  polluted the dataset (see below), so the experiment now starts at its first
  *clean* frozen matchday instead. Matches played before that freeze are not
  scored for any forecaster — fair, since nobody is scored on them.
- **The 2026-06-11 prediction file is VOID** and moved to
  `data/archive/2026-06-11.voided.json`. It was produced during development
  with a since-revised model lineup (contains `claude` entries from an
  aborted paid-API experiment) and was never scored. It remains in git
  history and the archive for transparency.
- **Final AI lineup frozen** (`AI_LINEUP` in scripts/freeze.py):
  `gemini-flash`, `llama-70b`, `gemma`, `deepseek-r1`, `gpt-4o-mini`.
  References: `market`, `crowd`, `uniform`. Claude is removed — no free API
  tier, and the £0 principle is non-negotiable. `gpt-4o-mini` now runs on the
  GitHub Models free tier (was: paid OpenAI API, which failed on credits).
  From the first scored matchday onwards this lineup only ever shrinks
  (a model that loses its free tier retires), never changes.
- **Defects fixed before launch** (would have corrupted human data):
  1. Viewing a match card auto-saved the default 40/30/30 slider position as
     a real pick (noUiSlider fires `update` on bind). Picks are now only
     recorded on user interaction.
  2. The picks app showed a freeze countdown later than the actual freeze
     time, so picks made in that window would have been silently void. The
     banner now derives from the real freeze schedule (18:00 UTC cron,
     earlier if a kickoff requires it).
  3. Placeholder fixtures ("Winner UEFA Playoff D") were forecast as if they
     were real teams. freeze.py now skips placeholder fixtures and the picks
     app shows them as "opponent not confirmed" without a slider.
- **Fixture data fix**: 32 knockout fixtures (Round of 32 onwards) had null
  teams but `is_placeholder: false`; all are now flagged placeholder. Ops
  rule going forward: when a knockout pairing is decided, fill in the real
  team names/codes in fixtures.json and flip `is_placeholder` to false —
  the freeze refuses to forecast a fixture while it stays placeholder.
- **Validation hardened**: scripts/validate_data.py audits the whole /data
  tree in CI; the golden-file integration test now actually compares output
  (it previously always passed); freeze.py aborts if the forecasters it
  produced drift from the frozen lineup.
