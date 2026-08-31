from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.archive_image_review_candidates import make_contact_sheet  # noqa: E402


class ImageReviewArchiveTests(unittest.TestCase):
    def test_contact_sheet_has_numbered_grid_for_twelve_candidates(self):
        root = ROOT / "tests" / "_runtime" / f"review-sheet-{uuid.uuid4().hex}"
        try:
            root.mkdir(parents=True)
            candidates = []
            for index in range(1, 13):
                path = root / f"{index:02d}.jpg"
                Image.new("RGB", (400, 300), ((index * 13) % 255, 80, 140)).save(path)
                candidates.append({"candidate_index": index, "temp_path": str(path)})
            output = root / "review-sheet.jpg"
            make_contact_sheet(candidates, output)
            self.assertTrue(output.exists())
            with Image.open(output) as sheet:
                self.assertEqual(sheet.size, (720, 1088))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
