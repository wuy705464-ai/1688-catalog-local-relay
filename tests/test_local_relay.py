from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.local_store import LocalStore, validate_record  # noqa: E402
from src.runtime_config import load_env_file  # noqa: E402


def record(offer_id: str, title: str = "测试项链"):
    return {
        "schema_version": 3,
        "offer_id": offer_id,
        "url": f"https://detail.1688.com/offer/{offer_id}.html",
        "title": title,
        "category": "项链",
        "price": {"raw": "10件 ¥8.00", "display": "¥8.00", "tiers": [{"min_qty": 10, "unit_price": 8.0}]},
        "size": {"raw": "链长: 45cm", "source": "attribute"},
        "specs": {"材质": "不锈钢", "链长": "45cm"},
        "image_urls": [f"https://img.alicdn.com/imgextra/{offer_id}/{i}.jpg" for i in range(8)],
        "collected_at": "2026-08-21T12:00:00Z",
    }


class LocalRelayStoreTests(unittest.TestCase):
    def setUp(self):
        temp_root = ROOT / "tests" / "_runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.root = temp_root / f"local-store-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.store = LocalStore(self.root / "catalog.db")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_explicit_env_file_overrides_machine_value(self):
        env_path = self.root / "relay.env"
        env_path.write_text("ARK_MODEL=file-model\n", encoding="utf-8")
        old = os.environ.get("ARK_MODEL")
        os.environ["ARK_MODEL"] = "machine-placeholder"
        try:
            load_env_file(env_path, override=True)
            self.assertEqual(os.environ["ARK_MODEL"], "file-model")
        finally:
            if old is None:
                os.environ.pop("ARK_MODEL", None)
            else:
                os.environ["ARK_MODEL"] = old

    def make_candidates(self, offer_id: str, version_hash: str):
        version_dir = self.root / "products" / "selected" / offer_id / version_hash[:16]
        version_dir.mkdir(parents=True)
        candidates = []
        for index, color in enumerate(((220, 30, 30), (30, 180, 30), (30, 30, 220)), start=1):
            path = version_dir / f"{index:02d}.jpg"
            Image.new("RGB", (400, 400), color).save(path)
            candidates.append(
                {
                    "candidate_index": index,
                    "source_url": f"https://img.alicdn.com/{offer_id}/{index}.jpg",
                    "width": 400,
                    "height": 400,
                    "sha256": f"sha-{offer_id}-{index}",
                    "role": ("hero", "detail", "lifestyle")[index - 1],
                    "reason": "test",
                    "is_selected": True,
                    "selected_rank": index,
                    "local_path": str(path),
                }
            )
        return candidates

    def test_rejects_url_offer_mismatch(self):
        bad = record("920000000001")
        bad["url"] = "https://detail.1688.com/offer/920000000002.html"
        with self.assertRaisesRegex(ValueError, "do not match"):
            validate_record(bad)

    def test_idempotent_upsert_and_exact_alignment(self):
        first = self.store.upsert_product(record("920000000001"))
        duplicate = self.store.upsert_product(record("920000000001"))
        self.assertFalse(first["unchanged"])
        self.assertTrue(duplicate["unchanged"])

        job = self.store.claim_next_job("test-worker")
        self.assertEqual(job["offer_id"], "920000000001")
        candidates = self.make_candidates(job["offer_id"], job["version_hash"])
        committed = self.store.complete_selection(
            job["offer_id"], job["version_hash"], candidates, "test", required_selected=3
        )
        self.assertTrue(committed)

        exported = self.store.products_for_export()
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["offer_id"], "920000000001")
        self.assertEqual([i["offer_id"] for i in exported[0]["selected_images"]], ["920000000001"] * 3)

    def test_stale_worker_cannot_attach_images_to_new_record(self):
        first = self.store.upsert_product(record("920000000003", "旧标题"))
        old_job = self.store.claim_next_job("old-worker")
        self.assertEqual(first["record_hash"], old_job["version_hash"])
        self.store.upsert_product(record("920000000003", "新标题"))

        old_candidates = self.make_candidates(old_job["offer_id"], old_job["version_hash"])
        committed = self.store.complete_selection(
            old_job["offer_id"], old_job["version_hash"], old_candidates, "stale-test", required_selected=3
        )
        self.assertFalse(committed)
        self.assertEqual(self.store.products_for_export(), [])


if __name__ == "__main__":
    unittest.main()
