# Per-Agent Outline Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each Hermes agent gets its own Outline user and API key, so actions in Outline are attributed to the agent.

**Architecture:** New `outline_user.py` module creates Outline users via REST API and API keys via direct DB INSERT. Orchestrator calls it during provisioning. Per-agent key stored in Paperclip DB `adapter_config.outline.apiKey` and injected into config.yaml.

**Tech Stack:** Python 3.11, httpx, psycopg2, Outline REST API, Outline PostgreSQL (direct INSERT)

---

## File Structure

| Action | Path | Purpose |
|--------|------|---------|
| Create | `hermes-gateway/orchestrator/outline_user.py` | Outline user/key provisioning |
| Modify | `hermes-gateway/orchestrator/config_generator.py:16-31,34-48` | Accept per-agent outline key |
| Modify | `hermes-gateway/orchestrator/orchestrator.py:24,308-322` | Call ensure_outline_user |
| Modify | `docker-compose.yml:82-112` | Add outline_internal network + env vars |
| Modify | `.env.example` | Document OUTLINE_DB_URL |

---

### Task 1: Create outline_user.py module

**Files:**
- Create: `hermes-gateway/orchestrator/outline_user.py`

- [ ] **Step 1: Create the module**

Create `hermes-gateway/orchestrator/outline_user.py`:

```python
import base64
import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone

import httpx
import psycopg2
import psycopg2.extras

logger = logging.getLogger("gateway-orchestrator.outline")

OUTLINE_URL = os.environ.get("OUTLINE_URL", "https://outline.collaborationism.tech")
OUTLINE_API_KEY = os.environ.get("OUTLINE_API_KEY", "")
OUTLINE_DB_URL = os.environ.get("OUTLINE_DB_URL", "")


def _generate_api_key() -> str:
    raw = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    return f"ol_api_{raw}"


def _find_outline_user(name: str) -> str | None:
    resp = httpx.post(
        f"{OUTLINE_URL}/api/users.list",
        headers={"Authorization": f"Bearer {OUTLINE_API_KEY}", "Content-Type": "application/json"},
        json={"query": name, "limit": 100},
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error("Outline users.list failed: %d %s", resp.status_code, resp.text[:200])
        return None
    for user in resp.json().get("data", []):
        if user.get("name") == name:
            return user["id"]
    return None


def _create_outline_user(name: str) -> str | None:
    email = f"{name.lower().replace(' ', '-')}@bots.collaborationism.tech"
    resp = httpx.post(
        f"{OUTLINE_URL}/api/users.invite",
        headers={"Authorization": f"Bearer {OUTLINE_API_KEY}", "Content-Type": "application/json"},
        json={"invites": [{"email": email, "name": name, "role": "member"}]},
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error("Outline users.invite failed: %d %s", resp.status_code, resp.text[:200])
        return None
    users = resp.json().get("data", {}).get("users", [])
    if users:
        return users[0]["id"]
    return _find_outline_user(name)


def _insert_api_key(user_id: str, key: str) -> bool:
    if not OUTLINE_DB_URL:
        logger.error("OUTLINE_DB_URL not set, cannot create Outline API key")
        return False
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    last4 = key[-4:]
    now = datetime.now(timezone.utc).isoformat()
    key_id = str(uuid.uuid4())
    try:
        conn = psycopg2.connect(OUTLINE_DB_URL)
        conn.set_session(autocommit=True)
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO "apiKeys" ("id", "name", "hash", "last4", "userId", "createdAt", "updatedAt") VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (key_id, "hermes", key_hash, last4, user_id, now, now),
        )
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to insert Outline API key: %s", e)
        return False


def _save_key_to_paperclip(agent_id: str, key: str, db_url: str):
    try:
        conn = psycopg2.connect(db_url)
        conn.set_session(autocommit=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT adapter_config FROM agents WHERE id = %s", (agent_id,))
        row = cur.fetchone()
        config = row["adapter_config"] if row and row["adapter_config"] else {}
        if "outline" not in config:
            config["outline"] = {}
        config["outline"]["apiKey"] = key
        cur.execute("UPDATE agents SET adapter_config = %s WHERE id = %s", (psycopg2.extras.Json(config), agent_id))
        cur.close()
        conn.close()
    except Exception as e:
        logger.error("Failed to save Outline key to Paperclip DB: %s", e)


def _load_key_from_paperclip(agent_id: str, db_url: str) -> str | None:
    try:
        conn = psycopg2.connect(db_url)
        conn.set_session(autocommit=True, readonly=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT adapter_config FROM agents WHERE id = %s", (agent_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row or not row["adapter_config"]:
            return None
        return (row["adapter_config"].get("outline") or {}).get("apiKey")
    except Exception:
        return None


def ensure_outline_user(agent_name: str, agent_id: str, paperclip_db_url: str) -> str | None:
    if not OUTLINE_API_KEY:
        return None
    try:
        existing_key = _load_key_from_paperclip(agent_id, paperclip_db_url)
        if existing_key:
            return existing_key
        user_id = _find_outline_user(agent_name)
        if not user_id:
            user_id = _create_outline_user(agent_name)
        if not user_id:
            logger.error("Could not find or create Outline user for %s", agent_name)
            return None
        key = _generate_api_key()
        if not _insert_api_key(user_id, key):
            return None
        _save_key_to_paperclip(agent_id, key, paperclip_db_url)
        logger.info("Created Outline user '%s' (id=%s) with API key", agent_name, user_id[:8])
        return key
    except Exception:
        logger.exception("Failed to ensure Outline user for %s", agent_name)
        return None
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/orchestrator/outline_user.py
git commit -m "Add outline_user module for per-agent Outline auth"
```

---

### Task 2: Modify config_generator.py to accept per-agent outline key

**Files:**
- Modify: `hermes-gateway/orchestrator/config_generator.py`

- [ ] **Step 1: Add outline_api_key parameter**

In `config_generator.py`, modify the `generate_profile_config` function signature (line 16-31). Add `outline_api_key: str | None = None` parameter after `paperclip_api_key`:

```python
def generate_profile_config(
    agent_id: str,
    company_id: str,
    allocated_port: int,
    model: str = "glm-5.1",
    provider: str = "zai",
    personality: str = "kawaii",
    summary_model: str = "glm-5",
    vision_model: str = "glm-4.6v",
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
    telegram_allowed_users: str | None = None,
    telegram_clarify_timeout: int | None = None,
    agent_name: str | None = None,
    paperclip_api_key: str = "",
    outline_api_key: str | None = None,
) -> str:
```

- [ ] **Step 2: Use per-agent key in values dict**

Change line 43 in the values dict from:

```python
        "outline_api_key": os.environ.get("MCP_OUTLINE_API_KEY", ""),
```

to:

```python
        "outline_api_key": outline_api_key or os.environ.get("MCP_OUTLINE_API_KEY", ""),
```

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/orchestrator/config_generator.py
git commit -m "Accept per-agent outline_api_key in config generator"
```

---

### Task 3: Integrate outline_user into orchestrator

**Files:**
- Modify: `hermes-gateway/orchestrator/orchestrator.py`

- [ ] **Step 1: Add import**

After line 25 (`from authelia_sync import ensure_authelia_user, rename_authelia_user`), add:

```python
from outline_user import ensure_outline_user
```

- [ ] **Step 2: Call ensure_outline_user and pass key to config**

In `provision_agent()`, replace the block that generates config (lines 312-323). The current code:

```python
        config = generate_profile_config(
            agent_id=agent_id,
            company_id=company_id,
            allocated_port=port,
            telegram_bot_token=agent_telegram.get("botToken") if enable_telegram else None,
            telegram_chat_id=agent_telegram.get("chatId") if enable_telegram else None,
            telegram_allowed_users=agent_telegram.get("allowedUsers") if enable_telegram else None,
            telegram_clarify_timeout=agent_telegram.get("defaultTimeout", 600) if enable_telegram else None,
            agent_name=name,
            paperclip_api_key=agent_jwt,
        )
```

Replace with:

```python
        agent_outline_key = None
        try:
            agent_outline_key = ensure_outline_user(name, agent_id, DATABASE_URL)
        except Exception:
            logger.error("Failed to provision Outline user for %s", name)

        config = generate_profile_config(
            agent_id=agent_id,
            company_id=company_id,
            allocated_port=port,
            telegram_bot_token=agent_telegram.get("botToken") if enable_telegram else None,
            telegram_chat_id=agent_telegram.get("chatId") if enable_telegram else None,
            telegram_allowed_users=agent_telegram.get("allowedUsers") if enable_telegram else None,
            telegram_clarify_timeout=agent_telegram.get("defaultTimeout", 600) if enable_telegram else None,
            agent_name=name,
            paperclip_api_key=agent_jwt,
            outline_api_key=agent_outline_key,
        )
```

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "Integrate per-agent Outline auth into orchestrator provisioning"
```

---

### Task 4: Update docker-compose.yml — network + env vars

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add environment variables**

In the `hermes-gateway` service `environment` section, after `AUTHELIA_USERS_FILE` (line 102), add:

```yaml
      OUTLINE_URL: "${OUTLINE_URL:-}"
      OUTLINE_API_KEY: "${OUTLINE_API_KEY:-}"
      OUTLINE_DB_URL: "${OUTLINE_DB_URL:-}"
```

- [ ] **Step 2: Add outline_internal network**

In the `hermes-gateway` service `networks` section (line 112), add `outline_internal`:

```yaml
    networks:
      - local-ai-internal
      - outline_internal
```

- [ ] **Step 3: Declare outline_internal as external network**

In the top-level `networks` section (after `traefik:` around line 225), add:

```yaml
  outline_internal:
    name: outline_internal
    external: true
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "Connect hermes-gateway to Outline network and add env vars"
```

---

### Task 5: Update .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add OUTLINE_DB_URL**

After the `OUTLINE_QDRANT_COLLECTION` line, add:

```
# OUTLINE_DB_URL=postgres://outline:password@outline-postgres:5432/outline
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "Document OUTLINE_DB_URL env var"
```

---

### Task 6: Clean up test data and configure env

**Files:** None (manual cleanup + env setup)

- [ ] **Step 1: Remove test bot user from Outline**

```bash
docker exec outline-postgres psql -U outline -d outline -c "DELETE FROM \"apiKeys\" WHERE \"userId\" = 'c5f74356-8305-4f3b-9a64-fee10a80368b'; DELETE FROM users WHERE id = 'c5f74356-8305-4f3b-9a64-fee10a80368b';"
```

- [ ] **Step 2: Add OUTLINE_DB_URL to .env**

```bash
echo 'OUTLINE_DB_URL=postgres://outline:199dc1ddf149a3b0408550848b5c6c9c@outline-postgres:5432/outline' >> .env
```

- [ ] **Step 3: Rebuild and deploy**

```bash
docker compose build hermes-gateway && docker compose up -d --force-recreate hermes-gateway
```

- [ ] **Step 4: Wait for reconciliation and verify**

```bash
sleep 75 && docker logs hermes-gateway 2>&1 | grep -i outline
```

Expected: `"Created Outline user 'CEO' ..."` and `"Created Outline user 'Founding Engineer' ..."`.

- [ ] **Step 5: Verify Outline users and API keys**

```bash
docker exec outline-postgres psql -U outline -d outline -c "SELECT u.name, u.role, k.name as key_name, k.last4 FROM users u LEFT JOIN \"apiKeys\" k ON k.\"userId\" = u.id WHERE u.name NOT IN ('Vladimir');" 2>/dev/null
```

Expected: agent users with `member` role and `hermes` API keys.

- [ ] **Step 6: Verify Paperclip DB has keys stored**

```bash
docker exec paperclip-db psql -U paperclip -d paperclip -c "SELECT a.name, a.adapter_config->'outline'->>'apiKey' as outline_key FROM agents WHERE adapter_config->'outline'->>'apiKey' IS NOT NULL;" 2>/dev/null
```

Expected: agent names with `ol_api_...` keys.
