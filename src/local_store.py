"""SQLite data layer for the local relay.

Every product, image and worker job is keyed by the same immutable ``offer_id``.
The record hash is checked again when an asynchronous image job finishes, so an
old job can never attach images to a newer version of a product record.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


OFFER_ID_RE = re.compile(r"^\d{5,30}$")
OFFER_URL_RE = re.compile(r"offer[/=](\d+)", re.IGNORECASE)
JOB_STATES = ("pending", "processing", "done", "failed")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _clean_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _unique_urls(values: Iterable[Any], limit: int = 40) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values or []:
        url = str(value or "").strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            continue
        key = url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(key[:3000])
        if len(out) >= limit:
            break
    return out


def validate_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize one atomic product snapshot."""
    if not isinstance(record, dict):
        raise ValueError("record must be an object")

    offer_id = _clean_text(record.get("offer_id"), 30)
    if not OFFER_ID_RE.fullmatch(offer_id):
        raise ValueError("offer_id must contain 5-30 digits")

    source_url = _clean_text(record.get("url") or record.get("source_url"), 2000)
    match = OFFER_URL_RE.search(source_url)
    if not match or match.group(1) != offer_id:
        raise ValueError("source URL and offer_id do not match")

    price = record.get("price") if isinstance(record.get("price"), dict) else {}
    size = record.get("size") if isinstance(record.get("size"), dict) else {}
    specs = record.get("specs") if isinstance(record.get("specs"), dict) else {}
    image_urls = _unique_urls(record.get("image_urls") or [])

    cleaned = {
        "schema_version": int(record.get("schema_version") or 1),
        "offer_id": offer_id,
        "url": source_url,
        "title": _clean_text(record.get("title"), 1000),
        "category": _clean_text(record.get("category"), 200),
        "price": {
            "raw": _clean_text(price.get("raw"), 6000),
            "display": _clean_text(price.get("display"), 500),
            "tiers": price.get("tiers") if isinstance(price.get("tiers"), list) else [],
        },
        "size": {
            "raw": _clean_text(size.get("raw"), 3000),
            "source": _clean_text(size.get("source"), 100),
        },
        "specs": {str(k)[:200]: _clean_text(v, 2000) for k, v in specs.items()},
        "image_urls": image_urls,
        "collected_at": _clean_text(record.get("collected_at"), 80) or utc_now(),
    }
    hash_payload = {k: cleaned[k] for k in cleaned if k != "collected_at"}
    cleaned["record_hash"] = hashlib.sha256(_json(hash_payload).encode("utf-8")).hexdigest()
    return cleaned


class LocalStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    offer_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    price_display TEXT NOT NULL DEFAULT '',
                    size_raw TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    selection_status TEXT NOT NULL DEFAULT 'pending',
                    selection_method TEXT NOT NULL DEFAULT '',
                    selection_error TEXT NOT NULL DEFAULT '',
                    selected_count INTEGER NOT NULL DEFAULT 0,
                    CHECK (offer_id GLOB '[0-9]*')
                );

                CREATE TABLE IF NOT EXISTS product_images (
                    offer_id TEXT NOT NULL,
                    candidate_index INTEGER NOT NULL,
                    source_url TEXT NOT NULL,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    sha256 TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    ai_reason TEXT NOT NULL DEFAULT '',
                    is_selected INTEGER NOT NULL DEFAULT 0,
                    selected_rank INTEGER,
                    local_path TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (offer_id, candidate_index),
                    UNIQUE (offer_id, source_url),
                    FOREIGN KEY (offer_id) REFERENCES products(offer_id) ON DELETE CASCADE,
                    CHECK (is_selected IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS image_jobs (
                    offer_id TEXT PRIMARY KEY,
                    version_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    not_before TEXT NOT NULL,
                    worker_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (offer_id) REFERENCES products(offer_id) ON DELETE CASCADE,
                    CHECK (status IN ('pending','processing','done','failed'))
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_ready
                    ON image_jobs(status, not_before, updated_at);
                CREATE INDEX IF NOT EXISTS idx_products_status
                    ON products(selection_status, category, offer_id);
                CREATE INDEX IF NOT EXISTS idx_images_selected
                    ON product_images(offer_id, is_selected, selected_rank);
                """
            )
            stale_before = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")
            conn.execute(
                """UPDATE image_jobs
                   SET status='pending', worker_id='', error='worker lease expired', updated_at=?
                   WHERE status='processing' AND updated_at < ?""",
                (utc_now(), stale_before),
            )

    def upsert_product(self, record: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = validate_record(record)
        now = utc_now()
        raw_json = _json(cleaned)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT record_hash, selection_status FROM products WHERE offer_id=?",
                (cleaned["offer_id"],),
            ).fetchone()
            unchanged = bool(existing and existing["record_hash"] == cleaned["record_hash"])

            if unchanged:
                conn.execute(
                    "UPDATE products SET received_at=? WHERE offer_id=?",
                    (now, cleaned["offer_id"]),
                )
                if existing["selection_status"] in ("failed", "pending"):
                    conn.execute(
                        """INSERT INTO image_jobs
                           (offer_id, version_hash, status, attempts, not_before, created_at, updated_at)
                           VALUES (?, ?, 'pending', 0, ?, ?, ?)
                           ON CONFLICT(offer_id) DO UPDATE SET
                             version_hash=excluded.version_hash, status='pending', attempts=0,
                             not_before=excluded.not_before, worker_id='', error='', updated_at=excluded.updated_at""",
                        (cleaned["offer_id"], cleaned["record_hash"], now, now, now),
                    )
                return {
                    "offer_id": cleaned["offer_id"],
                    "record_hash": cleaned["record_hash"],
                    "unchanged": True,
                    "selection_status": existing["selection_status"],
                }

            conn.execute(
                """INSERT INTO products
                   (offer_id, source_url, title, category, price_display, size_raw, raw_json,
                    record_hash, collected_at, received_at, updated_at, selection_status,
                    selection_method, selection_error, selected_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', '', 0)
                   ON CONFLICT(offer_id) DO UPDATE SET
                    source_url=excluded.source_url, title=excluded.title, category=excluded.category,
                    price_display=excluded.price_display, size_raw=excluded.size_raw,
                    raw_json=excluded.raw_json, record_hash=excluded.record_hash,
                    collected_at=excluded.collected_at, received_at=excluded.received_at,
                    updated_at=excluded.updated_at, selection_status='pending',
                    selection_method='', selection_error='', selected_count=0""",
                (
                    cleaned["offer_id"], cleaned["url"], cleaned["title"], cleaned["category"],
                    cleaned["price"]["display"], cleaned["size"]["raw"], raw_json,
                    cleaned["record_hash"], cleaned["collected_at"], now, now,
                ),
            )
            conn.execute("DELETE FROM product_images WHERE offer_id=?", (cleaned["offer_id"],))
            conn.execute(
                """INSERT INTO image_jobs
                   (offer_id, version_hash, status, attempts, not_before, worker_id, error, created_at, updated_at)
                   VALUES (?, ?, 'pending', 0, ?, '', '', ?, ?)
                   ON CONFLICT(offer_id) DO UPDATE SET
                    version_hash=excluded.version_hash, status='pending', attempts=0,
                    not_before=excluded.not_before, worker_id='', error='', updated_at=excluded.updated_at""",
                (cleaned["offer_id"], cleaned["record_hash"], now, now, now),
            )
            return {
                "offer_id": cleaned["offer_id"],
                "record_hash": cleaned["record_hash"],
                "unchanged": False,
                "selection_status": "pending",
            }

    def claim_next_job(self, worker_id: str) -> Optional[Dict[str, Any]]:
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT j.offer_id, j.version_hash, j.attempts, p.raw_json
                   FROM image_jobs j
                   JOIN products p ON p.offer_id=j.offer_id
                   WHERE j.status='pending' AND j.not_before <= ?
                   ORDER BY j.updated_at, j.offer_id
                   LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                return None
            updated = conn.execute(
                """UPDATE image_jobs SET status='processing', worker_id=?, updated_at=?
                   WHERE offer_id=? AND version_hash=? AND status='pending'""",
                (worker_id, now, row["offer_id"], row["version_hash"]),
            ).rowcount
            if updated != 1:
                return None
            return {
                "offer_id": row["offer_id"],
                "version_hash": row["version_hash"],
                "attempts": row["attempts"],
                "record": json.loads(row["raw_json"]),
            }

    def complete_selection(
        self,
        offer_id: str,
        version_hash: str,
        candidates: List[Dict[str, Any]],
        method: str,
        required_selected: int,
    ) -> bool:
        selected = [c for c in candidates if c.get("is_selected")]
        if len(selected) != required_selected:
            raise ValueError(f"expected {required_selected} selected images, got {len(selected)}")
        ranks = sorted(int(c.get("selected_rank") or 0) for c in selected)
        if ranks != list(range(1, required_selected + 1)):
            raise ValueError("selected image ranks must be contiguous starting at 1")

        hash_prefix = version_hash[:16]
        for item in selected:
            path = Path(str(item.get("local_path") or "")).resolve()
            if offer_id not in path.parts or hash_prefix not in path.parts:
                raise ValueError("selected image path is not scoped to offer_id and record hash")
            if not path.exists():
                raise ValueError(f"selected image missing: {path}")

        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT record_hash FROM products WHERE offer_id=?",
                (offer_id,),
            ).fetchone()
            if not current or current["record_hash"] != version_hash:
                return False
            job = conn.execute(
                "SELECT version_hash, status FROM image_jobs WHERE offer_id=?",
                (offer_id,),
            ).fetchone()
            if not job or job["version_hash"] != version_hash or job["status"] != "processing":
                return False

            conn.execute("DELETE FROM product_images WHERE offer_id=?", (offer_id,))
            for item in candidates:
                conn.execute(
                    """INSERT INTO product_images
                       (offer_id, candidate_index, source_url, width, height, sha256, role,
                        ai_reason, is_selected, selected_rank, local_path)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        offer_id, int(item["candidate_index"]), str(item["source_url"]),
                        int(item.get("width") or 0), int(item.get("height") or 0),
                        str(item.get("sha256") or ""), str(item.get("role") or ""),
                        str(item.get("reason") or "")[:1000], 1 if item.get("is_selected") else 0,
                        item.get("selected_rank"), str(item.get("local_path") or ""),
                    ),
                )
            conn.execute(
                """UPDATE products SET selection_status='ready', selection_method=?,
                   selection_error='', selected_count=?, updated_at=? WHERE offer_id=?""",
                (method, required_selected, now, offer_id),
            )
            conn.execute(
                """UPDATE image_jobs SET status='done', worker_id='', error='', updated_at=?
                   WHERE offer_id=? AND version_hash=?""",
                (now, offer_id, version_hash),
            )
            return True

    def fail_job(self, offer_id: str, version_hash: str, error: str, max_attempts: int) -> None:
        now_dt = datetime.now(timezone.utc)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT attempts, version_hash FROM image_jobs WHERE offer_id=?",
                (offer_id,),
            ).fetchone()
            if not row or row["version_hash"] != version_hash:
                return
            attempts = int(row["attempts"]) + 1
            terminal = attempts >= max_attempts
            status = "failed" if terminal else "pending"
            delay_seconds = min(300, 10 * (2 ** max(0, attempts - 1)))
            not_before = (now_dt + timedelta(seconds=delay_seconds)).isoformat(timespec="seconds")
            conn.execute(
                """UPDATE image_jobs SET status=?, attempts=?, not_before=?, worker_id='',
                   error=?, updated_at=? WHERE offer_id=? AND version_hash=?""",
                (status, attempts, not_before, error[:2000], utc_now(), offer_id, version_hash),
            )
            conn.execute(
                """UPDATE products SET selection_status=?, selection_error=?, updated_at=?
                   WHERE offer_id=? AND record_hash=?""",
                (status, error[:2000], utc_now(), offer_id, version_hash),
            )

    def stats(self) -> Dict[str, int]:
        with self.connect() as conn:
            totals = conn.execute(
                """SELECT COUNT(*) AS total,
                   SUM(CASE WHEN selection_status='ready' THEN 1 ELSE 0 END) AS ready,
                   SUM(CASE WHEN selection_status='pending' THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN selection_status='processing' THEN 1 ELSE 0 END) AS processing,
                   SUM(CASE WHEN selection_status='failed' THEN 1 ELSE 0 END) AS failed
                   FROM products"""
            ).fetchone()
        return {key: int(totals[key] or 0) for key in ("total", "ready", "pending", "processing", "failed")}

    def mark_processing(self, offer_id: str, version_hash: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE products SET selection_status='processing', updated_at=?
                   WHERE offer_id=? AND record_hash=?""",
                (utc_now(), offer_id, version_hash),
            )

    def products_for_export(self, category: str = "", limit: int = 0) -> List[Dict[str, Any]]:
        query = "SELECT * FROM products WHERE selection_status='ready'"
        params: List[Any] = []
        if category:
            query += " AND category=?"
            params.append(category)
        query += " ORDER BY category, offer_id"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        out: List[Dict[str, Any]] = []
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            for row in rows:
                images = conn.execute(
                    """SELECT * FROM product_images
                       WHERE offer_id=? AND is_selected=1 ORDER BY selected_rank""",
                    (row["offer_id"],),
                ).fetchall()
                item = dict(row)
                item["record"] = json.loads(row["raw_json"])
                item["selected_images"] = [dict(image) for image in images]
                out.append(item)
        return out
