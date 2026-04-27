import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for mod in ["psycopg2", "psycopg2.extras", "httpx"]:
    sys.modules.setdefault(mod, MagicMock())


@pytest.fixture
def mock_cursor():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    return cursor


@pytest.fixture
def mock_db(mock_cursor):
    conn = MagicMock()
    conn.cursor.return_value = mock_cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    with patch("psycopg2.connect", return_value=conn) as mock_connect:
        yield mock_connect, conn, mock_cursor


@pytest.fixture
def mock_env(tmp_path):
    env = {
        "DATABASE_URL": "postgres://test:test@localhost:5432/test",
        "BETTER_AUTH_SECRET": "test-secret-key-at-least-32-chars-long!!",
        "PAPERCLIP_API_URL": "http://localhost:3100/api",
        "PAPERCLIP_DATA_PATH": str(tmp_path / "paperclip"),
        "PAPERCLIP_INSTANCE_ID": "default",
        "ORCHESTRATOR_POLL_INTERVAL": "60",
    }
    saved = {}
    for key, val in env.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = val
    yield env
    for key, old in saved.items():
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


@pytest.fixture
def sample_agent_row():
    return {
        "agent_id": "00000000-0000-0000-0000-000000000001",
        "agent_name": "Test Agent",
        "company_id": "00000000-0000-0000-0000-000000000010",
        "company_name": "Test Company",
        "role": "worker",
        "adapter_type": "hermes_local",
        "status": "active",
        "adapter_config": json.dumps({}),
        "personality": "kawaii",
    }


@pytest.fixture
def temp_profile_dir(tmp_path):
    profile = tmp_path / "profiles" / "00000000-0000-0000-0000-000000000001"
    profile.mkdir(parents=True)
    (profile / "memories").mkdir()
    (profile / "skills").mkdir()
    (profile / "sessions").mkdir()
    return profile


@pytest.fixture
def agent_api_keys_file(tmp_path):
    keys = {
        "00000000-0000-0000-0000-000000000001": "pcp_test_key_agent_1",
        "00000000-0000-0000-0000-000000000002": "pcp_test_key_agent_2",
    }
    path = tmp_path / "agent_api_keys.json"
    path.write_text(json.dumps(keys))
    return path
