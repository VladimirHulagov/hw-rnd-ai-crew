import pytest
from unittest.mock import patch, MagicMock
from rag.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


class TestWebhook:
    @patch("rag.main.index_file")
    @patch("rag.nextcloud.download_file")
    @patch("rag.main.delete_by_path")
    def test_webhook_file_created(self, mock_delete, mock_download, mock_index):
        mock_download.return_value = b"fake pdf content"
        mock_index.return_value = 3
        resp = client.post("/webhook/nextcloud", json={
            "object": {
                "name": "test.pdf",
                "path": "/Documents/test.pdf",
                "mimetype": "application/pdf",
                "size": 100,
            },
            "signal": "FileCreated",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "indexed"
        assert data["chunks"] == 3

    @patch("rag.main.delete_by_path")
    def test_webhook_file_deleted(self, mock_delete):
        resp = client.post("/webhook/nextcloud", json={
            "object": {
                "name": "test.pdf",
                "path": "/Documents/test.pdf",
                "mimetype": "application/pdf",
            },
            "signal": "FileDeleted",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_webhook_unsupported_type(self):
        resp = client.post("/webhook/nextcloud", json={
            "object": {
                "name": "photo.png",
                "path": "/Photos/photo.png",
                "mimetype": "image/png",
            },
            "signal": "FileCreated",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_webhook_no_path(self):
        resp = client.post("/webhook/nextcloud", json={
            "object": {},
            "signal": "FileCreated",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
