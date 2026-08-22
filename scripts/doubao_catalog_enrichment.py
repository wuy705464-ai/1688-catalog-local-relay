"""Use the configured visual model only for ambiguous catalog-category enrichment.

The script never prints credentials.  It accepts a local env file path at run
time and outputs a small auditable offer-id keyed JSON map.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Tuple

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.image_selector import _ai_preview_data_url, _parse_json_object  # noqa: E402
from src.runtime_config import load_runtime_config  # noqa: E402


ALLOWED_CATEGORIES = {
    "Rings", "Necklaces & Pendants", "Bracelets", "Bangles & Bracelets", "Anklets",
    "Earrings", "Brooches", "Charms & Pendants", "Hair Accessories",
    "Beading Components", "Jewelry Sets", "Fashion Jewelry",
}


def load_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        # Human-maintained .env files often add a Chinese explanatory comment
        # after a value.  Keep it out of HTTP headers while preserving any
        # literal # that is part of an unquoted token.
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        # This explicit file is user-authorised for the run and must take
        # precedence over a stale placeholder inherited by the terminal.
        os.environ[key.strip()] = value.strip('"').strip("'")


def call_visual(row: Dict[str, Any], endpoint: str, api_key: str, model: str) -> Tuple[str, Dict[str, str]]:
    offer_id = str(row["offer_id"])
    prompt = f"""You are classifying one jewelry product for a customer catalog. Use the white-background product image only.
Offer ID: {offer_id}
Choose exactly one category from: {", ".join(sorted(ALLOWED_CATEGORIES))}.
Give 1-3 plausible visible finishes/colors in English. Do not invent measurements; for size return an empty string unless a clear dimension is visible.
Return JSON only: {{"offer_id":"{offer_id}","category":"...","color":"Gold / Silver","size":""}}"""
    response = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.0,
            "max_tokens": 220,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _ai_preview_data_url(Path(row["images"][0]["path"]))}},
            ]}],
        },
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}")
    content: Any = response.json()["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    data = _parse_json_object(str(content))
    if str(data.get("offer_id")) != offer_id:
        raise ValueError("offer ID mismatch")
    category = str(data.get("category") or "Fashion Jewelry")
    if category not in ALLOWED_CATEGORIES:
        category = "Fashion Jewelry"
    color = re.sub(r"[^A-Za-z /&-]", "", str(data.get("color") or "")).strip(" /-")
    size = re.sub(r"[^0-9A-Za-z .+×x–-]", "", str(data.get("size") or "")).strip()
    return offer_id, {"category": category, "color": color, "size": size}


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify ambiguous catalog rows with the configured visual model")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    load_env(args.env)
    cfg = load_runtime_config()
    vision = cfg.get("vision", {})
    api_key = os.getenv(str(vision.get("api_key_env") or "ARK_API_KEY"), "")
    base_url = os.getenv(str(vision.get("base_url_env") or "ARK_BASE_URL"), "").rstrip("/")
    env_model = os.getenv(str(vision.get("model_env") or "ARK_MODEL"), "").strip()
    model = str(vision.get("model") or "") if env_model.lower().startswith("api-key-") else (env_model or str(vision.get("model") or ""))
    if not api_key or not base_url or not model:
        raise SystemExit("configured visual API is unavailable")
    endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    rows = [row for row in json.loads(args.input.read_text(encoding="utf-8")) if row.get("category_en") == "Fashion Jewelry"]
    if args.limit:
        rows = rows[: args.limit]
    results: Dict[str, Dict[str, str]] = {}
    errors: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(8, args.workers))) as pool:
        future_map = {pool.submit(call_visual, row, endpoint, api_key, model): str(row["offer_id"]) for row in rows}
        for future in as_completed(future_map):
            offer_id = future_map[future]
            try:
                key, result = future.result()
                results[key] = result
            except Exception as exc:  # retain local fallback rather than blocking delivery
                errors[offer_id] = str(exc)[:240]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"results": results, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("classified", len(results), "errors", len(errors))


if __name__ == "__main__":
    main()
