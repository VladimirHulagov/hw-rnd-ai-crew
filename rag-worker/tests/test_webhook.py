import os
import pytest
from unittest.mock import patch, MagicMock
from rag.main import app
from fastapi.testclient import TestClient

os.environ.setdefault("NEXTCLOUD_USER", "vladimir")

client = TestClient(app)


class TestWebhook:
    @patch("rag.main.index_file")
    @patch("rag.nextcloud.download_file")
    @patch("rag.main.delete_by_path")
    def test_webhook_file_created(self, mock_delete, mock_download, mock_index):
        mock_download.return_value = b"fake pdf content"
        mock_index.return_value = 3
        resp = client.post("/webhook/nextcloud", json={
            "event": {
                "node": {"id": 1, "path": "/vladimir/files/Documents/test.pdf"},
                "class": "OCP\\Files\\Events\\Node\\NodeCreatedEvent",
            },
            "user": {"uid": "vladimir", "displayName": "vladimir"},
            "time": 1776017000,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "indexed"
        assert data["chunks"] == 3

    @patch("rag.main.delete_by_path")
    def test_webhook_file_deleted(self, mock_delete):
        resp = client.post("/webhook/nextcloud", json={
            "event": {
                "node": {"id": 1, "path": "/vladimir/files/Documents/test.pdf"},
                "class": "OCP\\Files\\Events\\Node\\NodeDeletedEvent",
            },
            "user": {"uid": "vladimir", "displayName": "vladimir"},
            "time": 1776017000,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_webhook_unsupported_type(self):
        resp = client.post("/webhook/nextcloud", json={
            "event": {
                "node": {"id": 1, "path": "/vladimir/files/Photos/photo.png"},
                "class": "OCP\\Files\\Events\\Node\\NodeCreatedEvent",
            },
            "user": {"uid": "vladimir", "displayName": "vladimir"},
            "time": 1776017000,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_webhook_no_path(self):
        resp = client.post("/webhook/nextcloud", json={
            "event": {
                "node": {},
                "class": "OCP\\Files\\Events\\Node\\NodeCreatedEvent",
            },
            "user": {"uid": "vladimir", "displayName": "vladimir"},
            "time": 1776017000,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
