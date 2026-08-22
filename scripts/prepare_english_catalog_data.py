"""Prepare strict, English customer-catalog rows without changing the template."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.catalog_copy import customer_price_display, english_catalog_fields  # noqa: E402
from src.local_store import LocalStore  # noqa: E402
from src.runtime_config import load_runtime_config, resolve_project_path  # noqa: E402

FIRST_ROLE = "white_background_product"
SECONDARY_ROLES = {"worn_on_person", "supplementary_product_view"}


def validate_images(product: Dict[str, Any]) -> List[Dict[str, Any]]:
    offer_id = str(product["offer_id"])
    hash_prefix = str(product["record_hash"])[:16]
    images = list(product.get("selected_images") or [])
    if len(images) != 3:
        raise ValueError(f"{offer_id}: expected exactly three selected images")
    ranks = [int(image.get("selected_rank") or 0) for image in images]
    roles = tuple(str(image.get("role") or "") for image in images)
    if ranks != [1, 2, 3] or len(roles) != 3 or roles[0] != FIRST_ROLE or any(role not in SECONDARY_ROLES for role in roles[1:]):
        raise ValueError(f"{offer_id}: image role contract is not white-background + two approved secondary views")
    for image in images:
        if str(image.get("offer_id")) != offer_id:
            raise ValueError(f"{offer_id}: image foreign key mismatch")
        path = Path(str(image.get("local_path") or "")).resolve()
        if offer_id not in path.parts or hash_prefix not in path.parts or not path.exists():
            raise ValueError(f"{offer_id}: selected image path is invalid")
    return images


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare auditable English catalog JSON from strict selected images")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    cfg = load_runtime_config(args.config)
    db_path = args.db.resolve() if args.db else resolve_project_path(
        str(cfg.get("relay", {}).get("database") or ""), "data/catalog.db"
    )
    # Filter by the new image-role contract before applying the output limit.
    # Older ready rows remain safely in the database but cannot accidentally
    # displace refreshed rows just because they sort earlier by category/SKU.
    rows = LocalStore(db_path).products_for_export()
    output: List[Dict[str, Any]] = []
    rejected: List[Dict[str, str]] = []
    for product in rows:
        try:
            images = validate_images(product)
            copy = english_catalog_fields(product["record"], product.get("category") or "", product.get("size_raw") or "")
            output.append(
                {
                    "offer_id": product["offer_id"],
                    "record_hash": product["record_hash"],
                    "price_display": customer_price_display(product.get("price_display") or ""),
                    "source_url": product["record"].get("url") or "",
                    "images": [
                        {
                            "rank": image["selected_rank"],
                            "role": image["role"],
                            "path": image["local_path"],
                            "source_url": image["source_url"],
                            "reason": image["ai_reason"],
                        }
                        for image in images
                    ],
                    **copy,
                }
            )
            if args.limit > 0 and len(output) >= args.limit:
                break
        except ValueError as exc:
            rejected.append({"offer_id": str(product["offer_id"]), "reason": str(exc)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"products": output, "rejected": rejected}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"prepared: {len(output)}")
    print(f"rejected: {len(rejected)}")


if __name__ == "__main__":
    main()
