from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.catalog_copy import customer_price_display, english_catalog_fields  # noqa: E402


class CatalogCopyTests(unittest.TestCase):
    def test_uses_exact_clean_keys_and_creates_natural_english_copy(self):
        fields = english_catalog_fields(
            {
                "category": "饰品配饰",
                "specs": {
                    "种类": "项链",
                    "材质": "不锈钢",
                    "颜色": "银色、金色",
                    "风格": "简约",
                    "造型": "几何型",
                    "处理工艺": "电镀",
                    "周长": "41cm(含)-50cm(含)",
                    "重量": "6G",
                    "材质 不锈钢 处理工艺": "电镀",
                },
            }
        )
        self.assertEqual(fields["category_en"], "Necklaces")
        self.assertEqual(fields["material_en"], "Stainless Steel")
        self.assertEqual(fields["color_en"], "Gold / Silver")
        self.assertEqual(fields["size_en"], "41 cm–50 cm")
        self.assertIn("Geometric silhouette", fields["key_features_en"])
        self.assertNotIn("材质 不锈钢", fields["key_features_en"])

    def test_drops_variant_labels_that_are_not_real_colors(self):
        fields = english_catalog_fields({"specs": {"颜色": "款式1"}})
        self.assertEqual(fields["color_en"], "")

    def test_translates_tiered_price_note(self):
        self.assertEqual(customer_price_display("¥3.00-5.00 (阶梯价)"), "¥3.00-5.00 (Tiered Price)")


if __name__ == "__main__":
    unittest.main()
