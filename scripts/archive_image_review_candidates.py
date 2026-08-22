"""Download auditable candidate archives and contact sheets without AI calls.

This script is intentionally separate from the background selector.  It lets
visual reviewers inspect numbered source images without spending vision-model
quota, and keeps the choices bound to one offer_id and record hash.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.image_selector import download_candidates  # noqa: E402
from src.local_store import LocalStore  # noqa: E402
from src.runtime_config import load_runtime_config, resolve_project_path  # noqa: E402


def make_contact_sheet(candidates: List[Dict[str, Any]], output_path: Path) -> None:
    """Create a numbered 3x4 review sheet without altering candidate originals."""
    tile_w, tile_h, label_h = 240, 240, 32
    columns, rows = 3, 4
    canvas = Image.new("RGB", (columns * tile_w, rows * (tile_h + label_h)), "#f4f4f4")
    draw = ImageDraw.Draw(canvas)
    for position, candidate in enumerate(candidates[: columns * rows]):
        col, row = position % columns, position // columns
        x, y = col * tile_w, row * (tile_h + label_h)
        with Image.open(candidate["temp_path"]) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((tile_w - 12, tile_h - 12), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (tile_w, tile_h), "white")
        tile.paste(image, ((tile_w - image.width) // 2, (tile_h - image.height) // 2))
        canvas.paste(tile, (x, y))
        draw.rectangle((x, y + tile_h, x + tile_w, y + tile_h + label_h), fill="#1f2937")
        draw.text((x + 10, y + tile_h + 8), f"Candidate {candidate['candidate_index']}", fill="white")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "JPEG", quality=88, optimize=True)


def archive_one(product: Dict[str, Any], cfg: Dict[str, Any], root: Path) -> Dict[str, Any]:
    offer_id = str(product["offer_id"])
    version = str(product["record_hash"])[:16]
    folder = root / offer_id / version
    manifest_path = folder / "manifest.json"
    review_path = folder / "review-sheet.jpg"
    if manifest_path.exists() and review_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {"offer_id": offer_id, "status": "existing", "candidate_count": len(data.get("candidates") or [])}
    if folder.exists():
        for path in folder.glob("*.jpg"):
            path.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    candidates = download_candidates(list(product["record"].get("image_urls") or []), folder, cfg)
    make_contact_sheet(candidates, review_path)
    manifest = {
        "offer_id": offer_id,
        "record_hash": product["record_hash"],
        "source_url": product["record"].get("url") or "",
        "candidate_count": len(candidates),
        "candidates": [
            {
                "index": item["candidate_index"],
                "source_url": item["source_url"],
                "path": item["temp_path"],
                "width": item["width"],
                "height": item["height"],
                "sha256": item["sha256"],
            }
            for item in candidates
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"offer_id": offer_id, "status": "downloaded", "candidate_count": len(candidates), "review_sheet": str(review_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Download numbered candidate archives for visual review without AI calls")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "work" / "image-review-archive")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--offer-id", action="append", default=[])
    args = parser.parse_args()
    if args.limit < 0 or args.workers < 1 or args.workers > 8:
        parser.error("--limit must be non-negative and --workers must be 1..8")

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = load_runtime_config(args.config)
    db_path = args.db.resolve() if args.db else resolve_project_path(
        str(cfg.get("relay", {}).get("database") or ""), "data/catalog.db"
    )
    products = LocalStore(db_path).product_snapshots_for_review(limit=args.limit, offer_ids=args.offer_id)
    if not products:
        raise SystemExit("no product snapshots matched")

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="catalog-archive") as executor:
        future_map = {executor.submit(archive_one, product, cfg, args.out_dir.resolve()): product["offer_id"] for product in products}
        for future in as_completed(future_map):
            offer_id = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:  # per-product failure should not stop the archive
                results.append({"offer_id": offer_id, "status": "failed", "error": str(exc)[:500]})

    results.sort(key=lambda item: item["offer_id"])
    summary = {
        "total": len(results),
        "downloaded": sum(item["status"] == "downloaded" for item in results),
        "existing": sum(item["status"] == "existing" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "results": results,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "archive-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("total", "downloaded", "existing", "failed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
