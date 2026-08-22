"""Persistent image worker with auditable candidate retention.

Each product keeps its downloaded candidates in a versioned directory and
copies exactly three approved images to ``products/selected``.  Rank 1 is a
white-background product image.  Ranks 2-3 prioritize genuine on-person/worn
views, but use clean complementary product views when the source gallery has
no further worn images.  Offer ID and record hash are checked before any result
is committed, so an asynchronous job cannot attach images to another product.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, ImageOps

try:
    from .local_store import LocalStore
    from .runtime_config import PROJECT_ROOT
except ImportError:  # pragma: no cover - direct script compatibility
    from local_store import LocalStore
    from runtime_config import PROJECT_ROOT


logger = logging.getLogger("image_selector")
WHITE_BACKGROUND_ROLE = "white_background_product"
WEARING_ROLE = "worn_on_person"
SUPPLEMENTARY_ROLE = "supplementary_product_view"
ALLOWED_ROLES = {WHITE_BACKGROUND_ROLE, WEARING_ROLE, SUPPLEMENTARY_ROLE}


class NeedsHumanVisualReview(RuntimeError):
    """The source gallery cannot satisfy the customer's mandatory image mix."""


def _host_allowed(url: str, allowed_suffixes: List[str]) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == suffix.lower() or host.endswith("." + suffix.lower()) for suffix in allowed_suffixes)


def _download_response(
    session: requests.Session,
    url: str,
    allowed_hosts: List[str],
    timeout: int,
) -> requests.Response:
    current = url
    for _ in range(4):
        if not _host_allowed(current, allowed_hosts):
            raise ValueError(f"image host not allowed: {urlparse(current).hostname}")
        response = session.get(
            current,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
                "Referer": "https://detail.1688.com/",
            },
            timeout=timeout,
            stream=True,
            allow_redirects=False,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("redirect without Location")
            current = urljoin(current, location)
            continue
        return response
    raise ValueError("too many image redirects")


def _dhash(img: Image.Image) -> int:
    gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | int(pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return value


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def download_candidates(
    image_urls: List[str],
    temp_dir: Path,
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    selection_cfg = cfg.get("image_selection", {})
    wanted = int(selection_cfg.get("candidate_count", 8))
    min_width = int(selection_cfg.get("min_width", 320))
    min_height = int(selection_cfg.get("min_height", 320))
    timeout = int(selection_cfg.get("request_timeout_seconds", 25))
    max_bytes = int(selection_cfg.get("max_download_mb", 15)) * 1024 * 1024
    allowed_hosts = list(selection_cfg.get("allowed_image_hosts") or ["alicdn.com", "1688.com"])

    temp_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    candidates: List[Dict[str, Any]] = []
    seen_sha = set()
    for source_url in image_urls:
        if len(candidates) >= wanted:
            break
        try:
            response = _download_response(session, source_url, allowed_hosts, timeout)
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type and not content_type.startswith("image/"):
                raise ValueError(f"unexpected content type {content_type}")
            chunks: List[bytes] = []
            size = 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("image exceeds configured size limit")
                chunks.append(chunk)
            response.close()
            data = b"".join(chunks)
            sha = hashlib.sha256(data).hexdigest()
            if sha in seen_sha:
                continue
            with Image.open(BytesIO(data)) as opened:
                img = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = img.size
            if width < min_width or height < min_height:
                raise ValueError(f"image too small: {width}x{height}")
            seen_sha.add(sha)
            candidate_index = len(candidates) + 1
            path = temp_dir / f"{candidate_index:02d}.jpg"
            img.save(path, "JPEG", quality=92, optimize=True)
            candidates.append(
                {
                    "candidate_index": candidate_index,
                    "source_url": source_url,
                    "temp_path": str(path.resolve()),
                    "width": width,
                    "height": height,
                    "sha256": sha,
                    "dhash": _dhash(img),
                }
            )
        except Exception as exc:
            logger.warning("candidate download skipped: %s (%s)", source_url[:120], exc)
    return candidates


def _ai_preview_data_url(path: Path) -> str:
    with Image.open(path) as opened:
        img = ImageOps.exif_transpose(opened).convert("RGB")
        img.thumbnail((768, 768), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, "JPEG", quality=78, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", (text or "").strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError("AI response did not contain JSON")
    return json.loads(match.group(0))


def _validate_role_order(selection: List[Dict[str, Any]], selected_count: int) -> None:
    """Keep a white-background cover mandatory without blocking sparse galleries."""
    if selected_count != 3:
        raise ValueError("customer catalog requires exactly three image slots")
    roles = tuple(str(item.get("role") or "") for item in selection)
    if len(roles) != 3 or roles[0] != WHITE_BACKGROUND_ROLE or any(
        role not in {WEARING_ROLE, SUPPLEMENTARY_ROLE} for role in roles[1:]
    ):
        raise ValueError(
            "image role contract not met: rank 1 must be white-background product; "
            "ranks 2-3 must be worn/on-person or clean supplementary product views"
        )


def select_with_doubao(
    record: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    selected_count: int,
    cfg: Dict[str, Any],
) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    vision_cfg = cfg.get("vision", {})
    api_key = os.getenv(str(vision_cfg.get("api_key_env") or "ARK_API_KEY"), "")
    base_url = os.getenv(str(vision_cfg.get("base_url_env") or "ARK_BASE_URL"), "").rstrip("/")
    env_model = os.getenv(str(vision_cfg.get("model_env") or "ARK_MODEL"), "").strip()
    fallback_model = str(vision_cfg.get("model") or "").strip()
    # A common console-copy mistake is to paste the API-key display name
    # (api-key-YYYY...) into ARK_MODEL. It is not a callable Model/Endpoint ID.
    # Ignore only that unambiguous label form; valid model IDs and ep-* endpoint
    # IDs continue to override the fallback.
    if env_model.lower().startswith("api-key-"):
        logger.warning("ARK_MODEL contains an API-key label; using configured model fallback")
        model = fallback_model
    else:
        model = env_model or fallback_model
    if not api_key or not base_url or not model:
        return None, "doubao environment variables are not configured"

    specs = record.get("specs") if isinstance(record.get("specs"), dict) else {}
    prompt = f"""你是外贸产品目录的严格选图审核员。所有信息属于同一个 1688 商品，但候选图中可能混入推荐商品、尺寸图或重复图。

商品 offer_id：{record['offer_id']}
标题：{record.get('title', '')}
分类：{record.get('category', '')}
规格：{json.dumps(specs, ensure_ascii=False)[:4000]}

    请从下面 {len(candidates)} 张候选图中选择恰好 {selected_count} 张，并严格按顺序返回：
    1. 第 1 张：必须是同一商品的纯白或近纯白背景产品展示图；产品清晰、完整、无人物、无拼图、无文字促销。
    2. 第 2、3 张：优先选择同一商品被真人实际佩戴/穿戴的图片，能看见产品在人体相应部位；有一张或两张合格穿戴图都应优先放入。若候选中没有更多合格穿戴图，选择清晰、非白底、不同角度的产品展示图补足，不能用二维码、尺寸图、纯文字促销图或包装图。
    3. 所有图片都必须与该商品一致；排除推荐商品、重复图、低清图、二维码、尺寸图和纯文字促销图。
    4. 只有在找不到第 1 张合格白底图时，才返回 {{"offer_id":"{record['offer_id']}","selected":[],"reason":"no qualified white-background product image"}}。
    5. 只返回 JSON，不要 Markdown。合格时格式：
    {{"offer_id":"{record['offer_id']}","selected":[{{"index":1,"role":"white_background_product","reason":"brief reason"}},{{"index":2,"role":"worn_on_person","reason":"brief reason"}},{{"index":3,"role":"supplementary_product_view","reason":"brief reason"}}]}}
    role 只能是 white_background_product、worn_on_person 或 supplementary_product_view。
"""
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for candidate in candidates:
        content.append({"type": "text", "text": f"候选图 {candidate['candidate_index']}"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _ai_preview_data_url(Path(candidate["temp_path"]))},
            }
        )

    endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    response = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
            "max_tokens": 900,
        },
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Doubao API HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    message_content = payload["choices"][0]["message"]["content"]
    if isinstance(message_content, list):
        message_content = "".join(str(part.get("text") or "") for part in message_content if isinstance(part, dict))
    data = _parse_json_object(str(message_content))
    if str(data.get("offer_id")) != str(record["offer_id"]):
        raise ValueError("AI returned a different offer_id")
    selected = data.get("selected")
    if selected == []:
        reason = str(data.get("reason") or "insufficient qualified images")[:500]
        raise NeedsHumanVisualReview(reason)
    if not isinstance(selected, list) or len(selected) != selected_count:
        raise ValueError(f"AI must select exactly {selected_count} images")
    valid_indices = {int(c["candidate_index"]) for c in candidates}
    seen = set()
    cleaned: List[Dict[str, Any]] = []
    for rank, item in enumerate(selected, start=1):
        index = int(item.get("index") or 0)
        role = str(item.get("role") or "")
        if index not in valid_indices or index in seen:
            raise ValueError("AI returned invalid or duplicate image index")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"AI returned unsupported image role: {role or '<empty>'}")
        seen.add(index)
        cleaned.append(
            {
                "index": index,
                "rank": rank,
                "role": role,
                "reason": str(item.get("reason") or "AI selected")[:1000],
            }
        )
    _validate_role_order(cleaned, selected_count)
    return cleaned, "doubao"


def heuristic_selection(candidates: List[Dict[str, Any]], selected_count: int) -> List[Dict[str, Any]]:
    chosen = [candidates[0]]
    remaining = candidates[1:]
    while remaining and len(chosen) < selected_count:
        next_item = max(
            remaining,
            key=lambda item: min(_hamming(int(item["dhash"]), int(existing["dhash"])) for existing in chosen),
        )
        chosen.append(next_item)
        remaining.remove(next_item)
    # This fallback is retained for local debugging only; normal catalog runs
    # require visual-model approval for the white-background cover image.
    roles = [WHITE_BACKGROUND_ROLE, SUPPLEMENTARY_ROLE, SUPPLEMENTARY_ROLE]
    return [
        {
            "index": int(item["candidate_index"]),
            "rank": rank,
            "role": roles[min(rank - 1, len(roles) - 1)],
            "reason": "AI unavailable; selected by source order and visual diversity",
        }
        for rank, item in enumerate(chosen, start=1)
    ]


def _remove_tree_safely(path: Path, allowed_root: Path) -> None:
    path = path.resolve()
    allowed_root = allowed_root.resolve()
    if path == allowed_root or allowed_root not in path.parents:
        raise ValueError(f"refusing to remove path outside worker root: {path}")
    if path.exists():
        shutil.rmtree(path)


def process_one_job(store: LocalStore, cfg: Dict[str, Any], worker_id: str) -> bool:
    job = store.claim_next_job(worker_id)
    if not job:
        return False
    offer_id = job["offer_id"]
    version_hash = job["version_hash"]
    record = job["record"]
    store.mark_processing(offer_id, version_hash)

    selection_cfg = cfg.get("image_selection", {})
    selected_count = int(selection_cfg.get("selected_count", 3))
    max_attempts = int(cfg.get("relay", {}).get("worker_max_attempts", 3))
    candidate_root = (PROJECT_ROOT / "products" / "candidates").resolve()
    selected_root = (PROJECT_ROOT / "products" / "selected").resolve()
    version_prefix = version_hash[:16]
    temp_dir = candidate_root / offer_id / version_prefix
    final_dir = selected_root / offer_id / version_prefix

    try:
        if temp_dir.exists():
            _remove_tree_safely(temp_dir, candidate_root)
        candidates = download_candidates(record.get("image_urls") or [], temp_dir, cfg)
        if len(candidates) < selected_count:
            raise ValueError(f"only {len(candidates)} valid candidate images; need {selected_count}")

        try:
            selection, method = select_with_doubao(record, candidates, selected_count, cfg)
        except NeedsHumanVisualReview:
            # A valid negative visual verdict is not an API failure and must
            # never be retried automatically: every retry spends quota but
            # cannot create absent white-background/worn images.
            raise
        except Exception as exc:
            logger.warning("Doubao selection failed for %s: %s", offer_id, exc)
            if bool(selection_cfg.get("require_ai", True)):
                raise RuntimeError(f"Doubao selection is required: {exc}") from exc
            selection, method = None, f"heuristic_fallback:{type(exc).__name__}"
        if not selection:
            if bool(selection_cfg.get("require_ai", True)):
                raise RuntimeError("Doubao selection is required but no AI result was returned")
            selection = heuristic_selection(candidates, selected_count)
            method = "heuristic_fallback:no_ai_config"
        if len(selection) != selected_count:
            raise ValueError("selection did not produce the required number of images")
        _validate_role_order(selection, selected_count)

        if final_dir.exists():
            _remove_tree_safely(final_dir, selected_root)
        final_dir.mkdir(parents=True, exist_ok=True)
        selection_by_index = {int(item["index"]): item for item in selection}
        db_candidates: List[Dict[str, Any]] = []
        for candidate in candidates:
            index = int(candidate["candidate_index"])
            selected_meta = selection_by_index.get(index)
            # Candidates remain on disk when delete_unselected=false, allowing a
            # human to audit/reselect without returning to the product page.
            local_path = str(Path(candidate["temp_path"]).resolve())
            if selected_meta:
                destination = final_dir / f"{int(selected_meta['rank']):02d}.jpg"
                shutil.copy2(candidate["temp_path"], destination)
                local_path = str(destination.resolve())
            db_candidates.append(
                {
                    **candidate,
                    "is_selected": bool(selected_meta),
                    "selected_rank": int(selected_meta["rank"]) if selected_meta else None,
                    "role": selected_meta["role"] if selected_meta else "",
                    "reason": selected_meta["reason"] if selected_meta else "",
                    "local_path": local_path,
                }
            )

        committed = store.complete_selection(
            offer_id=offer_id,
            version_hash=version_hash,
            candidates=db_candidates,
            method=method,
            required_selected=selected_count,
        )
        if not committed:
            _remove_tree_safely(final_dir, selected_root)
            logger.info("discarded stale image result for %s", offer_id)
            return True

        offer_root = selected_root / offer_id
        for child in offer_root.iterdir():
            if child.is_dir() and child.name != version_prefix:
                _remove_tree_safely(child, selected_root)
        logger.info("selected %s images for offer %s via %s", selected_count, offer_id, method)
        return True
    except NeedsHumanVisualReview as exc:
        store.fail_job(offer_id, version_hash, f"manual visual review required: {exc}", max_attempts=1)
        logger.info("image job requires manual visual review for %s: %s", offer_id, exc)
        return True
    except Exception as exc:
        store.fail_job(offer_id, version_hash, str(exc), max_attempts)
        logger.exception("image job failed for %s", offer_id)
        return True
    finally:
        if temp_dir.exists() and bool(selection_cfg.get("delete_unselected", True)):
            _remove_tree_safely(temp_dir, candidate_root)
            try:
                temp_dir.parent.rmdir()
            except OSError:
                pass


class ImageWorker:
    def __init__(self, store: LocalStore, cfg: Dict[str, Any]):
        self.store = store
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.threads: List[threading.Thread] = []

    def start(self) -> None:
        if any(thread.is_alive() for thread in self.threads):
            return
        count = max(1, min(8, int(self.cfg.get("relay", {}).get("worker_count", 1))))
        self.stop_event.clear()
        self.threads = []
        for index in range(count):
            worker_id = f"local-{uuid.uuid4().hex[:10]}"
            thread = threading.Thread(
                target=self._run,
                args=(worker_id,),
                name=f"catalog-image-worker-{index + 1}",
                daemon=True,
            )
            self.threads.append(thread)
            thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=timeout)

    def _run(self, worker_id: str) -> None:
        poll = float(self.cfg.get("relay", {}).get("worker_poll_seconds", 2.0))
        while not self.stop_event.is_set():
            worked = process_one_job(self.store, self.cfg, worker_id)
            if not worked:
                self.stop_event.wait(poll)
