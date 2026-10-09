# ai-skills

Personal [Claude Code](https://claude.com/claude-code) plugin marketplace. Each plugin bundles one or more skills.

## Install

```
/plugin marketplace add kamko/ai-skills
/plugin install gitlab-mass-merge@ai-skills
```

Without the plugin system, copy any `plugins/<plugin>/skills/<skill>` folder into `~/.claude/skills/`.

## Plugins

| Plugin | What it does |
|--------|--------------|
| [gitlab-mass-merge](plugins/gitlab-mass-merge) | Approve, rebase and merge a whole list of GitLab MRs in one go. Works on gitlab.com and self-hosted instances via your `glab` login or a `GITLAB_TOKEN`. |

## Layout

```
.claude-plugin/marketplace.json          # marketplace index
plugins/<plugin>/.claude-plugin/plugin.json
plugins/<plugin>/skills/<skill>/SKILL.md
plugins/<plugin>/skills/<skill>/scripts/  # bundled tools
```

## License

MIT
