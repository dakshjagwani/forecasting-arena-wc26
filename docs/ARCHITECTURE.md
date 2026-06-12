# Architecture — The Forecasting Arena

How every piece talks to every other piece. Diagrams render directly on
GitHub. Zero servers are owned: the entire system is GitHub Actions (compute),
the git repo (database), GitHub Pages (hosting), and free external APIs.

---

## 1. Bird's-eye view

```mermaid
flowchart LR
    subgraph EXT["☁️ External services (all free tiers)"]
        GEM["Gemini API\n(gemini-2.5-flash)"]
        GROQ["Groq API\n(llama-3.3-70b +\ngpt-oss-120b)"]
        OR["OpenRouter API\n(gemma-4-31b)"]
        GHM["GitHub Models\n(gpt-4o-mini)"]
        ODDS["The Odds API\n(h2h bookmaker odds)"]
        FD["football-data.org\n(match results)"]
        GAS["Google Apps Script\n(POST endpoint)"]
        SHEET[("Google Sheet\n(human picks)")]
        NTFY["ntfy.sh\n(push alerts)"]
    end

    subgraph GH["🐙 GitHub (compute + storage + hosting)"]
        subgraph ACT["Actions (cron, runs on GitHub's servers)"]
            FW["freeze.yml\n17:45 UTC daily\n(+retries, see RELIABILITY.md)"]
            SW["score.yml\n06:00 UTC daily"]
            TW["test.yml\non every push"]
        end
        subgraph DATA["repo /data (the database, append-only)"]
            FIX[("fixtures.json\n104 matches")]
            PRED[("predictions/\nYYYY-MM-DD.json")]
            RES[("results/\nresults.json")]
            SCORES[("scores/leaderboard.json\n+ calibration.json")]
            REF[("reference/\nelo, h2h, contexts")]
        end
        PAGES["GitHub Pages\nserves /site"]
    end

    subgraph USERS["📱 People"]
        HUMAN["Players\n(picks.html)"]
        DAKSH["Daksh\n(ops.html)"]
        PUBLIC["Audience\n(index.html)"]
    end

    HUMAN -- "probability triples\n(no-cors POST)" --> GAS --> SHEET
    SHEET -- "published CSV" --> FW
    GEM & GROQ & OR & GHM --> FW
    ODDS --> FW
    FIX --> FW
    REF --> FW
    FW -- "git commit\n(pre-kickoff, immutable)" --> PRED
    FW -- "✅/🚨" --> NTFY

    FD --> SW
    SW --> RES
    PRED & RES & FIX --> SW
    SW -- "git commit" --> SCORES

    PRED & SCORES & FIX & REF --> PAGES
    PAGES --> HUMAN & DAKSH & PUBLIC
    DAKSH -. "manual results entry\n(fallback)" .-> RES
```

**The key trick**: there is no backend. The git repo *is* the database, commits
*are* the audit log, and the site is static files reading JSON. Your laptop is
never required — both crons run on GitHub's machines.

---

## 2. One campaign day, in time order

```mermaid
sequenceDiagram
    participant H as Humans (phones)
    participant S as Google Sheet
    participant A as freeze.yml (17:45 UTC)
    participant L as 5 LLM APIs + Odds API
    participant R as repo /data
    participant P as GitHub Pages
    participant FB as football-data.org
    participant C as score.yml (06:00 UTC)

    Note over H,S: All day until 17:45 UTC
    H->>S: picks via picks.html → Apps Script
    Note over A: 17:45 UTC — THE FREEZE (retries 18:20/18:40 + external trigger)
    A->>A: gates: secrets? before kickoff? not already frozen?
    A->>L: identical prompt ×5 models, temp 0 + odds snapshot
    A->>S: fetch CSV, keep latest pre-freeze row per person
    A->>A: validate triples, compute crowd, lineup gate
    A->>R: commit predictions/YYYY-MM-DD.json  🔒 immutable
    A-->>H: ntfy push: ✅ Freeze OK
    Note over H: kickoff — matches play, site shows "locked"
    Note over C: 06:00 UTC next morning
    C->>FB: fetch final scores
    C->>R: append results.json
    C->>C: Brier per forecaster, qualification, calibration
    C->>R: commit leaderboard.json + calibration.json
    R->>P: Pages redeploys automatically
    P->>H: leaderboard updated — new rankings visible
```

---

## 3. freeze.py — internal anatomy (the integrity-critical script)

```mermaid
flowchart TD
    START([main]) --> ENV["load_dotenv()\n.env → environment"]
    ENV --> G1{"Gate 1: all 6 secrets\npresent?"}
    G1 -- no --> X1[/"exit 1 — nothing queried"/]
    G1 -- yes --> DAY["group_by_utc8(fixtures)\ncampaign day = (kickoff − 8h).date"]
    DAY --> PH["placeholder gate:\nskip is_placeholder fixtures"]
    PH --> G2{"Gate 2: now <\nearliest kickoff?"}
    G2 -- no --> X2[/"exit 2 — late freeze is VOID"/]
    G2 -- yes --> G3{"Gate 3: file already\nexists? (double-freeze)"}
    G3 -- yes --> X3[/"exit 0 — clean no-op,\nnever overwrites\n(retry-cron safe)"/]
    G3 -- no --> ODDS2["fetch_odds() → de-vig:\n1/odds, normalise to sum 1\nmatch_odds() alias-aware fuzzy match"]
    ODDS2 --> CTX["context_builder.get_context_block()\nElo + last-10 form + H2H + venue\n→ injected into every prompt"]
    CTX --> LOOP["per match × AI_LINEUP\n(single source of truth)"]
    LOOP --> Q["query_gemini / query_groq /\nquery_openrouter / query_github_models\ntemp 0 · 3 attempts · backoff"]
    Q --> PARSE["parse_llm_response()\ntries every {...} candidate —\nreasoning-text safe; None = 'failed',\nnever a default"]
    PARSE --> NORM["normalise(): clamp [0.01,0.98],\nrenormalise to sum 1.0"]
    NORM --> HUM["ingest_human_picks()\nlatest pre-freeze row wins ·\ntest-* slugs excluded"]
    HUM --> CROWD["compute_crowd()\nmean of human triples"]
    CROWD --> G4{"Gate 4: every triple sums\nto 1? forecaster ∈ lineup?"}
    G4 -- no --> X4[/"exit 4 — drift detected"/]
    G4 -- yes --> WRITE["write predictions/DATE.json\n(CI commits + pushes)"]
```

## 4. score.py — scoring anatomy

```mermaid
flowchart TD
    S([main]) --> LOAD["load fixtures (kickoff order)\n+ all predictions/*.json\n+ results.json"]
    LOAD --> FILT["scoreable = FT results with a\nvalid outcome AND a predictions entry,\nsorted by kickoff"]
    FILT --> BRIER["per (match, forecaster):\nbrier = Σ (p − y)²\nskips status='failed'"]
    BRIER --> UNI["uniform baseline row:\n(⅓,⅓,⅓) → 0.667 on every match"]
    UNI --> QUAL["qualification: n_predicted ≥\nceil(0.6 × matches scored since\nforecaster's FIRST prediction)"]
    QUAL --> RANK["rows sorted: qualified first,\nthen mean Brier ascending\n+ vs_market = mean − market's mean"]
    RANK --> CAL["calibration buckets 0–10%…90–100%:\npredicted prob vs actual frequency"]
    CAL --> OUT["leaderboard.json + calibration.json\n(idempotent — always recomputed\nfrom raw data, never edited)"]
```

---

## 5. Every file, its job, and who calls it

| File | What it does | Reads | Writes | Triggered by |
|---|---|---|---|---|
| `scripts/freeze.py` | Daily prediction freeze: odds + 5 LLMs + human picks → validated, immutable snapshot | fixtures.json, Sheet CSV, 6 APIs, reference/ | predictions/DATE.json | freeze.yml crons 17:45/18:20/18:40 UTC (or manual/external) |
| `scripts/score.py` | Brier scores, qualification, calibration, rankings | predictions/, results.json, fixtures.json | scores/*.json | score.yml cron 06:00 UTC |
| `scripts/fetch_results.py` | Pulls final scores; manual results entry stays first-class fallback | football-data.org | results.json | score.yml (continue-on-error) |
| `scripts/validate_data.py` | Audits the whole /data tree: schemas, sums, referential integrity, lineup membership, freeze-before-kickoff | everything in /data | exit code only | all 3 workflows + every push |
| `scripts/context_builder.py` | Builds Elo/form/H2H/venue context for prompts and the picks-UI intel panels | reference/*.csv,json | reference/match_contexts.json | imported by freeze.py |
| `scripts/apps_script.gs` | Source of the deployed Google Apps Script: appends POSTed picks to the Sheet (append-only by design) | POST body | Google Sheet rows | player submissions |
| `scripts/make_fixtures.py` | One-off: generated the 104-match fixtures.json | hand-curated data | fixtures.json | already done |
| `site/picks.html/.js` | Swipe-card picks app: campaign-day detection, 3-way slider, 3D carousel, localStorage, no-cors POST | fixtures.json, match_contexts.json | Sheet (via Apps Script) | players' browsers |
| `site/index.html` | Public leaderboard | scores/*.json | — | audience browsers |
| `site/ops.html` | Daksh's dashboard: freeze/run/results status + daily checklist | data/*, GitHub API | — | your browser |
| `site/methodology.html` | Pre-registration artifact: the frozen rules | — | — | — |
| `tests/` (52 tests) | Brier math, parsing, ingestion, UTC-8 parity, golden byte-compare pipeline test | tests/golden/, tests/fixtures/ | — | test.yml on every push |

## 6. Secrets — where each credential lives and what touches it

| Secret | Used by | Lives in |
|---|---|---|
| `GEMINI_API_KEY` | freeze.py → Gemini | .env + repo secret |
| `GROQ_API_KEY` | freeze.py → llama-70b **and** gpt-oss-120b | .env + repo secret |
| `OPENROUTER_API_KEY` | freeze.py → gemma | .env + repo secret |
| `GITHUB_TOKEN` | freeze.py → gpt-4o-mini via GitHub Models | PAT in .env; **built-in** in Actions (never appears in the secrets list — provided automatically via `permissions: models: read`) |
| `ODDS_API_KEY` | freeze.py → market forecaster | .env + repo secret |
| `SHEET_CSV_URL` | freeze.py → human picks ingestion | .env + repo secret |
| `FOOTBALL_DATA_API_KEY` | fetch_results.py | .env + repo secret |
| `APPS_SCRIPT_URL` | site/picks.js (baked into the page — it's public by nature) | repo secret kept for reference only; no workflow uses it |

## 7. Design invariants (the "why" behind the shape)

1. **Git is the referee.** A prediction only counts if its commit predates
   kickoff. That's why freeze aborts rather than ever writing late.
2. **One clock for everyone.** Models, market snapshot and humans share the
   17:45 UTC cutoff — nobody gets later information.
3. **Recompute, never edit.** score.py rebuilds everything from raw data on
   each run; corrections go into results.json + CHANGELOG.md, never into
   leaderboard.json by hand.
4. **The UTC−8 campaign day** (`(kickoff − 8h).date()`) keeps late-night US
   kickoffs grouped with their evening siblings. Implemented identically in
   picks.js and freeze.py, locked by a shared test-case file.
5. **Fail loud, fail void.** A model that errors gets `status: failed` for
   that match — never a substituted default. A missed freeze is a missed day
   for everyone equally.
