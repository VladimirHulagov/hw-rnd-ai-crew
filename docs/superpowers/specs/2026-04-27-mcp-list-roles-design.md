# MCP Tools: paperclip_list_roles & paperclip_get_role

## Context

Agents with `canCreateAgents` permission need to see available company roles before hiring new agents. Currently paperclip-mcp has no tools for listing/viewing roles, though the Paperclip API already exposes `GET /companies/:companyId/roles` and `GET /companies/:companyId/roles/:roleId`.

## Design

### Two new MCP tools in paperclip-mcp

#### `paperclip_list_roles`

- Calls `GET /companies/{companyId}/roles`
- Returns compact list: `name`, `slug`, `key`, `description`, `category`, `assignedAgentCount`
- Optional parameter: `includeHidden` (bool, default false)

#### `paperclip_get_role(roleId)`

- Calls `GET /companies/{companyId}/roles/{roleId}`
- Returns full role detail including `markdown` and `usedByAgents[]`

### Access control

- Helper `_check_can_create_agents()` in `tools.py`: calls `GET /agents/me`, checks `permissions.canCreateAgents`
- Both tools call this helper before the main request
- If `false` → returns `{"error": "Permission denied: this action requires canCreateAgents permission"}`

### Files to modify

1. `paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py` — add `list_roles()`, `get_role()`, `_check_can_create_agents()`
2. `paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py` — register tools in `list_tools()` + dispatch in `_dispatch()`

### Deployment

- `docker cp` both files into `paperclip-mcp` container + `docker restart paperclip-mcp`

### Agent-side naming

Due to hermes-agent's auto-prefixing (`mcp_<server>_`), tools appear as:
- `mcp_paperclip_paperclip_list_roles`
- `mcp_paperclip_paperclip_get_role`

Instructions for agents must use these full prefixed names.
