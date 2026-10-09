# gitlab-mass-merge

Claude Code skill + stdlib Python script to approve, rebase and merge a list of GitLab merge requests in one run.

- Works on gitlab.com and self-hosted GitLab; the host is read from each MR link.
- Auth via your existing `glab` login, or `GITLAB_TOKEN` for direct REST calls.
- `--dry-run` shows exactly what would happen and why the rest would not merge.
- Arms "merge when pipeline succeeds" when CI is still running.

```bash
python skills/gitlab-mass-merge/scripts/mass_merge.py --dry-run <MR-URL> ...
python skills/gitlab-mass-merge/scripts/mass_merge.py --yes --rebase --file links.txt
```

See [skills/gitlab-mass-merge/SKILL.md](skills/gitlab-mass-merge/SKILL.md) for flags and how to read the results.
