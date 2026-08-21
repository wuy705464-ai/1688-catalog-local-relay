"""One paid/live smoke test for the configured Doubao visual endpoint.

The script never prints credentials. It creates eight synthetic candidate images,
asks the configured endpoint to select three, validates the response, then removes
the generated fixtures.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.image_selector import select_with_doubao  # noqa: E402
from src.runtime_config import load_env_file, load_runtime_config  # noqa: E402


def draw_candidates(folder: Path):
    folder.mkdir(parents=True)
    candidates = []
    for index in range(1, 9):
        image = Image.new("RGB", (640, 640), "white")
        draw = ImageDraw.Draw(image)
        if index == 1:
            draw.arc((120, 70, 520, 500), 20, 160, fill="black", width=8)
            draw.ellipse((265, 345, 375, 455), fill=(190, 20, 35), outline="black", width=5)
        elif index == 2:
            draw.ellipse((150, 150, 490, 490), fill=(190, 20, 35), outline="black", width=10)
            draw.ellipse((280, 280, 360, 360), fill="white")
        elif index == 3:
            draw.ellipse((210, 60, 430, 280), fill=(235, 205, 180), outline="black", width=4)
            draw.line((220, 270, 160, 600), fill="black", width=5)
            draw.line((420, 270, 480, 600), fill="black", width=5)
            draw.ellipse((285, 360, 355, 430), fill=(190, 20, 35), outline="black", width=4)
        elif index == 4:
            draw.polygon([(80, 420), (460, 420), (570, 520), (150, 560)], fill=(30, 90, 210), outline="black")
        elif index == 5:
            draw.rectangle((80, 180, 560, 460), fill=(255, 230, 0), outline="red", width=12)
            draw.text((230, 290), "SALE", fill="red")
        elif index == 6:
            draw.rectangle((140, 160, 500, 500), fill=(160, 100, 50), outline="black", width=8)
        elif index == 7:
            draw.arc((120, 70, 520, 500), 20, 160, fill="black", width=8)
            draw.ellipse((265, 345, 375, 455), fill=(190, 20, 35), outline="black", width=5)
        else:
            draw.ellipse((150, 120, 280, 250), outline="black", width=12)
            draw.ellipse((360, 120, 490, 250), outline="black", width=12)
        path = folder / f"{index:02d}.jpg"
        image.save(path, "JPEG", quality=90)
        candidates.append(
            {
                "candidate_index": index,
                "source_url": f"https://img.alicdn.com/live-test/{index}.jpg",
                "temp_path": str(path),
                "width": 640,
                "height": 640,
                "sha256": f"live-test-{index}",
                "dhash": index,
            }
        )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    load_env_file(args.env_file, override=True)
    cfg = load_runtime_config()
    runtime_root = ROOT / "tests" / "_runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    folder = runtime_root / f"doubao-live-{uuid.uuid4().hex}"
    try:
        candidates = draw_candidates(folder)
        record = {
            "offer_id": "960000000001",
            "title": "红色圆形吊坠项链",
            "category": "项链",
            "specs": {"颜色": "红色", "款式": "圆形吊坠", "材质": "金属"},
        }
        selected, method = select_with_doubao(record, candidates, 3, cfg)
        if not selected or len(selected) != 3:
            raise SystemExit("live Doubao test failed: expected exactly three selections")
        print("live-doubao-ok")
        print("method:", method)
        print("selected:", [(item["index"], item["role"]) for item in selected])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
