---
name: gitlab-mr-status
description: Use when given one or more GitLab merge request links (or a ticket that lists them) and asked to go through them, check them, or report their current state, status, progress or "where are we", or narrower questions such as is it merged yet, what is blocking it, which ones are still open or draft, who approved, is the pipeline green, are there open review comments. Covers lists of MRs across many repos and batches of AI-agent MRs, on any GitLab instance. Read-only reporting; not for reviewing, merging or fixing an MR.
---

# GitLab MR Status

One read-only script, stdlib Python, authenticates through your existing `glab` login (or a `GITLAB_TOKEN` env var). The host is taken from each link, so it works on any instance and links may span hosts and projects. One call replaces a `glab api` loop per MR for metadata, approvals and paginated discussions.

The script lives at `scripts/mr_status.py` under this skill's base directory (the path shown when the skill loads). Call it by absolute path, or `cd` into the skill directory first.

```bash
python <skill-dir>/scripts/mr_status.py <MR-URL> [<MR-URL>...]
python <skill-dir>/scripts/mr_status.py --threads --file links.txt   # one URL per line
python <skill-dir>/scripts/mr_status.py --json ... > status.json
```

## Workflow

1. Collect the links. More than ~5: write them to a scratch file (`mr-links-<topic>.txt`) and use `--file`. A numeric range in one project can be generated in a shell loop. Lines that are not MR links are skipped with a warning, duplicates are dropped.
2. Run the script. Add `--threads` when the user wants to know what reviewers are asking for; it prints every unresolved thread with author (`[bot]` marked when GitLab flags the account or the username is a service account), file:line, reply count and the first 160 characters. The full note text is in `--json` output under `threads[].body`.
3. Report from the table. Group by the VERDICT column: merged, ready, blocked (and why), closed. Quote the VERDICT text rather than re-deriving it from the raw columns. Mention ERROR rows (usually a wrong link or no access).
4. Nothing to do afterwards: the script never writes. To act on the result (approve, merge, rebase) use a separate tool such as `gitlab-mass-merge`.

## Reading the table

| Column | Meaning |
|--------|---------|
| STATE | `open`, `draft`, `merged`, `closed`, `locked` |
| PIPELINE | head pipeline status; `-` means the project has no pipeline for this MR |
| APPROVALS | `given/required (who)`; `n/a` when the approvals API is unavailable |
| MERGE_STATUS | GitLab `detailed_merge_status`, e.g. `mergeable`, `not_approved`, `need_rebase`, `conflict`, `draft_status`, `discussions_not_resolved`, `ci_must_pass`, `blocked_status`; `not_open` for merged/closed |
| UNRES | count of unresolved resolvable threads, including general (non-diff) notes |
| UPDATED | age of the MR's `updated_at`; bot comments and pipeline events bump it, so a quiet MR can still read "2m ago" |
| VERDICT | one line: `merged <age> by <user>`, `READY: mergeable (...)`, `open, blocked: <reasons> (<notes>)`, `closed <age>` |

Blockers (prevent merge): draft, merge conflicts (`has_conflicts` or status `conflict`), needs rebase, pipeline failed/canceled/running, unresolved threads that GitLab counts as blocking, needs approval. Notes (in parentheses, do not prevent merge): no pipeline, unresolved threads GitLab reports as non-blocking (`blocking_discussions_resolved` is true), auto-merge armed.

## Gotchas

- Runtime is roughly 1.5 s per MR through `glab` (three API calls each); 60 MRs take about 90 s. Set a tool timeout accordingly, do not split into parallel runs. With `GITLAB_TOKEN` set (e.g. the token `glab auth status --show-token` prints) the direct REST path is about 3x faster.
- `blocking_discussions_resolved` can be true while threads show unresolved (threads opened by bots or on outdated diffs). The script reports both: the count stays in UNRES, the verdict marks them non-blocking.
- `APPROVALS` can read `2/1`: more approvals than required. `0/0` with `mergeable` means the project requires none.
- `glab auth status` may print an ERROR for an unrelated host without a token. Only the host in the links needs a working login.
- `GITLAB_TOKEN` switches to direct REST with a `PRIVATE-TOKEN` header; use it where `glab` is missing or logged in to a different host.
- Output is forced to UTF-8, so review-thread excerpts with non-ASCII text do not crash on Windows consoles.
- Exit code 1 means at least one MR could not be fetched, not that an MR is blocked.
