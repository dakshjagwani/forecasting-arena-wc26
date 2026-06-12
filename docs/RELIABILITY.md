# RELIABILITY — how the daily freeze survives failures

The freeze is the one unrecoverable daily event: if it doesn't run before
kickoff, the matchday is lost for everyone. GitHub's cron scheduler is
explicitly best-effort (it silently dropped our 18:00 run on 2026-06-12), so
no single trigger is trusted. Six independent layers, ordered by when they
fire — **a matchday is lost only if all six fail**:

| # | Layer | Fires at (UTC) | Survives |
|---|-------|---------------|----------|
| 1 | GitHub cron (primary) | 17:45 | normal operation |
| 2 | GitHub cron (retry) | 18:20 | one dropped/delayed cron |
| 3 | GitHub cron (retry 2) | 18:40 | two dropped crons |
| 4 | **cron-job.org** → workflow dispatch API | 18:30 | GitHub's scheduler being entirely dead (Actions itself still up) |
| 5 | **healthchecks.io** dead-man's switch | alerts ~18:50 if no success ping | *everything silent* — phone alarm tells Daksh to press the button |
| 6 | Human: dashboard one-click dispatch, or local `python scripts/freeze.py` + push; after first kickoff use **rescue mode** (`--remaining`) | any time | full GitHub Actions outage |

Layers 1–3 are no-risk retries: freeze.py treats an already-frozen day as a
clean no-op (exit 0) and **never overwrites** — immutability is enforced by
the script, not the scheduler.

**Rescue mode** (layer 6): `--remaining` (also a checkbox on the manual Run
workflow form) freezes only the matches that haven't kicked off yet; passed
matches are void *for every forecaster equally* (the pre-registered incident
rule). So even a disaster discovered at 21:00 UTC still salvages the
late-evening matches legitimately.

---

## One-time setup (Daksh, ~5 min each) — layers 4 and 5

### Layer 4 — cron-job.org (independent trigger)

1. Create a **fine-grained GitHub PAT**: Settings → Developer settings →
   Fine-grained tokens → Generate. Repository access: *Only
   forecasting-arena-wc26*. Permissions → Repository → **Actions:
   Read and write**. Expiry: after 2026-07-19.
2. Sign up at <https://cron-job.org> (free) → Create cronjob:
   - URL: `https://api.github.com/repos/dakshjagwani/forecasting-arena-wc26/actions/workflows/freeze.yml/dispatches`
   - Schedule: daily **18:30 UTC**
   - Request method: **POST**
   - Headers:
     - `Authorization: Bearer <the PAT>`
     - `Accept: application/vnd.github+json`
   - Request body: `{"ref":"main"}`
   - Enable failure notifications (so a 401/404 emails you).
3. Test it once with "Execute now" — a `Daily freeze` run should appear in
   Actions (it will no-op if today is already frozen — that's correct).

### Layer 5 — healthchecks.io (dead-man's switch)

1. Sign up at <https://healthchecks.io> (free) → Add check:
   - Name: `daily-freeze` · Schedule: **cron `45 17 * * *`, grace 65 min**
     (alarm fires ~18:50 UTC if no ping arrived)
   - Add your email and/or the ntfy integration for alerts.
2. Copy the ping URL (`https://hc-ping.com/<uuid>`) and store it:
   `gh secret set HEALTHCHECK_URL` (freeze.yml pings it on every real
   successful freeze; the step is skipped harmlessly until the secret exists).

---

## Knockout-round caveat

Some knockout kickoffs are 17:00 UTC. That week, shift **all** trigger times
~2h earlier: the three crons in freeze.yml, the cron-job.org schedule, the
healthchecks.io schedule, and `FREEZE_UTC_H/M` in site/picks.js (the banner
must always show the PRIMARY trigger time). The Sunday audit covers this.

## The user-facing contract

Picks lock at **17:45 UTC (18:45 UK)** daily — the primary trigger. Layers
2–4 are late safety nets: human picks submitted after 17:45 are still
*technically* ingested by a later-firing retry (the freeze reads the sheet at
run time), but nobody should be told that — the advertised deadline is 17:45
UTC, and the banner, docs and marketing all say so.
