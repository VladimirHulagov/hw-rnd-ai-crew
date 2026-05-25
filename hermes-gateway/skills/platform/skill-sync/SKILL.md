---
name: skill-sync
description: Synchronize skills with team repository (Forgejo). Push your skills to create a PR, pull updates from other agents, browse available skills.
version: 1.0
category: platform
---

# Skill Sync

Manage skill synchronization with the team repository.

## When to use

- After creating or updating a skill via `skill_manage` → call `mcp_skill-sync_skill_push`
- To get updates from other agents → call `mcp_skill-sync_skill_pull`
- To browse available skills → call `mcp_skill-sync_skill_list_remote`

## Tools

| Tool | Purpose |
|------|---------|
| `mcp_skill-sync_skill_push` | Push your skills to remote. Creates branch + PR for review. |
| `mcp_skill-sync_skill_pull` | Pull latest skills from remote main into your profile. |
| `mcp_skill-sync_skill_list_remote` | List skills in remote repository. |

## Workflow

1. Create or update skill locally using `skill_manage`
2. Call `mcp_skill-sync_skill_push` with your `agent_id`
3. A PR is created for human review
4. Other agents call `mcp_skill-sync_skill_pull` to get your updates after PR is merged

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
