# gitlab-mr-status

Claude Code skill + stdlib Python script that reports the current state of a list of GitLab merge requests in one read-only run.

- Per MR: state, draft, pipeline, approvals, `detailed_merge_status`, conflicts, unresolved review threads, last update, and a one-line verdict (merged / ready / blocked and why).
- `--threads` prints every unresolved thread with author, file:line and an excerpt, so you can see what reviewers are asking for.
- `--json` for further processing.
- Works on gitlab.com and self-hosted GitLab; the host is read from each MR link, links may span hosts and projects.
- Auth via your existing `glab` login, or `GITLAB_TOKEN` for direct REST calls.

```bash
python skills/gitlab-mr-status/scripts/mr_status.py <MR-URL> ...
python skills/gitlab-mr-status/scripts/mr_status.py --threads --file links.txt
```

See [skills/gitlab-mr-status/SKILL.md](skills/gitlab-mr-status/SKILL.md) for how to read the table. To act on the result (approve, rebase, merge) see the sibling `gitlab-mass-merge` plugin.
