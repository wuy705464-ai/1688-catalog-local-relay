"""Apply independently reviewed image choices with product/version safeguards.

Input is a JSON list.  Each review must provide one white-background candidate
and two approved secondary views from that product's archived manifest.
The script never accepts row-number-based choices and never starts a vision API.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.local_store import LocalStore  # noqa: E402
from src.runtime_config import load_runtime_config, resolve_project_path  # noqa: E402

ROLE_CONTRACT = ("white_background_product",)
SECONDARY_ROLES = {"worn_on_person", "supplementary_product_view"}


def _review_entries(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("reviews") or []
    if not isinstance(value, list):
        raise ValueError("review JSON must be a list or an object with a reviews list")
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("every review must be an object")
        yield item


def _load_manifest(root: Path, offer_id: str, record_hash: str) -> Dict[str, Any]:
    path = root / offer_id / record_hash[:16] / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"{offer_id}: archived candidate manifest does not exist")
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("offer_id")) != offer_id or str(data.get("record_hash")) != record_hash:
        raise ValueError(f"{offer_id}: archive manifest identity mismatch")
    return data


def _validated_selection(review: Dict[str, Any], manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    selected = review.get("selected")
    if not isinstance(selected, list) or len(selected) != 3:
        raise ValueError("review must contain exactly three selected candidates")
    by_index = {int(item["index"]): item for item in manifest.get("candidates") or []}
    seen = set()
    out: List[Dict[str, Any]] = []
    for rank, item in enumerate(selected, start=1):
        if not isinstance(item, dict):
            raise ValueError("selected candidate must be an object")
        index = int(item.get("index") or 0)
        role = str(item.get("role") or "")
        if index not in by_index or index in seen:
            raise ValueError("selection has an invalid or duplicate candidate index")
        if (rank == 1 and role != ROLE_CONTRACT[0]) or (rank > 1 and role not in SECONDARY_ROLES):
            raise ValueError("selection violates white-background + approved-secondary-view role contract")
        source = by_index[index]
        candidate_path = Path(str(source.get("path") or "")).resolve()
        if not candidate_path.exists():
            raise FileNotFoundError(f"candidate {index} is missing from archive")
        seen.add(index)
        out.append({**source, "role": role, "reason": str(item.get("reason") or "visual reviewer approved")[:1000]})
    return out


def apply_one(store: LocalStore, review: Dict[str, Any], archive_root: Path, selected_root: Path) -> str:
    offer_id = str(review.get("offer_id") or "").strip()
    record_hash = str(review.get("record_hash") or "").strip()
    if not offer_id or len(record_hash) < 16:
        raise ValueError("review requires offer_id and complete record_hash")
    manifest = _load_manifest(archive_root, offer_id, record_hash)
    selected = _validated_selection(review, manifest)

    snapshots = store.product_snapshots_for_review(offer_ids=[offer_id])
    if len(snapshots) != 1 or str(snapshots[0]["record_hash"]) != record_hash:
        raise ValueError(f"{offer_id}: saved product has changed since visual review")

    # This creates a one-product lease so complete_selection retains its normal
    # stale-result protection.  The relay must be stopped while applying a
    # manual review batch, otherwise a background worker could claim the lease.
    store.requeue_image_refresh(offer_ids=[offer_id])
    job = store.claim_next_job("visual-review-import")
    if not job or str(job["offer_id"]) != offer_id or str(job["version_hash"]) != record_hash:
        raise RuntimeError(f"{offer_id}: could not acquire a manual-review job lease")
    store.mark_processing(offer_id, record_hash)

    review_dir = selected_root / offer_id / record_hash[:16] / "reviewed"
    review_dir.mkdir(parents=True, exist_ok=True)
    candidates: List[Dict[str, Any]] = []
    selection_by_index = {int(item["index"]): (rank, item) for rank, item in enumerate(selected, start=1)}
    for source in manifest.get("candidates") or []:
        index = int(source["index"])
        candidate_path = Path(str(source["path"])).resolve()
        chosen = selection_by_index.get(index)
        local_path = str(candidate_path)
        role = ""
        reason = ""
        rank = None
        if chosen:
            rank, choice = chosen
            destination = review_dir / f"{rank:02d}.jpg"
            shutil.copy2(candidate_path, destination)
            local_path = str(destination.resolve())
            role, reason = choice["role"], choice["reason"]
        candidates.append(
            {
                "candidate_index": index,
                "source_url": str(source["source_url"]),
                "width": int(source.get("width") or 0),
                "height": int(source.get("height") or 0),
                "sha256": str(source.get("sha256") or ""),
                "role": role,
                "reason": reason,
                "is_selected": bool(chosen),
                "selected_rank": rank,
                "local_path": local_path,
            }
        )
    if not store.complete_selection(offer_id, record_hash, candidates, "visual_agent_review:v1", required_selected=3):
        raise RuntimeError(f"{offer_id}: selection was not committed because the record changed")
    return offer_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply strict visual-review selections to the local catalog")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--review-json", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_runtime_config(args.config)
    db_path = args.db.resolve() if args.db else resolve_project_path(
        str(cfg.get("relay", {}).get("database") or ""), "data/catalog.db"
    )
    store = LocalStore(db_path)
    reviews = list(_review_entries(json.loads(args.review_json.read_text(encoding="utf-8"))))
    if args.dry_run:
        for review in reviews:
            offer_id = str(review.get("offer_id") or "")
            manifest = _load_manifest(args.archive_root.resolve(), offer_id, str(review.get("record_hash") or ""))
            _validated_selection(review, manifest)
        print(f"validated reviews: {len(reviews)}")
        return

    selected_root = resolve_project_path("products/selected", "products/selected")
    applied = [apply_one(store, review, args.archive_root.resolve(), selected_root) for review in reviews]
    print(f"applied reviews: {len(applied)}")
    print("offer_ids:", ",".join(applied))


if __name__ == "__main__":
    main()
