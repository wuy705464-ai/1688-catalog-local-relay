"""Safely requeue existing product snapshots for a new visual-role policy."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.local_store import LocalStore  # noqa: E402
from src.runtime_config import load_runtime_config, resolve_project_path  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Requeue saved products for strict white-background/worn-image selection")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of saved products to requeue; 0 means all")
    parser.add_argument("--offer-id", action="append", default=[], help="Requeue only this offer ID; may be repeated")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be zero or positive")

    cfg = load_runtime_config(args.config)
    db_path = args.db.resolve() if args.db else resolve_project_path(
        str(cfg.get("relay", {}).get("database") or ""), "data/catalog.db"
    )
    store = LocalStore(db_path)
    if args.dry_run:
        print(f"stats: {store.stats()}")
        print(f"would requeue up to {args.limit or 'all'} product snapshots")
        return
    queued = store.requeue_image_refresh(limit=args.limit, offer_ids=args.offer_id)
    print(f"requeued: {len(queued)}")
    print("offer_ids:", ",".join(queued))


if __name__ == "__main__":
    main()
