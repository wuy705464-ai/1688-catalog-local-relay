"""Prepare an English, boss-template-ready catalog from approved product rows.

This script keeps every row bound to the stored offer ID, record hash, and
approved image strip.  Text is source-led where available; only the requested
color and size fallbacks are inferred for a customer-facing catalog when the
source omits those fields.
"""
from __future__ import annotations

import argparse
import colorsys
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.catalog_copy import customer_price_display, english_catalog_fields  # noqa: E402


def text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def first_spec(specs: Dict[str, Any], labels: Iterable[str]) -> str:
    for label in labels:
        value = text(specs.get(label))
        if value:
            return value
    return ""


def mapped(value: str, entries: Dict[str, str], fallback: str) -> str:
    value = text(value)
    for source in sorted(entries, key=len, reverse=True):
        if source in value:
            return entries[source]
    return fallback


def refined_category(source_category: str, product_type: str) -> str:
    haystack = f"{source_category} {product_type}"
    checks = [
        (("串珠", "配件"), "Beading Components"),
        (("发饰", "发夹", "发箍"), "Hair Accessories"),
        (("耳环", "耳饰", "耳针", "耳钉", "耳坠"), "Earrings"),
        (("戒指", "指环", "活口", "对戒"), "Rings"),
        (("脚链",), "Anklets"),
        (("手镯", "手环", "素圈"), "Bangles & Bracelets"),
        (("手链",), "Bracelets"),
        (("胸针",), "Brooches"),
        (("项链", "吊坠"), "Necklaces & Pendants"),
        (("挂件",), "Charms & Pendants"),
        (("套装",), "Jewelry Sets"),
    ]
    for words, category in checks:
        if any(word in haystack for word in words):
            return category
    return "Fashion Jewelry"


def product_type_en(product_type: str, category: str) -> str:
    return mapped(
        product_type,
        {
            "活口": "Adjustable Ring",
            "指环": "Ring",
            "对戒": "Couple Ring",
            "耳环": "Earrings",
            "手链": "Bracelet",
            "手镯": "Bangle",
            "项链": "Necklace",
            "吊坠": "Pendant Necklace",
            "挂件": "Charm Pendant",
            "胸针": "Brooch",
            "素圈": "Band Ring",
        },
        category.rstrip("s") if category not in {"Bangles & Bracelets", "Necklaces & Pendants", "Charms & Pendants"} else category,
    )


def visual_color(image_path: str) -> str:
    """Infer a conservative jewelry finish from the white-background cover."""
    try:
        with Image.open(image_path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((260, 260), Image.Resampling.LANCZOS)
            scores: Counter[str] = Counter()
            for red, green, blue in image.getdata():
                hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
                if value > 0.94 and saturation < 0.12:
                    continue
                if value < 0.28:
                    scores["Black"] += 1
                elif saturation < 0.18 and 0.35 < value < 0.92:
                    scores["Silver"] += 1
                elif 0.08 <= hue <= 0.17 and saturation >= 0.20 and value >= 0.30:
                    scores["Gold"] += 1
                elif (hue <= 0.04 or hue >= 0.94) and saturation >= 0.22 and value >= 0.35:
                    scores["Rose Gold"] += 1
            total = sum(scores.values())
            if not total:
                return "Gold / Silver"
            gold = scores["Gold"]
            silver = scores["Silver"]
            if gold > total * 0.12 and silver > total * 0.12:
                return "Gold / Silver"
            if max(scores.values()) < total * 0.16:
                return "Gold / Silver"
            return scores.most_common(1)[0][0]
    except (OSError, ValueError):
        return "Gold / Silver"


def standard_size(category: str) -> str:
    return {
        "Rings": "Adjustable / Standard Ring Size",
        "Necklaces & Pendants": "Approx. 40 + 5 cm",
        "Charms & Pendants": "Approx. 2–4 cm",
        "Bracelets": "Approx. 16 + 5 cm",
        "Bangles & Bracelets": "Approx. inner diameter 5.8–6.2 cm",
        "Anklets": "Approx. 21 + 5 cm",
        "Earrings": "One Size",
        "Brooches": "Approx. 3–5 cm",
        "Hair Accessories": "One Size",
        "Beading Components": "One Size",
        "Jewelry Sets": "Standard Set Size",
    }.get(category, "Standard Size")


def visual_color_en(value: str) -> str:
    value = text(value).lower()
    colors: List[str] = []
    for source, label in (
        ("rose gold", "Rose Gold"), ("antique bronze", "Antique Bronze"),
        ("gold", "Gold"), ("silver", "Silver"), ("black", "Black"),
        ("white", "White"), ("red", "Red"), ("blue", "Blue"),
        ("green", "Green"), ("purple", "Purple"), ("pink", "Pink"),
        ("orange", "Orange"), ("brown", "Brown"),
    ):
        if source in value and label not in colors:
            colors.append(label)
    return " / ".join(colors[:3])


def visual_size_en(value: str) -> str:
    value = text(value)
    if not value or not any(unit in value.lower() for unit in ("cm", "mm")):
        return ""
    value = value.replace("x", "×").replace("~", "–")
    value = value.replace("CM", "cm").replace("MM", "mm")
    return value[:40]


def audience_en(value: str) -> str:
    value = text(value)
    if "男女通用" in value:
        return "Unisex"
    out: List[str] = []
    if "女" in value:
        out.append("Women")
    if "男" in value:
        out.append("Men")
    if "儿童" in value:
        out.append("Children")
    if "情侣" in value:
        out.append("Couples")
    return " / ".join(out) or "Unisex"


def prepare_row(previous: Dict[str, Any], record: Dict[str, Any], category: str, vision: Dict[str, str]) -> Dict[str, Any]:
    specs = record.get("specs") if isinstance(record.get("specs"), dict) else {}
    source_type = first_spec(specs, ("种类", "产品类型", "类别"))
    item_no = first_spec(specs, ("货号", "型号", "款号"))
    if not item_no or item_no in {"/", "无"}:
        item_no = str(previous["offer_id"])
    copy = english_catalog_fields(record, category, previous.get("evidence", {}).get("size") or "")
    image_path = str(previous["images"][0]["path"])
    color = copy["color_en"] or visual_color_en(vision.get("color") or "") or visual_color(image_path)
    material = copy["material_en"] or "Mixed Materials"
    process = mapped(first_spec(specs, ("处理工艺", "工艺")), {
        "电镀": "Electroplated", "手工串珠": "Hand-strung", "手工": "Hand-finished",
        "抛光": "Polished", "镀金": "Gold-plated", "微镶": "Micro-pavé",
        "烤漆": "Enamel-coated", "雕刻": "Engraved", "镶宝石": "Stone-set",
    }, "Decorative finish")
    shape = mapped(first_spec(specs, ("造型", "形状")), {
        "几何": "Geometric", "心形": "Heart", "动物": "Animal-inspired", "蝴蝶": "Butterfly",
        "圆形": "Round", "星": "Star", "字母": "Letter", "皇冠": "Crown",
        "指环": "Band Ring", "叶子": "Leaf",
    }, "Contemporary")
    style = mapped(first_spec(specs, ("风格", "风格分类")), {
        "个性": "Statement", "甜美": "Sweet", "极简": "Minimalist", "简约": "Minimalist",
        "运动": "Sporty", "欧美": "Western-inspired", "哥特": "Gothic-inspired",
        "时尚OL": "Office chic", "复古": "Vintage-inspired", "百搭": "Versatile",
        "ins": "Modern", "度假": "Resort",
    }, "Modern")
    trendy = mapped(first_spec(specs, ("流行元素", "流行元素分类")), {
        "几何": "Geometric", "动物": "Animal", "字母": "Letter", "蝴蝶": "Butterfly",
        "数字": "Number", "爱心": "Heart", "十字架": "Cross", "圆圈": "Circle",
        "地图": "Map", "花": "Floral", "星": "Star", "皇冠": "Crown",
    }, shape)
    audience = audience_en(first_spec(specs, ("适用人群", "适用对象")))
    occasion = mapped(first_spec(specs, ("适用场景", "场景")), {
        "日常": "Everyday wear", "休闲": "Casual wear", "派对": "Party", "旅行": "Travel",
        "节日": "Festive gifting", "宴会": "Evening events", "商务": "Business casual", "婚礼": "Wedding",
    }, "Everyday wear")
    size = visual_size_en(vision.get("size") or "") or copy["size_en"] or standard_size(category)
    features = "\n".join([
        f"• Item No.: {item_no}",
        f"• Material: {material}",
        f"• Process: {process}",
        f"• Type: {product_type_en(source_type, category)}",
        f"• Shape: {shape}",
        f"• Color: {color}",
        f"• Style: {style}",
        f"• Trendy Element: {trendy}",
        f"• Suitable For: {audience}",
    ])
    return {
        "offer_id": str(previous["offer_id"]),
        "record_hash": str(previous["record_hash"]),
        "number": 0,
        "category_en": category,
        "sku": f"1688-{previous['offer_id']}",
        "price_display": customer_price_display(previous.get("price_display") or ""),
        "key_features_en": features,
        "material_en": material,
        "size_en": size,
        "weight_en": copy["weight_en"],
        "color_en": color,
        "style_en": style,
        "occasion_en": occasion,
        "item_no": item_no,
        "images": previous["images"],
        "collage_path": previous["collage_path"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare rows for the boss-provided customer catalog template")
    parser.add_argument("--input", type=Path, required=True, help="Approved prior catalog rows JSON")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "catalog.db")
    parser.add_argument("--vision", type=Path, default=None, help="Optional offer-id keyed visual enrichment JSON")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    previous_rows = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(previous_rows, list) or not previous_rows:
        raise SystemExit("input does not contain prepared catalog rows")
    vision_results: Dict[str, Dict[str, str]] = {}
    if args.vision and args.vision.exists():
        payload = json.loads(args.vision.read_text(encoding="utf-8"))
        vision_results = payload.get("results", {}) if isinstance(payload, dict) else {}
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    records = {
        str(row["offer_id"]): dict(row)
        for row in conn.execute("SELECT offer_id, category, raw_json FROM products")
    }
    conn.close()
    prepared: List[Dict[str, Any]] = []
    for prior in previous_rows:
        stored = records.get(str(prior["offer_id"]))
        if not stored:
            raise KeyError(f"missing database record for {prior['offer_id']}")
        record = json.loads(stored["raw_json"])
        product_type = first_spec(record.get("specs") or {}, ("种类", "产品类型", "类别"))
        category = refined_category(str(stored["category"] or ""), product_type)
        visual = vision_results.get(str(prior["offer_id"]), {})
        if category == "Fashion Jewelry" and visual.get("category"):
            category = str(visual["category"])
        prepared.append(prepare_row(prior, record, category, visual))
    prepared.sort(key=lambda item: (item["category_en"], item["item_no"], item["offer_id"]))
    for number, item in enumerate(prepared, start=1):
        item["number"] = number
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")
    print("prepared", len(prepared))
    print("categories", dict(sorted(Counter(item["category_en"] for item in prepared).items())))
    print("color_filled", sum(bool(item["color_en"]) for item in prepared))
    print("size_filled", sum(bool(item["size_en"]) for item in prepared))


if __name__ == "__main__":
    main()
