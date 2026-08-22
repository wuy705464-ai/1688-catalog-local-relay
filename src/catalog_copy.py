"""Create concise, source-grounded English catalog copy from saved 1688 fields.

The scraper's raw ``specs`` object may contain DOM fragments.  This module only
uses exact, recognised field labels and a controlled jewellery vocabulary.  It
therefore produces customer-facing English copy rather than word-for-word
machine translation of noisy page text.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple


CATEGORY_MAP = {
    "项链": "Necklaces",
    "戒指": "Rings",
    "手链": "Bracelets",
    "脚链": "Anklets",
    "耳环": "Earrings",
    "胸针": "Brooches",
    "手镯": "Bangles",
    "首饰套装": "Jewelry Sets",
    "发饰": "Hair Accessories",
    "串珠配件": "Beading Components",
    "饰品配饰": "Jewelry & Accessories",
    "未分类": "Jewelry & Accessories",
}

MATERIAL_MAP = {
    "304不锈钢": "304 Stainless Steel",
    "316不锈钢": "316 Stainless Steel",
    "不锈钢": "Stainless Steel",
    "钛钢": "Titanium Steel",
    "锌合金": "Zinc Alloy",
    "合金": "Alloy",
    "铜": "Copper",
    "铁": "Iron",
    "铝": "Aluminum",
    "亚克力": "Acrylic",
    "树脂": "Resin",
    "玻璃/琉璃": "Glass",
    "玻璃": "Glass",
    "皮质": "Leather",
    "PU": "PU Leather",
    "珍珠": "Pearl",
    "贝壳": "Shell",
    "宝石": "Stone",
    "水晶": "Crystal",
}

COLOR_MAP = {
    "金色": "Gold",
    "银色": "Silver",
    "玫瑰金": "Rose Gold",
    "黑色": "Black",
    "白色": "White",
    "红色": "Red",
    "蓝色": "Blue",
    "绿色": "Green",
    "粉色": "Pink",
    "紫色": "Purple",
    "黄色": "Yellow",
    "橙色": "Orange",
    "咖啡色": "Brown",
    "棕色": "Brown",
    "灰色": "Grey",
    "透明": "Clear",
}

STYLE_MAP = {
    "个性风潮": "statement",
    "个性风": "statement",
    "个性": "statement",
    "时尚通勤": "modern everyday",
    "简约": "minimalist",
    "极简": "minimalist",
    "清新甜美": "sweet",
    "文艺复古": "vintage-inspired",
    "欧美": "Western-inspired",
    "优雅": "elegant",
    "哥特": "Gothic-inspired",
    "运动": "sporty",
    "甜美": "sweet",
}

SHAPE_MAP = {
    "几何": "geometric",
    "动物/生肖": "animal-inspired",
    "动物": "animal-inspired",
    "心形": "heart-shaped",
    "字母/数字/文字": "letter and number",
    "花朵": "floral",
    "圆形": "round",
    "星星": "star-shaped",
    "蝴蝶": "butterfly",
}

PROCESS_MAP = {
    "电镀": "electroplated",
    "抛光": "polished",
    "手工": "hand-finished",
    "镶嵌": "inlaid",
    "镶钻": "rhinestone-accented",
    "镶宝石": "stone-accented",
    "烤漆": "enamel-coated",
    "编织": "woven",
}

TYPE_LABELS = ("种类", "产品类型", "类别")
MATERIAL_LABELS = ("材质", "吊坠材质", "耳针材质", "链子材质", "链条材质")
COLOR_LABELS = ("颜色", "颜色分类", "色彩")
STYLE_LABELS = ("风格", "风格分类")
SHAPE_LABELS = ("造型", "形状")
PROCESS_LABELS = ("处理工艺", "工艺")
SIZE_LABELS = ("链长", "链条尺寸", "周长", "吊坠尺寸", "尺寸")
WEIGHT_LABELS = ("重量",)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _exact_spec(specs: Dict[str, Any], labels: Iterable[str]) -> str:
    for label in labels:
        value = _text(specs.get(label))
        if value:
            return value
    return ""


def _mapped(value: str, mapping: Dict[str, str]) -> str:
    value = _text(value)
    if not value:
        return ""
    for source in sorted(mapping, key=len, reverse=True):
        if source in value:
            return mapping[source]
    return ""


def _translate_colors(value: str) -> str:
    value = _text(value)
    if not value or "款式" in value or value in {"其他", "混色", "随机"}:
        return ""
    matches: List[str] = []
    for source in sorted(COLOR_MAP, key=len, reverse=True):
        if source in value and COLOR_MAP[source] not in matches:
            matches.append(COLOR_MAP[source])
    return " / ".join(matches)


def _normalise_dimension(candidate: str) -> str:
    """Return only a compact dimension, never adjacent DOM/page text."""
    candidate = re.sub(r"(?i)(\d)\s*(cm|mm)\b", lambda m: f"{m.group(1)} {m.group(2).lower()}", candidate)
    pieces = re.split(r"\s*([+x×*~–-])\s*", candidate)
    units = [re.search(r"(?i)\b(cm|mm)\b", piece) for piece in pieces[::2]]
    fallback_unit = next((match.group(1).lower() for match in reversed(units) if match), "")
    normalised: List[str] = []
    for index, piece in enumerate(pieces):
        if index % 2:
            normalised.append({"x": " × ", "*": " × ", "+": " + ", "~": "–", "-": "–"}.get(piece, piece))
            continue
        piece = piece.strip()
        if piece and not re.search(r"(?i)\b(cm|mm)\b", piece):
            piece = f"{piece} {fallback_unit}".strip()
        normalised.append(piece)
    return "".join(normalised)


def _format_dimension(value: str) -> str:
    value = _text(value)
    if not value or value in {"均码", "无", "其他"}:
        return ""
    value = value.replace("（含）", "").replace("(含)", "")
    value = value.replace("厘米", " cm").replace("毫米", " mm")

    # The first form covers values such as ``41cm-50cm``.  The second covers
    # compact forms such as ``42+5cm`` and ``2.1*2.4cm`` where the unit is
    # supplied only once at the end.  Restricting the result to this match is
    # deliberate: the saved DOM sometimes appends unrelated page text.
    explicit = re.search(
        r"(?i)\d+(?:\.\d+)?\s*(?:cm|mm)(?:\s*[+x×*~–-]\s*\d+(?:\.\d+)?\s*(?:cm|mm)){0,2}",
        value,
    )
    compact = re.search(
        r"(?i)\d+(?:\.\d+)?(?:\s*[+x×*~–-]\s*\d+(?:\.\d+)?){1,2}\s*(?:cm|mm)",
        value,
    )
    candidates = [match.group(0) for match in (explicit, compact) if match]
    if not candidates:
        return ""
    return _normalise_dimension(max(candidates, key=len))


def _format_weight(value: str) -> str:
    value = _text(value)
    if not value:
        return ""
    value = value.replace("克", " g")
    match = re.search(r"(?i)\d+(?:\.\d+)?\s*g\b", value)
    if not match:
        return ""
    return re.sub(r"(?i)(\d)\s*g\b", r"\1 g", match.group(0))


def _english_category(category: str, product_type: str) -> str:
    product_type = _text(product_type)
    if product_type in CATEGORY_MAP:
        return CATEGORY_MAP[product_type]
    category = _text(category)
    return CATEGORY_MAP.get(category, "Jewelry & Accessories")


def customer_price_display(value: Any) -> str:
    """Keep a source price readable to overseas buyers without Chinese notes."""
    price = _text(value)
    if not price:
        return ""
    price = price.replace("（阶梯价）", "(Tiered Price)")
    price = price.replace("(阶梯价)", "(Tiered Price)")
    price = price.replace("阶梯价", "Tiered Price")
    return price


def english_catalog_fields(record: Dict[str, Any], category: str = "", size_raw: str = "") -> Dict[str, Any]:
    """Return English fields plus raw evidence for one immutable product record."""
    specs = record.get("specs") if isinstance(record.get("specs"), dict) else {}
    product_type = _exact_spec(specs, TYPE_LABELS)
    material_raw = _exact_spec(specs, MATERIAL_LABELS)
    color_raw = _exact_spec(specs, COLOR_LABELS)
    style_raw = _exact_spec(specs, STYLE_LABELS)
    shape_raw = _exact_spec(specs, SHAPE_LABELS)
    process_raw = _exact_spec(specs, PROCESS_LABELS)
    size_value = _text(size_raw) or _exact_spec(specs, SIZE_LABELS)
    weight_raw = _exact_spec(specs, WEIGHT_LABELS)

    material = _mapped(material_raw, MATERIAL_MAP)
    color = _translate_colors(color_raw)
    style = _mapped(style_raw, STYLE_MAP)
    shape = _mapped(shape_raw, SHAPE_MAP)
    process = _mapped(process_raw, PROCESS_MAP)
    size = _format_dimension(size_value)
    weight = _format_weight(weight_raw)

    features: List[str] = []
    if shape:
        features.append(f"{shape.capitalize()} silhouette")
    if style:
        features.append(f"{style.capitalize()} style")
    if process:
        if process == "hand-finished":
            features.append("Hand-finished detail")
        else:
            features.append(f"{process.capitalize()} finish")
    if size and ("cm" in size.lower() or "mm" in size.lower()):
        features.append(f"Size: {size}")
    if weight and "g" in weight.lower():
        features.append(f"Approx. {weight}")
    if not features:
        features.append("Fashion jewelry with a clean, export-ready presentation")

    evidence = {
        "type": product_type,
        "material": material_raw,
        "color": color_raw,
        "style": style_raw,
        "shape": shape_raw,
        "process": process_raw,
        "size": size_value,
        "weight": weight_raw,
    }
    missing = [name for name, value in (("material", material), ("color", color), ("size", size)) if not value]
    return {
        "category_en": _english_category(category or _text(record.get("category")), product_type),
        "key_features_en": ". ".join(features) + ".",
        "material_en": material,
        "size_en": size,
        "weight_en": weight,
        "color_en": color,
        "evidence": evidence,
        "missing": missing,
    }
