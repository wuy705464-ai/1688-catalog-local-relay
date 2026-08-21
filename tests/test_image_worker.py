from __future__ import annotations

import sys
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import image_selector  # noqa: E402
from src.local_store import LocalStore  # noqa: E402


def product_record(offer_id: str):
    return {
        "schema_version": 3,
        "offer_id": offer_id,
        "url": f"https://detail.1688.com/offer/{offer_id}.html",
        "title": f"商品 {offer_id}",
        "category": "项链",
        "price": {"raw": "¥8.00", "display": "¥8.00", "tiers": []},
        "size": {"raw": "45cm", "source": "attribute"},
        "specs": {"链长": "45cm"},
        "image_urls": [f"https://img.alicdn.com/{offer_id}/{i}.jpg" for i in range(8)],
        "collected_at": "2026-08-21T12:00:00Z",
    }


class ImageWorkerTests(unittest.TestCase):
    def setUp(self):
        temp_root = ROOT / "tests" / "_runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.root = temp_root / f"image-worker-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.store = LocalStore(self.root / "data" / "catalog.db")
        self.cfg = {
            "relay": {"worker_max_attempts": 3},
            "image_selection": {"candidate_count": 8, "selected_count": 3, "delete_unselected": True},
            "vision": {"provider": "doubao"},
        }

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def fake_download(urls, temp_dir, cfg):
        temp_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for index in range(1, 9):
            path = temp_dir / f"{index:02d}.jpg"
            color = ((index * 29) % 255, (index * 61) % 255, (index * 97) % 255)
            Image.new("RGB", (500, 500), color).save(path)
            result.append(
                {
                    "candidate_index": index,
                    "source_url": urls[index - 1],
                    "temp_path": str(path),
                    "width": 500,
                    "height": 500,
                    "sha256": f"sha-{index}",
                    "dhash": index * 1000003,
                }
            )
        return result

    @staticmethod
    def fake_ai(record, candidates, selected_count, cfg):
        return [
            {"index": 1, "rank": 1, "role": "hero", "reason": "main"},
            {"index": 4, "rank": 2, "role": "detail", "reason": "detail"},
            {"index": 7, "rank": 3, "role": "lifestyle", "reason": "scene"},
        ], "doubao-test"

    def test_downloads_eight_and_keeps_only_three_in_offer_scoped_folder(self):
        offer_id = "930000000001"
        self.store.upsert_product(product_record(offer_id))
        with patch.object(image_selector, "PROJECT_ROOT", self.root), \
             patch.object(image_selector, "download_candidates", self.fake_download), \
             patch.object(image_selector, "select_with_doubao", self.fake_ai):
            worked = image_selector.process_one_job(self.store, self.cfg, "test-worker")
        self.assertTrue(worked)
        rows = self.store.products_for_export()
        self.assertEqual(len(rows), 1)
        selected = rows[0]["selected_images"]
        self.assertEqual(len(selected), 3)
        self.assertEqual([row["selected_rank"] for row in selected], [1, 2, 3])
        paths = [Path(row["local_path"]) for row in selected]
        self.assertTrue(all(offer_id in path.parts for path in paths))
        self.assertEqual(len(list(paths[0].parent.glob("*.jpg"))), 3)
        self.assertFalse((self.root / "products" / "candidates" / offer_id).exists())


if __name__ == "__main__":
    unittest.main()
