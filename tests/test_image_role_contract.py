from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.image_selector import NeedsHumanVisualReview, _validate_role_order, select_with_doubao  # noqa: E402


class ImageRoleContractTests(unittest.TestCase):
    def test_accepts_white_background_then_worn_or_clean_secondary_images(self):
        _validate_role_order(
            [
                {"role": "white_background_product"},
                {"role": "supplementary_product_view"},
                {"role": "worn_on_person"},
            ],
            3,
        )

    def test_rejects_an_unknown_secondary_image_role(self):
        with self.assertRaisesRegex(ValueError, "role contract"):
            _validate_role_order(
                [
                    {"role": "white_background_product"},
                    {"role": "supplementary_product_view"},
                    {"role": "detail"},
                ],
                3,
            )

    def test_empty_model_selection_is_a_manual_review_not_a_generic_failure(self):
        with self.assertRaisesRegex(NeedsHumanVisualReview, "insufficient qualified images"):
            # Parsing happens before any network I/O, so a minimal invalid
            # candidate list is sufficient to exercise the returned verdict.
            from unittest.mock import Mock, patch
            from src import image_selector
            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": '{"offer_id":"900000000001","selected":[],"reason":"insufficient qualified images"}'}}]
            }
            cfg = {"vision": {"api_key_env": "TEST_KEY", "base_url_env": "TEST_URL", "model": "model"}}
            record = {"offer_id": "900000000001", "title": "", "category": "", "specs": {}}
            candidate = {"candidate_index": 1, "temp_path": __file__, "width": 500, "height": 500}
            with patch.dict("os.environ", {"TEST_KEY": "k", "TEST_URL": "https://example.invalid"}, clear=False), \
                 patch.object(image_selector, "_ai_preview_data_url", return_value="data:image/jpeg;base64,AA=="), \
                 patch.object(image_selector.requests, "post", return_value=response):
                select_with_doubao(record, [candidate], 3, cfg)


if __name__ == "__main__":
    unittest.main()
