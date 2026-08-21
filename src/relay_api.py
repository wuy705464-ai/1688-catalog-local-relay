"""Local-only FastAPI relay for the Tampermonkey collector."""
from __future__ import annotations

import argparse
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

try:
    from .image_selector import ImageWorker
    from .local_store import LocalStore
    from .runtime_config import load_env_file, load_runtime_config, resolve_project_path
except ImportError:  # pragma: no cover
    from image_selector import ImageWorker
    from local_store import LocalStore
    from runtime_config import load_env_file, load_runtime_config, resolve_project_path


logger = logging.getLogger("relay_api")


class PricePayload(BaseModel):
    raw: str = ""
    display: str = ""
    tiers: List[Dict[str, Any]] = Field(default_factory=list)


class SizePayload(BaseModel):
    raw: str = ""
    source: str = ""


class ProductPayload(BaseModel):
    schema_version: int = 3
    offer_id: str
    url: str
    title: str = ""
    category: str = ""
    price: PricePayload = Field(default_factory=PricePayload)
    size: SizePayload = Field(default_factory=SizePayload)
    specs: Dict[str, Any] = Field(default_factory=dict)
    image_urls: List[str] = Field(default_factory=list)
    collected_at: str = ""


def create_app(
    config_path: Path | str | None = None,
    env_file: Path | str | None = None,
    start_worker: bool = True,
) -> FastAPI:
    # An explicitly supplied file is the operator's source of truth. This also
    # prevents unrelated machine-level ARK_* variables from selecting a
    # different model or putting placeholder text into request headers.
    load_env_file(env_file, override=True)
    cfg = load_runtime_config(config_path)
    relay_cfg = cfg.get("relay", {})
    db_path = resolve_project_path(str(relay_cfg.get("database") or ""), "data/catalog.db")
    store = LocalStore(db_path)
    worker = ImageWorker(store, cfg)
    expected_token = os.getenv("RELAY_TOKEN") or str(relay_cfg.get("token") or "")
    if not expected_token or expected_token == "CHANGE_ME_LOCAL_TOKEN":
        raise RuntimeError("Set a private relay.token or RELAY_TOKEN before starting the relay")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_worker:
            worker.start()
            logger.info("background image worker started")
        try:
            yield
        finally:
            if start_worker:
                worker.stop()

    app = FastAPI(
        title="1688 Local Catalog Relay",
        version="3.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.store = store
    app.state.worker = worker
    app.state.config = cfg

    def require_token(x_relay_token: str = Header(default="")) -> None:
        if not expected_token or not hmac.compare_digest(x_relay_token, expected_token):
            raise HTTPException(status_code=401, detail="invalid relay token")

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "service": "1688-local-relay", "version": "3.0.0"}

    @app.get("/api/v1/stats", dependencies=[Depends(require_token)])
    def stats() -> Dict[str, Any]:
        return {"ok": True, **store.stats()}

    @app.post("/api/v1/products", dependencies=[Depends(require_token)])
    def submit_product(payload: ProductPayload) -> Dict[str, Any]:
        try:
            result = store.upsert_product(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, **result, "stats": store.stats()}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local 1688 catalog relay")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--env-file", type=Path, default=None, help="豆包 ARK_API_KEY/ARK_BASE_URL/ARK_MODEL 文件")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-worker", action="store_true")
    args = parser.parse_args()

    load_env_file(args.env_file, override=True)
    cfg = load_runtime_config(args.config)
    relay_cfg = cfg.get("relay", {})
    host = args.host or str(relay_cfg.get("host") or "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("For safety this relay only binds to localhost")
    port = args.port or int(relay_cfg.get("port") or 8765)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    import uvicorn

    app = create_app(args.config, None, start_worker=not args.no_worker)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
