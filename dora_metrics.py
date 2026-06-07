# -*- coding: utf-8 -*-
"""
DORA Metrics Calculator
=======================
Computes the four DORA metrics from git history and (optionally) GitHub Issues.

Metrics computed:
  1. Deployment Frequency   -- how often you tag a release (v* tags)
  2. Lead Time for Changes  -- avg time from first commit in a batch to its deploy tag
  3. Change Failure Rate    -- % of deploys followed by an 'incident' issue (needs GitHub token)
  4. MTTR                   -- avg time to close 'incident'-labeled issues (needs GitHub token)

Usage:
  # Local git only (metrics 1 + 2):
  python dora_metrics.py

  # With GitHub API (all 4 metrics):
  GITHUB_TOKEN=ghp_xxx python dora_metrics.py

Requirements: standard library only (no pip installs needed)
"""

import subprocess
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Optional


# ── Config ─────────────────────────────────────────────────────────────────

REPO_PATH = os.path.dirname(os.path.abspath(__file__))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")


def get_github_repo():
    """Auto-detect owner/repo from git remote."""
    try:
        remote = run_git(["remote", "get-url", "origin"]).strip()
        if "github.com" in remote:
            remote = remote.replace("git@github.com:", "").replace("https://github.com/", "")
            return remote.removesuffix(".git")
    except Exception:
        pass
    return None


# ── Git helpers ─────────────────────────────────────────────────────────────

def run_git(args):
    result = subprocess.run(
        ["git"] + args,
        cwd=REPO_PATH,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def parse_git_date(date_str):
    """
    Parse git date strings. git outputs formats like:
      '2026-02-16 23:20:54 +0100'  (creatordate:iso)
      '2026-02-16T23:20:54+01:00'  (--format=%ai strict)
    """
    date_str = date_str.strip()
    parts = date_str.split()
    if len(parts) == 3:
        # "2026-02-07 21:12:39 +0200"
        date_str = parts[0] + "T" + parts[1] + parts[2]
    elif len(parts) == 2 and (parts[1].startswith("+") or parts[1].startswith("-")):
        # "2026-02-07T21:12:39 +0200"
        date_str = parts[0] + parts[1]

    # Ensure timezone has colon: +0200 -> +02:00
    if len(date_str) >= 22 and date_str[-5] in ("+", "-") and ":" not in date_str[-5:]:
        date_str = date_str[:-2] + ":" + date_str[-2:]

    return datetime.fromisoformat(date_str)


def get_tags():
    """Return list of {name, sha, date} for all v* tags, oldest first, deduplicated."""
    raw = run_git([
        "tag", "-l", "v*",
        "--sort=creatordate",
        "--format=%(refname:short)|%(objectname:short)|%(*objectname:short)|%(creatordate:iso)"
    ])
    tags = []
    seen_commits = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        name = parts[0]
        sha_short = parts[2] if parts[2] else parts[1]
        date_str = parts[3]
        try:
            full_sha = run_git(["rev-parse", sha_short])
        except Exception:
            full_sha = sha_short
        if full_sha in seen_commits:
            continue  # skip duplicate tags on the same commit (e.g. v1.1 and v1.1.0)
        seen_commits.add(full_sha)
        tags.append({
            "name": name,
            "sha": full_sha,
            "date": parse_git_date(date_str),
        })
    return tags


def get_commits_between(from_sha, to_sha):
    """Return commits in (from_sha, to_sha], oldest first."""
    rev_range = f"{from_sha}..{to_sha}" if from_sha else to_sha
    raw = run_git(["log", rev_range, "--format=%H|%ai", "--reverse"])
    commits = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, date_str = line.split("|", 1)
        commits.append({"sha": sha, "date": parse_git_date(date_str)})
    return commits


def get_unreleased_commits(last_tag_sha):
    """Commits after the last tag (not yet deployed)."""
    raw = run_git(["log", f"{last_tag_sha}..HEAD", "--format=%H|%ai", "--reverse"])
    commits = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, date_str = line.split("|", 1)
        commits.append({"sha": sha, "date": parse_git_date(date_str)})
    return commits


# ── GitHub API helper ────────────────────────────────────────────────────────

def github_get(repo, path):
    url = f"https://api.github.com/repos/{repo}{path}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  [GitHub API] {e.code} on {path}")
        return []
    except Exception as e:
        print(f"  [GitHub API] error: {e}")
        return []


def get_incident_issues(repo):
    """Issues labeled 'incident' -- used for CFR and MTTR."""
    issues = github_get(repo, "/issues?labels=incident&state=all&per_page=100")
    if not isinstance(issues, list):
        return []
    result = []
    for issue in issues:
        created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
        closed = None
        if issue.get("closed_at"):
            closed = datetime.fromisoformat(issue["closed_at"].replace("Z", "+00:00"))
        result.append({"created": created, "closed": closed})
    return result


# ── Formatting helpers ───────────────────────────────────────────────────────

def fmt_duration(td):
    total_hours = td.total_seconds() / 3600
    if total_hours < 1:
        return f"{int(td.total_seconds() / 60)} min"
    if total_hours < 24:
        return f"{total_hours:.1f} hrs"
    return f"{td.days}d {int(total_hours % 24)}h"


def dora_tier(metric, value):
    """
    Returns Elite/High/Medium/Low based on 2023 DORA benchmarks.
    Value units: deploys_per_day, hours, percent, hours for each metric.
    """
    if metric == "deployment_frequency":  # deploys per day; higher is better
        if value >= 1.0:     return "Elite"
        if value >= 1/7:     return "High"
        if value >= 1/30:    return "Medium"
        return "Low"
    if metric == "lead_time":  # hours; lower is better
        if value <= 24:          return "Elite"
        if value <= 7 * 24:      return "High"
        if value <= 30 * 24:     return "Medium"
        return "Low"
    if metric == "cfr":  # percent; lower is better
        if value <= 5:   return "Elite"
        if value <= 10:  return "High"
        if value <= 15:  return "Medium"
        return "Low"
    if metric == "mttr":  # hours; lower is better
        if value <= 1:       return "Elite"
        if value <= 24:      return "High"
        if value <= 7 * 24:  return "Medium"
        return "Low"
    return "Unknown"


# ── Main ─────────────────────────────────────────────────────────────────────

def compute_metrics():
    github_repo = get_github_repo()

    print("")
    print("DORA Metrics Report")
    print(f"  Repo : {github_repo or REPO_PATH}")
    print(f"  Date : {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 52)

    # 1 & 2: Deployment Frequency + Lead Time (from git tags)
    tags = get_tags()

    if not tags:
        print("")
        print("No v* tags found. Tag a release to start tracking.")
        print("  git tag -a v1.0 -m 'first release' && git push --tags")
        return

    print(f"\n  Found {len(tags)} unique deploy(s): {', '.join(t['name'] for t in tags)}")
    print("")

    first_commit_date = parse_git_date(
        run_git(["log", "--reverse", "--format=%ai", "--max-parents=0", "HEAD"]).splitlines()[0]
    )
    now = datetime.now(timezone.utc)
    total_days = max((now - first_commit_date).days, 1)

    # --- Deployment Frequency ---
    deploy_count = len(tags)
    deploys_per_day = deploy_count / total_days
    deploys_per_week = deploys_per_day * 7
    df_tier = dora_tier("deployment_frequency", deploys_per_day)

    print("1. Deployment Frequency")
    print(f"   {deploy_count} deploys over {total_days} days")
    print(f"   -> {deploys_per_week:.2f} per week  [{df_tier}]")
    print("")

    # --- Lead Time for Changes ---
    lead_times = []
    batches = []
    prev_sha = None
    for tag in tags:
        commits = get_commits_between(prev_sha, tag["sha"])
        if commits:
            first_commit = commits[0]["date"]
            lt = tag["date"] - first_commit
            lead_times.append(lt)
            batches.append({
                "tag": tag["name"],
                "commits": len(commits),
                "lead_time": lt,
            })
        prev_sha = tag["sha"]

    unreleased = get_unreleased_commits(tags[-1]["sha"])

    print("2. Lead Time for Changes")
    for b in batches:
        print(f"   {b['tag']:8s}  {b['commits']:2d} commit(s)  ->  {fmt_duration(b['lead_time'])}")
    if unreleased:
        current_lt = now - unreleased[0]["date"]
        print(f"   (HEAD)     {len(unreleased):2d} commit(s)  ->  {fmt_duration(current_lt)} and counting (not yet deployed)")

    if lead_times:
        avg_lt = sum(lead_times, timedelta()) / len(lead_times)
        lt_hours = avg_lt.total_seconds() / 3600
        lt_tier = dora_tier("lead_time", lt_hours)
        print(f"   Avg: {fmt_duration(avg_lt)}  [{lt_tier}]")
    print("")

    # 3 & 4: CFR + MTTR (GitHub Issues labeled 'incident')
    print("3. Change Failure Rate")
    print("4. Mean Time to Restore (MTTR)")
    print("")

    if not github_repo:
        print("   Could not detect GitHub repo from git remote.")
        print("   Push to GitHub and re-run.")
    elif not GITHUB_TOKEN:
        print("   Set GITHUB_TOKEN to fetch incident data:")
        print("     GITHUB_TOKEN=ghp_xxx python dora_metrics.py")
        print("   Create Issues labeled 'incident' when a deploy causes a prod problem.")
    else:
        incidents = get_incident_issues(github_repo)
        if not incidents:
            print("   No 'incident'-labeled issues found yet.")
            print("   When a deploy breaks prod: open a GitHub Issue, add the 'incident' label,")
            print("   then close it when resolved. Re-run this script to see CFR and MTTR.")
        else:
            cfr = (len(incidents) / deploy_count) * 100
            cfr_tier = dora_tier("cfr", cfr)
            print(f"   CFR: {len(incidents)} incident(s) / {deploy_count} deploy(s) = {cfr:.1f}%  [{cfr_tier}]")
            print("")
            resolved = [i for i in incidents if i["closed"]]
            if resolved:
                mttr_times = [i["closed"] - i["created"] for i in resolved]
                avg_mttr = sum(mttr_times, timedelta()) / len(mttr_times)
                mttr_hours = avg_mttr.total_seconds() / 3600
                mttr_tier = dora_tier("mttr", mttr_hours)
                print(f"   MTTR: avg {fmt_duration(avg_mttr)} to resolve  [{mttr_tier}]")
            else:
                print("   MTTR: no resolved incidents yet.")

    print("")
    print("=" * 52)
    print("  Tiers (DORA 2023):  Elite > High > Medium > Low")
    print("")
    print("  Next actions:")
    print("  - Ship more often: tag releases as v1.2, v1.3, ...")
    print("  - Track incidents: label GitHub Issues with 'incident'")
    print("  - Run with GITHUB_TOKEN to see CFR and MTTR")
    print("=" * 52)
    print("")


if __name__ == "__main__":
    compute_metrics()
