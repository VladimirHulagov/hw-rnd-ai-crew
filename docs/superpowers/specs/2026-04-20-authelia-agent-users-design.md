# Authelia Agent Users

## Problem

When a new agent is created in Paperclip, there is no corresponding user in Authelia. This means agents cannot be authenticated through Traefik's forward_auth mechanism for services that require Authelia SSO.

## Solution

Automatically create an Authelia user in the `bots` group when the Hermes gateway orchestrator provisions a new agent. Update the user when the agent is renamed. Do not delete users when agents are removed.

## Architecture

### Components

1. **`authelia_sync.py`** — new module in `hermes-gateway/orchestrator/`
   - `ensure_authelia_user(name: str) -> bool` — create user if not exists
   - `rename_authelia_user(old_name: str, new_name: str) -> bool` — rename user key and displayname

2. **`orchestrator.py`** — integration points
   - `provision_agent()`: call `ensure_authelia_user(name)` after gateway start
   - `_restart_agent()` / `reconcile()`: call `rename_authelia_user()` when agent name changes

3. **`docker-compose.yml`** — new bind mount
   - `/mnt/services/authelia/config:/authelia-config:rw` in hermes-gateway service
   - Env var `AUTHELIA_USERS_FILE` (default `/authelia-config/users_database.yml`)

### User record format

```yaml
users:
  "Agent Name":
    displayname: "Agent Name"
    password: "$argon2id$v=19$m=65536,t=3,p=4$..."
    email: ""
    groups:
      - bots
```

### Locked password

The password is a SHA-256 hash of 64 random bytes, then hashed with argon2id. The input secret is never stored — no one can log in with a password. Authentication happens only via Traefik forward_auth.

### Rename handling

When the orchestrator detects an agent name change (`_agent_data_changed()`), it calls `rename_authelia_user(old_name, new_name)` which:
1. Reads YAML, finds user by `old_name` key
2. Creates new entry with `new_name` as key, copies existing data
3. Removes old entry
4. Writes file back

This invalidates any existing sessions tied to the old username, which is acceptable since bots use locked passwords and don't maintain interactive sessions.

### Error handling

All Authelia operations are wrapped in try/except in the orchestrator. Errors are logged but do not prevent agent provisioning from completing. This ensures that Authelia being unavailable doesn't break agent functionality.

### What we do NOT do

- Delete users when agents are terminated/paused
- Create OIDC clients for agents
- Add API endpoints
- Modify Authelia `configuration.yml`
