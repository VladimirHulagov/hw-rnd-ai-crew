import hashlib
import logging
import os

import yaml
from argon2 import PasswordHasher, Type

logger = logging.getLogger("gateway-orchestrator.authelia")

AUTHELIA_USERS_FILE = os.environ.get("AUTHELIA_USERS_FILE", "/authelia-config/users_database.yml")


def _generate_locked_password() -> str:
    raw = hashlib.sha256(os.urandom(64)).hexdigest()
    ph = PasswordHasher(
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )
    return ph.hash(raw)


def _read_users_db() -> dict:
    with open(AUTHELIA_USERS_FILE, "r") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    if "users" not in data or data["users"] is None:
        data["users"] = {}
    return data


def _write_users_db(data: dict) -> None:
    tmp = AUTHELIA_USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.replace(tmp, AUTHELIA_USERS_FILE)


def ensure_authelia_user(name: str) -> bool:
    try:
        data = _read_users_db()
        if name in data["users"]:
            return False
        data["users"][name] = {
            "displayname": name,
            "password": _generate_locked_password(),
            "email": "",
            "groups": ["bots"],
        }
        _write_users_db(data)
        logger.info("Created Authelia user: %s", name)
        return True
    except Exception:
        logger.exception("Failed to ensure Authelia user: %s", name)
        return False


def rename_authelia_user(old_name: str, new_name: str) -> bool:
    try:
        data = _read_users_db()
        if old_name in data["users"]:
            data["users"][new_name] = data["users"].pop(old_name)
            data["users"][new_name]["displayname"] = new_name
            _write_users_db(data)
            logger.info("Renamed Authelia user: %s -> %s", old_name, new_name)
            return True
        data["users"][new_name] = {
            "displayname": new_name,
            "password": _generate_locked_password(),
            "email": "",
            "groups": ["bots"],
        }
        _write_users_db(data)
        logger.info("Created Authelia user (rename fallback): %s", new_name)
        return True
    except Exception:
        logger.exception("Failed to rename Authelia user: %s -> %s", old_name, new_name)
        return False
