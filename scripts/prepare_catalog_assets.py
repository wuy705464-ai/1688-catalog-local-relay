"""Build neutral three-image catalog strips from approved English catalog JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageOps


def fit_tile(image_path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail(size, Image.Resampling.LANCZOS)
    tile = Image.new("RGB", size, "white")
    tile.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return tile


def make_strip(images: List[Path], output: Path) -> None:
    if len(images) != 3:
        raise ValueError("exactly three approved images are required")
    tile_size = (300, 450)
    canvas = Image.new("RGB", (900, 450), "white")
    for index, image_path in enumerate(images):
        canvas.paste(fit_tile(image_path, tile_size), (index * tile_size[0], 0))
        if index:
            for y in range(canvas.height):
                canvas.putpixel((index * tile_size[0], y), (225, 225, 225))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, "JPEG", quality=88, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create image strips for a prepared English customer catalog")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list) or not products:
        raise SystemExit("input has no prepared products")
    out_rows: List[Dict[str, Any]] = []
    for number, product in enumerate(products, start=1):
        offer_id = str(product["offer_id"])
        record_hash = str(product["record_hash"])
        images = list(product.get("images") or [])
        if [int(item.get("rank") or 0) for item in images] != [1, 2, 3]:
            raise ValueError(f"{offer_id}: image ranks must be 1..3")
        image_paths = [Path(str(item["path"])).resolve() for item in images]
        if not all(path.exists() for path in image_paths):
            raise FileNotFoundError(f"{offer_id}: an approved image file is missing")
        strip_path = args.asset_dir.resolve() / f"{offer_id}_{record_hash[:16]}.jpg"
        make_strip(image_paths, strip_path)
        out_rows.append({**product, "number": number, "collage_path": str(strip_path)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"catalog rows: {len(out_rows)}")


if __name__ == "__main__":
    main()
