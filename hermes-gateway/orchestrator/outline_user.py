import hashlib
import logging
import os
import secrets
import string
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
    chars = string.ascii_letters + string.digits + "_"
    return "ol_api_" + "".join(secrets.choice(chars) for _ in range(38))


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
