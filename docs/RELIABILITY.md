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

| # | Layer | Fires (UTC) | Survives |
|---|-------|-------------|----------|
| 1 | GitHub crons, every 30 min | 12:00–20:30 (≈18/day; ~6 land inside any day's window) | several dropped crons in a row |
| 2 | **cron-job.org** → workflow dispatch API, hourly | 12:15–19:15 | GitHub's scheduler entirely dead (Actions itself still up) |
| 3 | **healthchecks.io** dead-man's switch | alarms ~21:00 if no real freeze pinged | *everything silent* — phone alarm tells Daksh to press the button |
| 4 | Human: dashboard one-click dispatch, or local `python scripts/freeze.py` + push; after first kickoff use **rescue mode** (`--remaining`) | any time | full GitHub Actions outage |

**Rescue mode** (layer 4): `--remaining` (also a checkbox on the manual Run
workflow form) freezes only the matches that haven't kicked off yet; passed
matches are void *for every forecaster equally* (the pre-registered incident
rule). So even a disaster discovered late in the evening still salvages the
remaining matches legitimately.

---

## One-time setup (Daksh) — layers 4 and 5, step by step

> Web UIs change their labels; if an exact label below doesn't exist, look
> for the nearest synonym — each step says what the setting must *achieve*,
> which is the part that matters. Steps 1–3 of layer 4 are testable from
> your terminal before any external website is involved.

### Layer 4 — cron-job.org (independent trigger) · ~10 min

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
   - **Permissions → Repository permissions** → find **Actions** → set to
     **Read and write**. (Leave everything else at No access.)
5. **Generate token** and copy the `github_pat_…` string somewhere safe.

**Step B — prove the token works (your terminal, before touching cron-job.org):**
```bash
curl -i -X POST \
  -H "Authorization: Bearer github_pat_PASTE_HERE" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/dakshjagwani/forecasting-arena-wc26/actions/workflows/freeze.yml/dispatches \
  -d '{"ref":"main"}'
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
3. **Execution schedule**: hourly through the afternoon — pick *custom* and
   set minute **15**, hours **12–19** (or cron expression `15 12-19 * * *`).
   Each dispatch is safe: freeze.py no-ops unless it's inside the 3h
   pre-kickoff window. ⚠ Check the timezone selector — set it to **UTC**
   (the account default is often Europe/Berlin).
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

### Layer 5 — healthchecks.io (dead-man's switch) · ~5 min

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
