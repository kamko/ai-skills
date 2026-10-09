#!/usr/bin/env python3
"""Mass approve + merge GitLab merge requests from a list of MR links.

Works with any GitLab instance: the host is taken from each MR link.
Transport: `glab api` (uses your glab login) unless GITLAB_TOKEN is set, in which
case direct REST calls with a PRIVATE-TOKEN header are used (CI, VMs without glab).
Stdlib only.

Usage:
  python mass_merge.py [flags] <MR-URL> [<MR-URL> ...]
  python mass_merge.py [flags] --file links.txt
  cat links.txt | python mass_merge.py [flags] -

Run with --dry-run first. Exit code 1 if any MR failed to reach its target state.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

MR_RE = re.compile(r"^https?://(?P<host>[^/]+)/(?P<path>.+?)/-/merge_requests/(?P<iid>\d+)")
MERGEABLE = "mergeable"
WAIT_FOR_CI = {"ci_still_running", "ci_must_pass"}
TRANSIENT = {"unchecked", "checking", "preparing"}
PIPELINE_ACTIVE = {"running", "pending", "created", "waiting_for_resource", "preparing"}


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

    def call(self, method: str, path: str, params: dict | None = None):
        params = params or {}
        if self.token:
            return self._urllib(method, path, params)
        return self._glab(method, path, params)

    def _urllib(self, method, path, params):
        url = f"https://{self.host}/api/v4/{path}"
        data = None
        headers = {"PRIVATE-TOKEN": self.token}
        if method == "GET" and params:
            url += "?" + urllib.parse.urlencode(params)
        elif params:
            data = json.dumps(params).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode(errors="replace")) from None

    def _glab(self, method, path, params):
        cmd = [self.glab, "api", "--hostname", self.host, "-X", method]
        if method == "GET" and params:
            path += "?" + urllib.parse.urlencode(params)
        else:
            for k, v in params.items():
                if isinstance(v, bool):
                    cmd += ["-F", f"{k}={str(v).lower()}"]
                elif isinstance(v, int):
                    cmd += ["-F", f"{k}={v}"]
                else:
                    cmd += ["-f", f"{k}={v}"]
        cmd.append(path)
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0:
            m = re.search(r"\(HTTP (\d+)\)", p.stderr)
            status = int(m.group(1)) if m else 0
            raise ApiError(status, (p.stdout or p.stderr).strip())
        out = p.stdout.strip()
        return json.loads(out) if out else {}


@dataclass
class Target:
    url: str
    host: str
    project: str  # URL-encoded path
    iid: int
    actions: list[str] = field(default_factory=list)
    result: str = ""
    ok: bool = False

    @property
    def base(self) -> str:
        return f"projects/{self.project}/merge_requests/{self.iid}"


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
        out.append(Target(url=line, host=m["host"], project=urllib.parse.quote(m["path"], safe=""), iid=int(m["iid"])))
    return out


def wait_status(api: Api, t: Target, tries: int = 10, delay: float = 2.0) -> dict:
    """Re-fetch until detailed_merge_status leaves the transient states."""
    mr = api.call("GET", t.base, {"include_rebase_in_progress": "true"})
    for _ in range(tries):
        if mr.get("detailed_merge_status") not in TRANSIENT and not mr.get("rebase_in_progress"):
            break
        time.sleep(delay)
        mr = api.call("GET", t.base, {"include_rebase_in_progress": "true"})
    return mr


def process(api: Api, me_id: int, t: Target, a: argparse.Namespace) -> None:
    mr = api.call("GET", t.base)
    title = mr.get("title", "")
    print(f"\n== !{t.iid} {urllib.parse.unquote(t.project)} -- {title[:70]}")

    if mr["state"] != "opened":
        t.result, t.ok = f"skipped: state={mr['state']}", mr["state"] == "merged"
        return

    # 1. undraft
    if mr.get("draft"):
        if a.undraft:
            t.actions.append("undraft")
            if not a.dry_run:
                api.call("PUT", t.base, {"title": re.sub(r"^(\[?Draft\]?:?|\(?WIP\)?:?)\s*", "", title, flags=re.I)})
        else:
            t.result = "skipped: draft (use --undraft)"
            return

    # 2. approve
    if not a.no_approve:
        approvals = api.call("GET", f"{t.base}/approvals")
        already = any(x.get("user", {}).get("id") == me_id for x in approvals.get("approved_by", []))
        if already:
            t.actions.append("approve:already")
        elif a.dry_run and (mr.get("author") or {}).get("id") == me_id:
            t.actions.append("approve:likely-forbidden (you are the author)")
        else:
            t.actions.append("approve")
            if not a.dry_run:
                try:
                    api.call("POST", f"{t.base}/approve")
                except ApiError as e:
                    if e.status in (401, 403):
                        t.actions[-1] = "approve:forbidden"
                    else:
                        raise

    if a.no_merge:
        t.result, t.ok = "approve-only done", True
        return

    # 3. check mergeability, rebase if needed
    mr = wait_status(api, t)  # read-only polling, safe in dry-run too
    status = mr.get("detailed_merge_status")
    if status == "need_rebase" and a.rebase:
        t.actions.append("rebase")
        if not a.dry_run:
            api.call("PUT", f"{t.base}/rebase", {"skip_ci": False})
            time.sleep(3)
            mr = wait_status(api, t, tries=30, delay=3)
            if mr.get("merge_error"):
                t.result = f"rebase failed: {mr['merge_error'][:120]}"
                return
            status = mr.get("detailed_merge_status")

    # 4. merge
    params = {}
    if a.squash is not None:
        params["squash"] = a.squash
    if a.remove_source_branch is not None:
        params["should_remove_source_branch"] = a.remove_source_branch

    pipeline = (mr.get("head_pipeline") or mr.get("pipeline") or {}).get("status")
    if status == MERGEABLE:
        t.actions.append("merge")
    elif status in WAIT_FOR_CI and pipeline in PIPELINE_ACTIVE and not a.no_auto_merge:
        t.actions.append("merge:when-pipeline-succeeds")
        params["merge_when_pipeline_succeeds"] = True
    elif a.dry_run and status == "not_approved":
        t.actions.append("merge (status=not_approved now; assumes your approval is the last one needed)")
    elif a.dry_run and status == "need_rebase" and a.rebase:
        t.actions.append("merge (status=need_rebase now; after --rebase)")
    else:
        t.result = f"not merged: status={status} pipeline={pipeline}"
        return

    if a.dry_run:
        t.result, t.ok = "dry-run", True
        return
    try:
        res = api.call("PUT", f"{t.base}/merge", params)
    except ApiError as e:
        if e.status in (405, 406, 422):
            mr = api.call("GET", t.base)
            t.result = f"merge refused ({e.status}): status={mr.get('detailed_merge_status')}"
            return
        raise
    if res.get("state") == "merged":
        t.result, t.ok = "merged", True
    elif res.get("merge_when_pipeline_succeeds") or res.get("auto_merge_enabled"):
        t.result, t.ok = "auto-merge set (merges when pipeline passes)", True
    else:
        t.result = f"merge call ok but state={res.get('state')} status={res.get('detailed_merge_status')}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("links", nargs="*", help="MR URLs, or '-' to read from stdin")
    ap.add_argument("--file", "-f", help="file with one MR URL per line")
    ap.add_argument("--dry-run", "-n", action="store_true", help="only report what would be done")
    ap.add_argument("--no-approve", action="store_true", help="skip the approve step")
    ap.add_argument("--no-merge", action="store_true", help="approve only, do not merge")
    ap.add_argument("--undraft", action="store_true", help="mark Draft MRs as ready before merging")
    ap.add_argument("--rebase", action="store_true", help="rebase MRs that need it, then merge")
    ap.add_argument("--no-auto-merge", action="store_true", help="do not set 'merge when pipeline succeeds' on running pipelines")
    ap.add_argument("--squash", dest="squash", action="store_true", default=None)
    ap.add_argument("--no-squash", dest="squash", action="store_false")
    ap.add_argument("--remove-source-branch", dest="remove_source_branch", action="store_true", default=None)
    ap.add_argument("--keep-source-branch", dest="remove_source_branch", action="store_false")
    ap.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
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

    hosts = {t.host for t in targets}
    if len(hosts) > 1:
        sys.exit(f"links span multiple hosts: {hosts}")
    host = hosts.pop()
    api = Api(host, os.environ.get("GITLAB_TOKEN"))
    me = api.call("GET", "user")
    mode = "DRY RUN" if a.dry_run else ("APPROVE ONLY" if a.no_merge else "APPROVE + MERGE")
    print(f"{mode} as {me['username']} on {host}: {len(targets)} MR(s)")
    if not a.dry_run and not a.yes:
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            return 2

    for t in targets:
        try:
            process(api, me["id"], t, a)
        except ApiError as e:
            t.result = f"error {e.status}: {e.body[:160]}"
        except Exception as e:  # noqa: BLE001
            t.result = f"error: {e}"
        print(f"   actions: {', '.join(t.actions) or '-'}\n   result : {t.result}")

    print("\n" + "=" * 72 + "\nSUMMARY")
    w = max(len(t.url) for t in targets)
    for t in targets:
        print(f"{'OK  ' if t.ok else 'FAIL'} {t.url.ljust(w)}  {t.result}")
    failed = [t for t in targets if not t.ok]
    print(f"\n{len(targets) - len(failed)} ok, {len(failed)} need attention")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
