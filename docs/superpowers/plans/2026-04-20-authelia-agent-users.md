# Authelia Agent Users Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically create and maintain Authelia users in the `bots` group when agents are provisioned by the Hermes gateway orchestrator.

**Architecture:** New `authelia_sync.py` module with idempotent YAML manipulation functions, called from the existing orchestrator's `provision_agent()` and `reconcile()` flows. Authelia config directory bind-mounted into hermes-gateway container.

**Tech Stack:** Python 3.11, PyYAML, argon2-cffi (new dependency), Authelia file-based user database

---

## File Structure

| Action | Path | Purpose |
|--------|------|---------|
| Create | `hermes-gateway/orchestrator/authelia_sync.py` | Authelia user CRUD via YAML |
| Modify | `hermes-gateway/requirements.txt` | Add `argon2-cffi` dependency |
| Modify | `hermes-gateway/orchestrator/orchestrator.py` | Call authelia_sync from provision/reconcile |
| Modify | `docker-compose.yml` | Bind-mount Authelia config into hermes-gateway |
| Modify | `.env.example` | Document `AUTHELIA_USERS_FILE` env var |

---

### Task 1: Add argon2-cffi dependency

**Files:**
- Modify: `hermes-gateway/requirements.txt`

- [ ] **Step 1: Add argon2-cffi to requirements**

Add `argon2-cffi>=23.1.0` to `hermes-gateway/requirements.txt` after the existing `pyyaml` line:

```
argon2-cffi>=23.1.0
```

- [ ] **Step 2: Rebuild hermes-gateway image**

```bash
docker build -t hermes-gateway:latest hermes-gateway/
```

- [ ] **Step 3: Verify package available**

```bash
docker run --rm hermes-gateway:latest python -c "from argon2 import PasswordHasher; print('argon2-cffi OK')"
```

Expected: `argon2-cffi OK`

---

### Task 2: Create authelia_sync.py module

**Files:**
- Create: `hermes-gateway/orchestrator/authelia_sync.py`

- [ ] **Step 1: Create the module**

Create `hermes-gateway/orchestrator/authelia_sync.py` with the following content:

```python
import hashlib
import logging
import os
from pathlib import Path

import yaml
from argon2 import PasswordHasher

logger = logging.getLogger("gateway-orchestrator.authelia")

AUTHELIA_USERS_FILE = os.environ.get(
    "AUTHELIA_USERS_FILE", "/authelia-config/users_database.yml"
)

ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=2,
)


def _generate_locked_password() -> str:
    raw = hashlib.sha256(os.urandom(64)).hexdigest()
    return ph.hash(raw)


def _read_users_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"users": {}}
    with open(p, "r") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    if "users" not in data or data["users"] is None:
        data["users"] = {}
    return data


def _write_users_file(path: str, data: dict):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def ensure_authelia_user(name: str) -> bool:
    if not name:
        return False
    try:
        data = _read_users_file(AUTHELIA_USERS_FILE)
        if name in data["users"]:
            logger.debug("Authelia user '%s' already exists", name)
            return False
        data["users"][name] = {
            "displayname": name,
            "password": _generate_locked_password(),
            "email": "",
            "groups": ["bots"],
        }
        _write_users_file(AUTHELIA_USERS_FILE, data)
        logger.info("Created Authelia user '%s' in group 'bots'", name)
        return True
    except Exception as e:
        logger.error("Failed to create Authelia user '%s': %s", name, e)
        return False


def rename_authelia_user(old_name: str, new_name: str) -> bool:
    if not old_name or not new_name or old_name == new_name:
        return False
    try:
        data = _read_users_file(AUTHELIA_USERS_FILE)
        if old_name not in data["users"]:
            logger.debug("Authelia user '%s' not found, creating '%s' instead", old_name, new_name)
            return ensure_authelia_user(new_name)
        user_data = data["users"].pop(old_name)
        user_data["displayname"] = new_name
        data["users"][new_name] = user_data
        _write_users_file(AUTHELIA_USERS_FILE, data)
        logger.info("Renamed Authelia user '%s' → '%s'", old_name, new_name)
        return True
    except Exception as e:
        logger.error("Failed to rename Authelia user '%s' → '%s': %s", old_name, new_name, e)
        return False
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/orchestrator/authelia_sync.py
git commit -m "Add authelia_sync module for Authelia user management"
```

---

### Task 3: Integrate authelia_sync into orchestrator

**Files:**
- Modify: `hermes-gateway/orchestrator/orchestrator.py`

- [ ] **Step 1: Add import**

Add at the top of `orchestrator.py` after the existing local imports (line 24):

```python
from authelia_sync import ensure_authelia_user, rename_authelia_user
```

- [ ] **Step 2: Call ensure_authelia_user in provision_agent**

In `provision_agent()`, after the line `logger.info("Gateway %s started (port %d, profile %s)", proc_name, port, profile_dir)` (line 394), add:

```python
        try:
            ensure_authelia_user(name)
        except Exception:
            logger.error("Failed to create Authelia user for %s", name)
```

- [ ] **Step 3: Call rename_authelia_user in reconcile**

In `reconcile()`, inside the agent loop, right before `self._known_agents[agent_id] = agent` (line 426), add rename detection:

```python
            if agent_id in self._known_agents:
                old_name = self._known_agents[agent_id].get("name", "")
                new_name = agent.get("name", "")
                if old_name and new_name and old_name != new_name:
                    try:
                        rename_authelia_user(old_name, new_name)
                    except Exception:
                        logger.error("Failed to rename Authelia user '%s' → '%s'", old_name, new_name)
```

- [ ] **Step 4: Commit**

```bash
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "Integrate authelia_sync into orchestrator provision and reconcile"
```

---

### Task 4: Update docker-compose.yml with Authelia config mount

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add bind mount and env var**

In the `hermes-gateway` service `volumes` section (after line 110 `gateway_ports:/run/gateway-ports`), add:

```yaml
      - /mnt/services/authelia/config:/authelia-config:rw
```

In the `hermes-gateway` service `environment` section (after the `PAPERCLIP_DATA_PATH` line), add:

```yaml
      AUTHELIA_USERS_FILE: "/authelia-config/users_database.yml"
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "Mount Authelia config into hermes-gateway for user sync"
```

---

### Task 5: Update .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add AUTHELIA_USERS_FILE documentation**

Add to `.env.example`:

```
# Authelia integration (hermes-gateway)
# AUTHELIA_USERS_FILE=/authelia-config/users_database.yml
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "Document AUTHELIA_USERS_FILE env var"
```

---

### Task 6: Rebuild and deploy

**Files:** None (deployment)

- [ ] **Step 1: Rebuild hermes-gateway image with new dependency**

```bash
docker compose build hermes-gateway
```

- [ ] **Step 2: Restart hermes-gateway**

```bash
docker compose up -d --force-recreate hermes-gateway
```

- [ ] **Step 3: Verify logs show authelia_sync loaded**

```bash
docker logs hermes-gateway --tail 50 2>&1 | grep -i authelia
```

Expected: no import errors; on next reconciliation cycle, should see `"Created Authelia user '...' in group 'bots'"` for existing agents that don't have Authelia users yet.

- [ ] **Step 4: Verify users_database.yml updated**

```bash
cat /mnt/services/authelia/config/users_database.yml
```

Expected: existing agents listed under `users:` with `groups: [bots]` and argon2id password hashes.
