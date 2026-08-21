from __future__ import annotations

import shutil
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.relay_api import create_app  # noqa: E402


class RelayApiTests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / "tests" / "_runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.root = runtime / f"api-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.config = self.root / "config.yaml"
        self.config.write_text(
            yaml.safe_dump(
                {
                    "relay": {"token": "test-token", "database": str(self.root / "catalog.db")},
                    "image_selection": {"candidate_count": 8, "selected_count": 3},
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        self.client = TestClient(create_app(self.config, start_worker=False))

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def payload():
        offer_id = "950000000001"
        return {
            "schema_version": 3,
            "offer_id": offer_id,
            "url": f"https://detail.1688.com/offer/{offer_id}.html",
            "title": "API 测试项链",
            "category": "项链",
            "price": {"raw": "¥5.00", "display": "¥5.00", "tiers": []},
            "size": {"raw": "45cm", "source": "attribute"},
            "specs": {"链长": "45cm"},
            "image_urls": [f"https://img.alicdn.com/{offer_id}/{i}.jpg" for i in range(8)],
            "collected_at": "2026-08-21T12:00:00Z",
        }

    def test_requires_token_and_returns_matching_offer_id(self):
        self.assertEqual(self.client.post("/api/v1/products", json=self.payload()).status_code, 401)
        response = self.client.post(
            "/api/v1/products",
            json=self.payload(),
            headers={"X-Relay-Token": "test-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["offer_id"], self.payload()["offer_id"])
        self.assertEqual(body["stats"]["total"], 1)

    def test_refuses_shipped_default_token(self):
        unsafe_config = self.root / "unsafe.yaml"
        unsafe_config.write_text(
            yaml.safe_dump(
                {
                    "relay": {
                        "token": "CHANGE_ME_LOCAL_TOKEN",
                        "database": str(self.root / "unsafe.db"),
                    }
                }
            ),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"RELAY_TOKEN": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "private relay.token"):
                create_app(unsafe_config, start_worker=False)


if __name__ == "__main__":
    unittest.main()
