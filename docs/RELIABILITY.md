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
3. **Execution schedule**: pick *custom* / *every day at…* and set
   **18:30**. ⚠ Check the timezone selector next to the time — set it to
   **UTC** (the account default is often Europe/Berlin; 18:30 Berlin would
   be 16:30/17:30 UTC and fire before picks close).
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
   - Expression: `45 17 * * *` · **Time zone**: UTC · **Grace time**: 65
     minutes. Meaning: it *expects* one ping daily at 17:45 UTC and raises
     the alarm at ~18:50 UTC if none arrived — which is precisely "no freeze
     attempt succeeded today".
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
