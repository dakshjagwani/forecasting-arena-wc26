# RELIABILITY — how the daily freeze survives failures

The freeze is the one unrecoverable daily event: if it doesn't run before
kickoff, the matchday is lost for everyone. Two facts shape the design:

1. **GitHub's cron scheduler is best-effort** — it silently dropped our
   18:00 run on 2026-06-12.
2. **No fixed clock time works**: first kickoffs range from 16:00 UTC to
   22:00 UTC across the tournament, so any fixed freeze time is either after
   some kickoffs or absurdly early for others.

So the freeze is **window-gated and self-deciding**: picks close
**3 hours before the day's first kickoff** (`FREEZE_WINDOW` in freeze.py),
and *many* triggers fire all afternoon — each run decides for itself:
too early → no-op · already frozen → no-op · inside the window → freeze.
Immutability is enforced by the script (an existing file is never
overwritten), not by the scheduler, so unlimited retries are free.

| # | Layer | Fires (UTC) | Survives | Status |
|---|-------|-------------|----------|--------|
| 1 | GitHub crons, every 30 min | 12:00–20:30 (≈18/day; ~6 land inside any day's window) | several dropped crons in a row | ✅ live |
| 2 | **cron-job.org** → workflow dispatch API, hourly | 12:15–19:15 | GitHub's scheduler entirely dead (Actions itself still up) | ✅ live |
| 3 | **healthchecks.io** dead-man's switch | alarms ~21:00 if no real freeze pinged | *everything silent* — phone alarm tells Daksh to press the button | ✅ live |
| 4 | Human: dashboard one-click dispatch, or local `python scripts/freeze.py` + push; after first kickoff use **rescue mode** (`--remaining`) | any time | full GitHub Actions outage | always available |

### Live configuration (set up 2026-06-13/14)

- **Layer 2 — cron-job.org** job `arena-freeze-backup`: POST to the
  freeze.yml dispatch API, schedule `15 12-19 * * *` UTC, body `{"ref":"main"}`,
  fine-grained PAT with **Actions: write**. ⚠ **Token expires 2026-10-08** —
  after the 2026-07-19 final, so no in-tournament gap, but reissue + repaste
  into the job's Authorization header before then if the project outlives it.
- **Layer 3 — healthchecks.io** check `daily-freeze`: cron `0 12 * * *` UTC,
  grace 9 h; its ping URL is stored as the repo secret `HEALTHCHECK_URL`;
  freeze.yml pings it on every successful freeze; Email integration →
  daksh.jagwani7@gmail.com (confirmed). Verified pinging end-to-end.

**Rescue mode** (layer 4): `--remaining` (also a checkbox on the manual Run
workflow form) freezes only the matches that haven't kicked off yet; passed
matches are void *for every forecaster equally* (the pre-registered incident
rule). So even a disaster discovered late in the evening still salvages the
remaining matches legitimately.

---

## One-time setup (Daksh) — layers 2 and 3 · DONE 2026-06-13/14

> ✅ Both layers are already configured and verified (see "Live configuration"
> above). The steps below are kept as the reference for re-doing them — e.g.
> reissuing the cron-job.org token after 2026-10-08, or recreating the
> healthchecks.io check. Web UIs change their labels; each step says what the
> setting must *achieve*, which is the part that matters.

### Layer 2 — cron-job.org (independent trigger) · ~10 min

**Step A — create the token (on github.com):**
1. Click your avatar (top-right) → **Settings**.
2. Left sidebar, scroll to the very bottom → **Developer settings**.
3. **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
   (If your UI shows "Tokens (classic)" only: generate a classic token with
   the `workflow` scope instead — that also works.)
4. Fill in:
   - **Token name**: `arena-freeze-trigger`
   - **Expiration**: Custom → any date after 2026-07-19
   - **Resource owner**: your account
   - **Repository access**: *Only select repositories* → `forecasting-arena-wc26`
   - **Permissions**: the list starts EMPTY showing "No repository permissions
     added yet" — that's expected. Click **+ Add permissions** (top-right of
     the box), type **`Actions`**, select it, and set its access to
     **Read and write**. **Metadata: Read-only** is then added automatically
     (GitHub requires it — leave it). The box should read "Repositories 2".
     Nothing under the **Account** tab is needed.
5. **Generate token** and copy the `github_pat_…` string somewhere safe.

**Step B — prove the token works (your terminal, before touching cron-job.org):**
Paste this as ONE line (don't copy the word `bash` from any code fence — that
just opens a new shell and prints a macOS banner; the request never runs).
Replace the token with your real `github_pat_…`:

```
curl -i -X POST -H "Authorization: Bearer github_pat_PASTE_HERE" -H "Accept: application/vnd.github+json" https://api.github.com/repos/dakshjagwani/forecasting-arena-wc26/actions/workflows/freeze.yml/dispatches -d '{"ref":"main"}'
```
Success = response `HTTP/2 204` (no body) **and** a new "Daily freeze" run
appearing at github.com → Actions within seconds (it will no-op green if
today is already frozen — that is correct). A `401` means the token was
pasted wrong; `403/404` means the Actions permission or repo selection is
wrong. Don't proceed until this works — after this, cron-job.org is *only*
replaying this exact request on a schedule.

**Step C — schedule it (cron-job.org):**
1. Sign up free → **Create cronjob** (button top-right of the Cronjobs page).
2. **Title**: `arena-freeze-backup` · **URL**: the same API URL from Step B.
3. **Execution schedule**: pick **Custom** and set the crontab expression to
   `15 12-19 * * *` (fires at :15 past each hour, noon–7pm). Each dispatch is
   safe: freeze.py no-ops unless it's inside the 3h pre-kickoff window.
   ⚠ **Timezone**: the "Next executions" panel shows the job's timezone
   (defaults to your locale, e.g. Europe/London). Set it to **UTC** and keep
   `15 12-19`. If you can't switch it off a UK/Berlin locale, the whole
   tournament is on summer time (UTC+1), so use `15 13-20 * * *` instead —
   same 12:15–19:15 UTC. Getting this wrong fires the backup outside the
   window on late-kickoff days.
4. Open the **Advanced** tab:
   - **Request method**: POST
   - **Headers** — add two:
     `Authorization` = `Bearer github_pat_…` and
     `Accept` = `application/vnd.github+json`
   - **Request body**: `{"ref":"main"}`
5. Save, then use **Execute now** once: the History entry should show
   status **204** and a fresh run in GitHub Actions.
6. In cron-job.org settings, make sure **failure notifications** (email on
   non-2xx) are on — that's how you learn the PAT expired.

### Layer 3 — healthchecks.io (dead-man's switch) · ~5 min

1. Sign up free at <https://healthchecks.io> → you land on a project with a
   default check, or click **Add Check**.
2. Click the check's name to open it → **Change Schedule**:
   - Choose the **Cron** tab (not "Simple"/"Period").
   - Expression: `0 12 * * *` · **Time zone**: UTC · **Grace time**: 9
     hours. Meaning: it expects one ping per day arriving between 12:00 and
     21:00 UTC — the freeze pings whenever it actually fires (12:00–20:30
     depending on that day's first kickoff), and silence by ~21:00 UTC
     raises the alarm: "no day was frozen today".
3. On the check page copy the **ping URL** — looks like
   `https://hc-ping.com/xxxxxxxx-xxxx-…`.
4. Store it as a repo secret (from the project folder):
   ```bash
   gh secret set HEALTHCHECK_URL
   # paste the ping URL when prompted
   ```
   freeze.yml already pings it after every real successful freeze; until the
   secret exists the step skips silently, so this is safe to do anytime.
5. Wire the alarm to your phone: check page → **Integrations** — add Email
   (instant) and/or **ntfy** (enter topic `forecasting-arena-daksh`, server
   `https://ntfy.sh`) so the alert lands in the same app as freeze pushes.
6. Test: open the ping URL once in your browser (that registers a ping →
   check turns green/"up"), then you're done. Tomorrow's 17:45 freeze keeps
   it green forever; silence pages you.

---

## Knockout rounds: nothing to move

The window adapts by construction — early kickoffs simply open the window
earlier (a 16:00 UTC first kickoff freezes from 13:00 UTC; the 12:00-onwards
cron attempts cover everything down to a 15:00 UTC kickoff). Only if FIFA
ever schedules a first kickoff before 15:00 UTC would the cron range in
freeze.yml + cron-job.org need extending earlier. The Sunday audit checks
kickoff times anyway.

## The user-facing contract

Picks lock **3 hours before the day's first kickoff** — one rule, every day.
The picks-page banner computes and shows the exact countdown, so users never
need to do timezone math. A later attempt could technically ingest a pick
submitted after the window opened but before the freeze actually fired —
nobody should be told that; the advertised deadline is the 3-hour rule, and
the banner, docs and marketing all say so.
