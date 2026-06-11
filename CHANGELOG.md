# CHANGELOG — data corrections & experiment-affecting decisions

Every data correction and every decision that affects the experiment's
integrity is logged here, newest first. Scores JSONs are never hand-edited;
they are recomputed from raw data after any correction.

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
