# CHANGELOG — data corrections & experiment-affecting decisions

Every data correction and every decision that affects the experiment's
integrity is logged here, newest first. Scores JSONs are never hand-edited;
they are recomputed from raw data after any correction.

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
