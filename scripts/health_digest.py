#!/usr/bin/env python3
"""
health_digest.py — daily self-triaging ops report (Tiers 2–3, docs/RELIABILITY.md).

Runs the verify_cycle checks, classifies each finding via ops_playbook, composes
one plain-English digest, and:
  • pushes a short verdict to ntfy (the channel freeze.yml already uses), and
  • on 🚨 action-needed / ❓ unknown, opens a GitHub issue with an EVIDENCE PACK
    (the findings + that day's stored model error reasons + a link to recent runs)
    so a novel problem reaches a human already-diagnosed.

Healthy days: ntfy ✅ only, no issue. Read-only: never edits data or code.

  python scripts/health_digest.py            # run + push (CI)
  python scripts/health_digest.py --dry-run   # print only, no push/issue
  python scripts/health_digest.py --date 2026-06-15
"""
from __future__ import annotations
import argparse, json, os, sys, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import verify_cycle
from ops_playbook import classify

ROOT = Path(os.environ.get("FORECASTING_ROOT") or Path(__file__).parent.parent)
REPO = os.environ.get("GITHUB_REPOSITORY", "dakshjagwani/forecasting-arena-wc26")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "forecasting-arena-daksh")

# icon (for printed/issue text), label, ntfy priority, ntfy Tags shortcode.
# Emoji live in the UTF-8 body / ntfy Tags header — NEVER in the Title header,
# which urllib must encode as latin-1 (a raw emoji there raises a codec error).
_VERDICT = {
    "healthy": ("✅", "All healthy", "min", "white_check_mark"),
    "auto":    ("ℹ️", "Healthy — minor issues auto-handled, no action", "min", "information_source"),
    "unknown": ("❓", "Unknown condition — needs a look", "high", "question"),
    "action":  ("🚨", "Action needed", "urgent", "rotating_light"),
}

def compose(summary: dict, triage: dict) -> tuple:
    """Return (title, body) for the digest. Pure → unit-testable."""
    day = summary.get("day") or "—"
    icon, label = _VERDICT[triage["overall"]][:2]
    title = f"{icon} {day}: {label}"
    lines = [f"Campaign day {day} — {label}.", ""]
    if triage["action_items"]:
        lines.append("ACTION NEEDED:")
        for it in triage["action_items"]:
            lines.append(f"• {it['message']}")
            lines.append(f"  → {it['action']}")
        lines.append("")
    if triage["unknown_items"]:
        lines.append("UNKNOWN (needs a human):")
        for it in triage["unknown_items"]:
            lines.append(f"• {it['message']}")
        lines.append("")
    if triage["auto_items"]:
        lines.append("Auto-handled (no action):")
        for it in triage["auto_items"]:
            lines.append(f"• {it['why']}")
        lines.append("")
    lines.append(f"{triage['healthy_count']} checks passed.")
    return title, "\n".join(lines)

def evidence_pack(summary: dict) -> str:
    """Markdown evidence bundle for a GitHub issue: findings + stored model
    error reasons for the day + a link to recent freeze runs."""
    day = summary.get("day")
    out = ["## Findings\n"]
    for f in summary.get("findings", []):
        out.append(f"- {f['level']} `{f['code']}` — {f['message']}")
    # Stored model error reasons from that day's predictions (Part A stores them)
    pred = ROOT / "data" / "predictions" / f"{day}.json"
    if pred.exists():
        data = json.loads(pred.read_text())
        errs = []
        for m in data.get("matches", []):
            for fid, fc in m.get("forecasts", {}).items():
                if fc.get("status") == "failed":
                    errs.append(f"- `{fid}` @ {m['match_id']}: {fc.get('error', '(no reason stored)')}")
        if errs:
            out.append("\n## Model failure reasons\n")
            out.extend(errs)
    out.append("\n## Links\n")
    out.append(f"- [Recent freeze runs](https://github.com/{REPO}/actions/workflows/freeze.yml)")
    out.append(f"- [Recent score runs](https://github.com/{REPO}/actions/workflows/score.yml)")
    out.append(f"- [Ops dashboard](https://dakshjagwani.github.io/forecasting-arena-wc26/site/ops.html)")
    return "\n".join(out)

# ── Outbound (ntfy + GitHub issue) ────────────────────────────────────────────
def _ascii(s: str) -> str:
    """HTTP headers must be latin-1; strip anything that isn't (e.g. emoji)."""
    return s.encode("ascii", "ignore").decode().strip()

def push_ntfy(title: str, body: str, priority: str, tag: str = "") -> None:
    headers = {"Title": _ascii(title) or "Arena health digest", "Priority": priority}
    if tag:
        headers["Tags"] = tag  # ntfy renders this shortcode as an emoji icon
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}", data=body.encode("utf-8"),
        headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=15)

def open_issue(title: str, body: str, token: str) -> int | None:
    """Create a GitHub issue with the evidence pack. Returns issue number."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/issues",
        data=json.dumps({"title": title, "body": body, "labels": ["ops"]}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("number")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--dry-run", action="store_true", help="Print only; no push/issue")
    args = parser.parse_args()

    verify_cycle._JSON_MODE = True  # suppress verify_cycle's own per-line prints
    summary = verify_cycle.run_checks(args.date)
    triage = classify(summary)
    title, body = compose(summary, triage)
    needs_issue = triage["overall"] in ("action", "unknown")

    print(title)
    print(body)

    if args.dry_run:
        print(f"\n[dry-run] would push to ntfy; "
              f"{'would open a GitHub issue' if needs_issue else 'no issue (healthy)'}")
        return

    issue_no = None
    if needs_issue:
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            try:
                issue_no = open_issue(f"[ops] {title}", evidence_pack(summary), token)
            except Exception as e:
                print(f"issue creation failed: {e}", file=sys.stderr)

    ntfy_body = body + (f"\n\nEvidence: issue #{issue_no}" if issue_no else "")
    _, _, priority, tag = _VERDICT[triage["overall"]]
    try:
        push_ntfy(title, ntfy_body, priority, tag)
    except Exception as e:
        print(f"ntfy push failed: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
