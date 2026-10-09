---
name: gitlab-mass-merge
description: Use when asked to mass approve, bulk merge, or approve-and-merge many GitLab merge requests at once from a list of MR links (e.g. a batch of AI-agent MRs across repos), or to rebase and merge a pile of MRs on any GitLab instance (self-hosted or gitlab.com).
---

# GitLab Mass Approve + Merge

One script, stdlib Python, authenticates through your existing `glab` login (or a `GITLAB_TOKEN` env var). The GitLab host is taken from the links, so it works on any instance. Handles links across many projects in one run.

The script lives at `scripts/mass_merge.py` under this skill's base directory (the path shown when the skill loads). Always call it with that absolute path.

```bash
python <skill-dir>/scripts/mass_merge.py --dry-run <MR-URL> [<MR-URL>...]
python <skill-dir>/scripts/mass_merge.py --yes --rebase <MR-URL> ...
python <skill-dir>/scripts/mass_merge.py --yes --file links.txt   # one URL per line
```

## Workflow

1. More than ~5 links: put them in a scratchpad file (`mr-links-<topic>.txt`) and use `--file`. Fewer: pass inline.
2. **Always run `--dry-run` first** and show the user the summary. It polls mergeability (read-only) and reports exactly which MRs would be approved/rebased/merged and why the rest would not.
3. Run for real with `--yes` (the script otherwise prompts on stdin, which hangs in a tool call). Add `--rebase` when the dry run showed `need_rebase`. Drafts are skipped by default: when the dry run shows draft MRs, ask the user whether to add `--undraft` (it rewrites the title) unless they already said to merge drafts.
4. Report the SUMMARY block: `OK` rows are merged or auto-merge-armed; `FAIL` rows carry the reason. Exit code 1 means at least one needs attention.

## Flags

| Flag | Effect |
|------|--------|
| `-n`, `--dry-run` | Report only, no writes |
| `-y`, `--yes` | Skip confirmation prompt (required in tool calls) |
| `--rebase` | Rebase `need_rebase` MRs, wait, then merge |
| `--undraft` | Strip `Draft:` and merge drafts too |
| `--no-approve` / `--no-merge` | Only merge / only approve |
| `--no-auto-merge` | Don't arm "merge when pipeline succeeds" on running pipelines |
| `--squash`, `--no-squash`, `--remove-source-branch`, `--keep-source-branch` | Override project defaults (omit to keep them) |

## Reading the results

| Result | Meaning / what to do |
|--------|----------------------|
| `merged` | Done |
| `auto-merge set` | Pipeline still running; GitLab merges on green |
| `approve:forbidden` in actions | You are the author or lack rights; approval must come from someone else. Merge is still attempted |
| `status=not_approved` | Needs more approvals (approval rules / CODEOWNERS) — ask the right approver |
| `status=conflict` | Needs manual conflict resolution; `--rebase` cannot fix it |
| `status=need_rebase` | Re-run with `--rebase` |
| `status=ci_must_pass` + pipeline failed/None | Fix or retry the pipeline first |
| `status=unchecked` after polling | GitLab never evaluated mergeability (common on archived projects) — check in the UI |
| `skipped: state=merged` | Already merged, counted as OK |

## Gotchas

- `glab auth status` may exit with an ERROR banner for an unrelated host entry without a token. Only the host in your links needs a working login.
- Dry-run `approve:likely-forbidden (you are the author)` means the real run will get 401 on approve (prevent-author-approval); the MR then needs another approver before merge succeeds.
- Dry-run `merge (status=not_approved now; ...)` is an assumption: if CODEOWNERS needs more approvers than you, the real run ends with `status=not_approved`.
- Approve + merge are separate API calls; GitLab's "prevent author approval" makes self-approval return 401, which the script tolerates.
- Links from other hosts are rejected in one run; the script refuses mixed-host lists.
- `GITLAB_TOKEN` switches to direct REST with a `PRIVATE-TOKEN` header — use it where `glab` is missing or logged in to a different host.
