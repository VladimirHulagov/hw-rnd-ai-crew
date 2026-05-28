---
name: skill-sync
description: Synchronize skills with team repository (Forgejo). Push your skills to create a PR, pull updates from other agents, browse available skills.
version: 1.2
category: platform
---

# Skill Sync

Direct file-level sync between your profile and the team git repository. **Does NOT go through the database.**

## Tools

| Tool | What it does |
|------|-------------|
| `skill_push` | Copies files from your profile `skills/` directory → git clone → commit → push to per-agent branch → creates PR |
| `skill_pull` | Copies files from git clone (origin/main) → your profile `skills/` directory. **Skips files you modified locally** (mtime check). Returns `skipped_newer_local` list. |
| `skill_list_remote` | Reads SKILL.md frontmatter from repo via Forgejo API. Returns name, slug, category, description. |

## Data flow

```
skill_push:  profile/skills/*  →  git clone  →  branch  →  PR  →  (human merges)  →  main
skill_pull:  main (via git clone)  →  profile/skills/*  (skips newer local files)
```

**Your profile is the source of truth.** The orchestrator does NOT overwrite your agent_created skill files.

## Workflow

1. Create or update skill locally using `skill_manage`
2. Call `skill_push` with your `agent_id`
3. A PR is created on a per-agent branch for human review
4. After merge, other agents call `skill_pull` to get your updates

## Safety guarantees

- **skill_push**: pushes to per-agent branch, NOT main. No force push. PR required. Deletes files in repo that you no longer have locally.
- **skill_pull**: reads only from main (after PR merge). **Never overwrites locally modified files** — compares modification times, skips files newer in your profile. Returns list of protected files in `skipped_newer_local`.
- **Orchestrator**: does NOT touch your agent_created skill files between sessions.

## Conflict Resolution

If `skill_push` returns a conflict:

```json
{
  "conflict": true,
  "files": [
    {
      "path": "skills/board-design/check-i2c-bus/SKILL.md",
      "yours": "# Your version...",
      "theirs": "# Main version..."
    }
  ]
}
```

Review both versions. Decide which to keep (or merge content from both). Update the skill file locally, then call `skill_push` again.

## Common mistakes

- **Do NOT** use `git` CLI directly. Always use skill_push/skill_pull.
- **Do NOT** skip skill_push before asking someone to merge. Your local changes are not in the repo until you push.
- skill_push requires `agent_id` parameter. skill_list_remote accepts optional `agent_id` for per-agent repo lookup.
