# ROADMAP — what's left to build (and in what order)

Snapshot taken 2026-06-12, just before the first real freeze (18:00 UTC,
campaign day 2026-06-12). Everything above this list is DONE: pipeline live
and CI-verified end-to-end, all secrets proven on GitHub's servers, site +
dashboard + methodology shipped, 52 tests green, fetch_results live-tested
against the real MEX 2–0 RSA result.

When returning to build mode: read this top to bottom, check the ops
dashboard is green, then pick up the highest unchecked item.

---

## 0. Verify the first unattended cycle (do this before building anything)

- [ ] Evening 2026-06-12: ntfy push ✅ at ~19:00 BST; ops.html shows freeze
      green with all 5 models ok and humans > 0
- [ ] Morning 2026-06-13: leaderboard populated on its own (score cron 06:00
      UTC) — first real rankings, zero manual steps
- [ ] If anything is red: incident playbook in CLAUDE.md §14.3; manual freeze
      via Actions → "Daily freeze" → Run workflow (supports date + dry_run)

## 1. card.html — personal calibration card  ← BUILD NEXT

The second of the two "must be excellent" surfaces (CLAUDE.md §12). Each
player's shareable report card:
- rank, mean Brier, n predicted, vs market
- calibration curve from data/scores/calibration.json with a plain-English
  one-liner ("when you say 70%, it happens 54% of the time — overconfident
  by 16 points")
- PNG export via html2canvas (CDN, no build step) for WhatsApp/Instagram
- This is the growth engine: people share their own results, not leaderboards.
- Data accumulates from 2026-06-12 onwards; meaningful after ~4 matchdays.

## 2. daily.html — auto-generated matchday card (small, pairs with #1)

One screenshot-able page: today's fixtures + current top 5 + biggest mover /
upset of yesterday. Turns the morning WhatsApp post into a 10-second
copy-paste. Reads fixtures.json + leaderboard.json, no backend.

## 3. Playwright smoke suite (TESTING.md §3.5 — last open testing gap)

~8 behavioural checks at 375px/1440px, light+dark: no-overlap invariant,
no-truncation, no-phantom-picks, pick round-trip, freeze-banner truth,
submission payload, placeholder rendering, leaderboard empty/error states.
Runner: `npx playwright test` against `python3 -m http.server` of /site.
Do this BEFORE the next significant UI change. Also closes TESTING.md §6 #10
(JS-side UTC-8 parity runner reading tests/fixtures/utc8_cases.json).

## 4. bias_lab.py — the headline research piece (~2026-06-24, matchday 3)

CLAUDE.md Phase 3: re-ask each model the same fixtures with anonymised teams
("Team A: Elo 2054, FIFA rank 5…" vs real names), same prompt shape, temp 0.
Output: per-model name-premium chart → site/biaslab.html + LinkedIn post.
Display-only: never feeds the leaderboard. The context_builder already
produces the stats block, so anonymisation is mostly string substitution.

## 5. Ongoing ops (not features)

- From 2026-06-28 (Round of 32): fill resolved knockout pairings into
  fixtures.json (real names + codes, is_placeholder → false). Ask Claude or
  edit by hand; freeze refuses TBD fixtures until then.
- That same week: check kickoff times — if any kickoff < 19:00 UTC, move the
  freeze.yml cron earlier AND FREEZE_HOUR_UTC in site/picks.js (must match).
- Sunday weekly audit (CLAUDE.md §14.2): spot-check 3 scored matches vs BBC,
  junk-name moderation, next week's kickoff times.

## 6. Phase 4 — post-final (after 2026-07-19)

- Freeze the arena (disable crons), final write-up page: headline findings,
  human-vs-AI-vs-market verdict, calibration galleries, full dataset zip
  (predictions + results + raw LLM responses), reproduction instructions.
- Final personal cards for all qualified humans.
- Tag a release; archive the repo read-only once the write-up ships.

---

## Parked / explicitly cut

- qwen-laptop local Ollama forecaster + freeze_local.py — cut at relaunch
  (lineup is frozen; do not revisit).
- Mid-tournament model additions of any kind — forbidden, invalidates the
  experiment (CLAUDE.md §4).
