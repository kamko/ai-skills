#!/usr/bin/env python3
"""Report the current state of GitLab merge requests from a list of MR links.

Read-only. Works with any GitLab instance: the host is taken from each MR link,
links may span several hosts and projects. Transport: `glab api` (uses your glab
login) unless GITLAB_TOKEN is set, in which case direct REST calls with a
PRIVATE-TOKEN header are used. Stdlib only.

Usage:
  python mr_status.py [flags] <MR-URL> [<MR-URL> ...]
  python mr_status.py [flags] --file links.txt
  cat links.txt | python mr_status.py [flags] -

Per MR: state, draft, pipeline, approvals, mergeability, conflicts, unresolved
review threads, last update, author, and a one-line "verdict".
--threads prints every unresolved thread (author, file, first note excerpt).
--json emits the same data as JSON for further processing.
Exit code 1 only when an MR could not be fetched (not for MRs that are blocked).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

MR_RE = re.compile(r"^https?://(?P<host>[^/]+)/(?P<path>.+?)/-/merge_requests/(?P<iid>\d+)")
PIPELINE_ACTIVE = {"running", "pending", "created", "waiting_for_resource", "preparing", "scheduled"}
# detailed_merge_status -> short human reason (values from the GitLab MR API docs)
STATUS_REASON = {
    "mergeable": "ready to merge",
    "not_approved": "needs approval",
    "need_rebase": "needs rebase",
    "conflict": "merge conflicts",
    "ci_must_pass": "pipeline must pass",
    "ci_still_running": "pipeline running",
    "draft_status": "draft",
    "discussions_not_resolved": "unresolved threads",
    "blocked_status": "blocked by another MR",
    "broken_status": "broken (missing branch?)",
    "not_open": "not open",
    "policies_denied": "policy denied",
    "external_status_checks": "external checks pending",
    "jira_association_missing": "missing issue link",
    "requested_changes": "changes requested",
    "unchecked": "mergeability not evaluated yet",
    "checking": "mergeability being checked",
    "preparing": "mergeability being checked",
    "merge_time": "merge scheduled",
    "merge_request_blocked": "blocked",
}


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class Api:
    def __init__(self, host: str, token: str | None):
        self.host = host
        self.token = token
        self.glab = shutil.which("glab")
        if not token and not self.glab:
            sys.exit("Neither GITLAB_TOKEN set nor `glab` found on PATH.")

    def get(self, path: str, params: dict | None = None):
        if params:
            path += ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
        return self._urllib(path) if self.token else self._glab(path)

    def get_all(self, path: str, params: dict | None = None) -> list:
        """Follow page= pagination until a short page comes back."""
        out, page = [], 1
        while True:
            chunk = self.get(path, {**(params or {}), "per_page": 100, "page": page})
            out += chunk
            if len(chunk) < 100:
                return out
            page += 1

    def _urllib(self, path):
        req = urllib.request.Request(f"https://{self.host}/api/v4/{path}", headers={"PRIVATE-TOKEN": self.token})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode(errors="replace")) from None

    def _glab(self, path):
        p = subprocess.run([self.glab, "api", "--hostname", self.host, "-X", "GET", path],
                           capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0:
            m = re.search(r"\(HTTP (\d+)\)", p.stderr)
            raise ApiError(int(m.group(1)) if m else 0, (p.stdout or p.stderr).strip())
        out = p.stdout.strip()
        return json.loads(out) if out else {}


@dataclass
class Target:
    url: str
    host: str
    project: str  # URL-encoded path
    iid: int
    info: dict = field(default_factory=dict)
    error: str = ""

    @property
    def base(self) -> str:
        return f"projects/{self.project}/merge_requests/{self.iid}"

    @property
    def label(self) -> str:
        return f"{urllib.parse.unquote(self.project)}!{self.iid}"


def parse_links(raw: list[str]) -> list[Target]:
    out, seen = [], set()
    for line in raw:
        line = line.strip().rstrip("/")
        if not line or line.startswith("#"):
            continue
        m = MR_RE.match(line)
        if not m:
            print(f"!! not an MR link, skipping: {line}", file=sys.stderr)
            continue
        key = (m["host"], m["path"], int(m["iid"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(Target(url=m.group(0), host=m["host"], project=urllib.parse.quote(m["path"], safe=""), iid=int(m["iid"])))
    return out


def age(iso: str | None) -> str:
    if not iso:
        return "-"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    s = int((datetime.now(timezone.utc) - dt).total_seconds())
    for unit, div in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= div:
            return f"{s // div}{unit} ago"
    return "just now"


def users(lst: list | None) -> list[str]:
    return [u.get("username", "?") for u in (lst or [])]


def collect(api: Api, t: Target, want_threads: bool) -> dict:
    mr = api.get(t.base)
    pipe = mr.get("head_pipeline") or mr.get("pipeline") or {}
    info = {
        "url": t.url,
        "project": urllib.parse.unquote(t.project),
        "iid": t.iid,
        "title": mr.get("title", ""),
        "state": mr.get("state"),  # opened / merged / closed / locked
        "draft": bool(mr.get("draft") or mr.get("work_in_progress")),
        "author": (mr.get("author") or {}).get("username"),
        "assignees": users(mr.get("assignees")),
        "reviewers": users(mr.get("reviewers")),
        "source_branch": mr.get("source_branch"),
        "target_branch": mr.get("target_branch"),
        "merge_status": mr.get("detailed_merge_status") or mr.get("merge_status"),
        "has_conflicts": bool(mr.get("has_conflicts")),
        "pipeline": pipe.get("status"),
        "pipeline_url": pipe.get("web_url"),
        "updated_at": mr.get("updated_at"),
        "merged_at": mr.get("merged_at"),
        "merged_by": ((mr.get("merge_user") or mr.get("merged_by")) or {}).get("username"),
        "closed_at": mr.get("closed_at"),
        "auto_merge": bool(mr.get("merge_when_pipeline_succeeds") or mr.get("auto_merge_enabled")),
        "approved": None,
        "approved_by": [],
        "approvals_required": None,
        "approvals_left": None,
        "blocking_discussions_resolved": mr.get("blocking_discussions_resolved"),
        "unresolved": 0,
        "threads": [],
    }

    try:
        ap = api.get(f"{t.base}/approvals")
        info["approved"] = ap.get("approved")
        info["approved_by"] = [x.get("user", {}).get("username", "?") for x in ap.get("approved_by", [])]
        info["approvals_required"] = ap.get("approvals_required")
        info["approvals_left"] = ap.get("approvals_left")
    except ApiError as e:
        if e.status not in (401, 403, 404):
            raise

    for d in api.get_all(f"{t.base}/discussions"):
        notes = [n for n in d.get("notes", []) if not n.get("system")]
        if not notes or not notes[0].get("resolvable"):
            continue
        if all(n.get("resolved") for n in notes if n.get("resolvable")):
            continue
        info["unresolved"] += 1
        if want_threads:
            first = notes[0]
            pos = first.get("position") or {}
            body = re.sub(r"\s+", " ", first.get("body", "")).strip()
            author = first.get("author") or {}
            info["threads"].append({
                "author": author.get("username"),
                "bot": bool(author.get("bot")) or str(author.get("username", "")).startswith(("service_account", "project_", "group_")),
                "file": pos.get("new_path") or pos.get("old_path"),
                "line": pos.get("new_line") or pos.get("old_line"),
                "replies": len(notes) - 1,
                "last_reply_by": (notes[-1].get("author") or {}).get("username") if len(notes) > 1 else None,
                "excerpt": body[:160],
                "body": first.get("body", ""),
                "created_at": first.get("created_at"),
            })

    info["verdict"] = verdict(info)
    return info


def verdict(i: dict) -> str:
    if i["state"] == "merged":
        who = f" by {i['merged_by']}" if i["merged_by"] else ""
        return f"merged {age(i['merged_at'])}{who}"
    if i["state"] == "closed":
        return f"closed {age(i['closed_at'])} (not merged)"
    if i["state"] == "locked":
        return "locked (merge in progress)"
    blockers, notes = [], []
    if i["draft"]:
        blockers.append("draft")
    if i["has_conflicts"] or i["merge_status"] == "conflict":
        blockers.append("merge conflicts")
    elif i["merge_status"] == "need_rebase":
        blockers.append("needs rebase")
    if i["pipeline"] in ("failed", "canceled"):
        blockers.append(f"pipeline {i['pipeline']}")
    elif i["pipeline"] in PIPELINE_ACTIVE:
        blockers.append("pipeline running")
    elif i["pipeline"] is None:
        notes.append("no pipeline")
    if i["unresolved"]:
        plural = "s" if i["unresolved"] > 1 else ""
        if i["blocking_discussions_resolved"]:
            notes.append(f"{i['unresolved']} unresolved thread{plural}, non-blocking")
        else:
            blockers.append(f"{i['unresolved']} unresolved thread{plural}")
    if i["approved"] is False or (i["approvals_left"] or 0) > 0:
        blockers.append("needs approval")
    if i["auto_merge"]:
        notes.append("auto-merge armed")
    tail = f" ({'; '.join(notes)})" if notes else ""
    if i["merge_status"] == "mergeable" and not blockers:
        return "READY: mergeable" + tail
    if not blockers:
        reason = STATUS_REASON.get(i["merge_status"], i["merge_status"] or "unknown")
        return f"open: {reason}" + tail
    return "open, blocked: " + ", ".join(blockers) + tail


def approvals_cell(i: dict) -> str:
    if i["approved"] is None and not i["approved_by"]:
        return "n/a"
    n = len(i["approved_by"])
    need = i["approvals_required"]
    cell = f"{n}/{need}" if need is not None else str(n)
    if i["approved_by"]:
        more = "+" if n > 3 else ""
        cell += f" ({', '.join(i['approved_by'][:3])}{more})"
    return cell


def state_cell(i: dict) -> str:
    if i["state"] == "opened":
        return "draft" if i["draft"] else "open"
    return i["state"] or "?"


def print_table(targets: list[Target]) -> None:
    rows = []
    for t in targets:
        if t.error:
            rows.append((t.label, "ERROR", "-", "-", "-", "-", "-", t.error[:80]))
            continue
        i = t.info
        rows.append((
            t.label,
            state_cell(i),
            i["pipeline"] or "-",
            approvals_cell(i),
            i["merge_status"] or "-",
            str(i["unresolved"]),
            age(i["updated_at"]),
            i["verdict"],
        ))
    head = ("MR", "STATE", "PIPELINE", "APPROVALS", "MERGE_STATUS", "UNRES", "UPDATED", "VERDICT")
    widths = [max(len(str(r[c])) for r in [head, *rows]) for c in range(len(head))]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*head))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))

    for t in targets:
        if t.error or not t.info["threads"]:
            continue
        print(f"\n-- unresolved threads on {t.label} ({t.info['title'][:60]})")
        for th in t.info["threads"]:
            loc = f" @ {th['file']}:{th['line']}" if th["file"] else ""
            reply = f", last reply {th['last_reply_by']}" if th["last_reply_by"] else ""
            who = f"{th['author']} [bot]" if th["bot"] else th["author"]
            print(f"  * {who}{loc} ({age(th['created_at'])}, {th['replies']} replies{reply})")
            print(f"    {th['excerpt']}")

    ok = [t for t in targets if not t.error]
    merged = sum(1 for t in ok if t.info["state"] == "merged")
    closed = sum(1 for t in ok if t.info["state"] == "closed")
    ready = sum(1 for t in ok if t.info["verdict"].startswith("READY"))
    blocked = len(ok) - merged - closed - ready
    errors = f", {len(targets) - len(ok)} errors" if len(ok) != len(targets) else ""
    print(f"\n{len(targets)} MR(s): {merged} merged, {ready} open+ready, {blocked} open+blocked, {closed} closed{errors}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("links", nargs="*", help="MR URLs, or '-' to read from stdin")
    ap.add_argument("--file", "-f", help="file with one MR URL per line")
    ap.add_argument("--threads", "-t", action="store_true", help="print each unresolved review thread")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    a = ap.parse_args()

    raw: list[str] = []
    if a.file:
        raw += open(a.file, encoding="utf-8").read().splitlines()
    if "-" in a.links or (not a.links and not a.file):
        raw += sys.stdin.read().splitlines()
    raw += [l for l in a.links if l != "-"]
    targets = parse_links(raw)
    if not targets:
        ap.error("no MR links given")

    token = os.environ.get("GITLAB_TOKEN")
    apis: dict[str, Api] = {}
    for t in targets:
        api = apis.setdefault(t.host, Api(t.host, token))
        try:
            t.info = collect(api, t, a.threads or a.json)
        except ApiError as e:
            t.error = f"HTTP {e.status}: {e.body[:120]}"
        except Exception as e:  # noqa: BLE001
            t.error = f"error: {e}"

    if a.json:
        print(json.dumps([t.info if not t.error else {"url": t.url, "error": t.error} for t in targets], indent=2))
    else:
        print_table(targets)
    return 1 if any(t.error for t in targets) else 0


if __name__ == "__main__":
    sys.exit(main())
