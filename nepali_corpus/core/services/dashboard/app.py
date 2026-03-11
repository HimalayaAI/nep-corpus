from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from nepali_corpus.core.services.storage.env_storage import EnvStorageService
from nepali_corpus.core.services.scrapers.control import ScrapeCoordinator
from .sources import get_sources
from .file_tables import (
    infer_columns_from_jsonl,
    list_file_tables,
    list_jsonl_files,
    read_jsonl_page,
    resolve_data_file,
    resolve_file_table,
    search_jsonl,
)

app = FastAPI(title="Nepali Corpus Dashboard")

# Static UI
base_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(base_dir, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

storage = EnvStorageService()
coordinator = ScrapeCoordinator(storage)

_ws_clients: List[WebSocket] = []
_log_buffer: List[str] = []
_logger = logging.getLogger("nepali_corpus.dashboard")


class WSLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        _log_buffer.append(msg)
        if len(_log_buffer) > 500:
            _log_buffer.pop(0)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_broadcast_log(msg))
        except Exception:
            return


async def _broadcast_log(message: str) -> None:
    dead: List[WebSocket] = []
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


def _setup_logging() -> None:
    root = logging.getLogger()
    if any(isinstance(h, WSLogHandler) for h in root.handlers):
        return
    handler = WSLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    root.addHandler(handler)
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.on_event("startup")
async def on_startup() -> None:
    _setup_logging()
    try:
        await storage.initialize()
    except Exception as exc:
        _logger.warning("Storage initialization failed: %s", exc)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    try:
        await storage.close()
    except Exception:
        pass


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/get-tables")
async def get_tables() -> List[str]:
    db_tables: List[str] = []

    if storage._db is not None:
        try:
            rows = await storage._db.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_type IN ('BASE TABLE', 'VIEW')
                ORDER BY table_name
                """
            )
            for row in rows:
                db_tables.append(row[0])
        except Exception as e:
            _logger.warning("Failed to fetch database tables: %s", e)

    return db_tables


@app.get("/api/files")
async def list_files() -> List[Dict[str, Any]]:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
    files: List[Dict[str, Any]] = []
    for rel in list_jsonl_files(include_gz=True):
        path = os.path.join(repo_root, rel)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        files.append(
            {
                "name": os.path.basename(path),
                "path": rel,
                "size_mb": round(stat.st_size / 1024 ** 2, 2),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    return files


@app.get("/api/files/download/{file_path:path}")
async def download_file(file_path: str):
    path = resolve_data_file(file_path)
    if not path:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path), filename=path.name)


@app.get("/api/status")
async def get_status() -> dict:
    data = coordinator.state.to_dict()
    data["db_url_count"] = None
    if storage._db is not None:
        try:
            session = storage.create_session()
            data["db_url_count"] = await session.count_urls()
        except Exception:
            data["db_url_count"] = None
    return data


@app.post("/api/start")
async def start_scraper(payload: Dict[str, Any]) -> dict:
    if coordinator.is_running():
        raise HTTPException(status_code=400, detail="Scraper already running")

    workers = int(payload.get("workers", 4))
    max_pages = payload.get("max_pages")
    categories = payload.get("categories") or None
    pdf_enabled = bool(payload.get("pdf_enabled", False))
    gzip_output = bool(payload.get("gzip_output", False))

    await coordinator.start(
        workers=workers,
        max_pages=max_pages,
        categories=categories,
        pdf_enabled=pdf_enabled,
        gzip_output=gzip_output,
    )
    return {"status": "started", "workers": workers}


@app.post("/api/stop")
async def stop_scraper() -> dict:
    await coordinator.stop()
    return {"status": "stopped"}


@app.post("/api/pause")
async def pause_scraper() -> dict:
    coordinator.pause()
    return {"status": "paused"}


@app.post("/api/resume")
async def resume_scraper() -> dict:
    coordinator.resume()
    return {"status": "resumed"}


@app.get("/api/logs")
async def get_logs(lines: int = 200) -> dict:
    return {"lines": _log_buffer[-lines:]}


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    for line in _log_buffer[-100:]:
        await websocket.send_text(line)
    _ws_clients.append(websocket)
    try:
        while True:
            await asyncio.sleep(1)
    except (WebSocketDisconnect, Exception):
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


@app.websocket("/ws/stats")
async def ws_stats(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await get_status()
            await websocket.send_text(json.dumps(data))
            await asyncio.sleep(1)
    except (WebSocketDisconnect, Exception):
        return


@app.get("/api/sources")
async def list_sources(refresh: bool = Query(False)) -> Dict[str, Any]:
    sources = get_sources(refresh=refresh)
    crawled_counts: Dict[str, int] = {}
    saved_counts: Dict[str, int] = {}
    live_stats = coordinator.state.source_stats

    if storage._db is not None:
        try:
            # Get saved (cleaned) counts
            saved_rows = await storage._db.fetch(
                "SELECT source_id, COUNT(*) FROM training_documents GROUP BY source_id"
            )
            saved_counts = {row[0]: int(row[1]) for row in saved_rows}
            
            # Get crawled (raw) counts
            crawled_rows = await storage._db.fetch(
                "SELECT source_id, COUNT(*) FROM raw_records GROUP BY source_id"
            )
            crawled_counts = {row[0]: int(row[1]) for row in crawled_rows}
        except Exception as e:
            _logger.error(f"Failed to fetch counts: {e}")

    for source in sources:
        sid = source["id"]
        # Use live stats if available, else DB counts
        live = live_stats.get(sid, {})
        
        saved_db = saved_counts.get(sid, 0)
        crawled_db = crawled_counts.get(sid, 0)
        
        source["saved"] = live.get("saved", saved_db)
        source["crawled"] = live.get("crawled", max(crawled_db, source["saved"]))
        source["failed"] = live.get("failed", 0)

    return {
        "sources": sources,
        "total": len(sources),
        "categories": sorted({s["category"] for s in sources if s.get("category")}),
    }


def _is_file_table(table_name: str) -> bool:
    return table_name.startswith("file:")


def _format_row(row: Dict[str, Any]) -> Dict[str, Any]:
    formatted: Dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (dict, list)):
            formatted[key] = json.dumps(value, ensure_ascii=False, indent=2)
        elif isinstance(value, datetime):
            formatted[key] = value.isoformat()
        else:
            formatted[key] = value
    return formatted


@app.get("/api/column-names")
async def get_column_names(table_name: str = Query(..., min_length=1)) -> List[Dict[str, Any]]:
    if _is_file_table(table_name):
        path = resolve_file_table(table_name)
        if not path:
            raise HTTPException(status_code=404, detail="File table not found")
        return infer_columns_from_jsonl(path)

    if storage._db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    rows = await storage._db.fetch(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table_name,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Table not found")

    return [{"name": row[0], "type": row[1], "is_json": row[1] in ("json", "jsonb")} for row in rows]


@app.get("/api/metrics-data")
async def get_metrics_data(
    table_name: str = Query(..., min_length=1),
    x_column: Optional[str] = Query(None),
    y_column: Optional[str] = Query(None),
    full_table: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
) -> Dict[str, Any]:
    if _is_file_table(table_name):
        path = resolve_file_table(table_name)
        if not path:
            raise HTTPException(status_code=404, detail="File table not found")

        data, total_count = read_jsonl_page(path, page, page_size)
        if not full_table:
            if not x_column or not y_column:
                raise HTTPException(status_code=400, detail="X and Y columns required")
            pairs = []
            for row in data:
                pairs.append({"x_value": row.get(x_column), "y_value": row.get(y_column)})
            data = pairs

        return {
            "data": data,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": (total_count + page_size - 1) // page_size if total_count else 1,
        }

    if storage._db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    col_rows = await storage._db.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table_name,
    )
    columns = [row[0] for row in col_rows]
    if not columns:
        raise HTTPException(status_code=404, detail="Table not found")

    offset = (page - 1) * page_size

    if full_table:
        select_cols = ", ".join([f'"{c}"' for c in columns])
        query = f'SELECT {select_cols} FROM "{table_name}" OFFSET $1 LIMIT $2'
        rows = await storage._db.fetch(query, offset, page_size)
    else:
        if not x_column or not y_column:
            raise HTTPException(status_code=400, detail="X and Y columns required")
        if x_column not in columns or y_column not in columns:
            raise HTTPException(status_code=400, detail="Invalid column")
        query = f'SELECT "{x_column}" as x_value, "{y_column}" as y_value FROM "{table_name}" OFFSET $1 LIMIT $2'
        rows = await storage._db.fetch(query, offset, page_size)

    total_count = await storage._db.fetch_value(f'SELECT COUNT(*) FROM "{table_name}"')

    data = [_format_row(dict(row)) for row in rows]

    return {
        "data": data,
        "total_count": int(total_count or 0),
        "page": page,
        "page_size": page_size,
        "total_pages": (int(total_count or 0) + page_size - 1) // page_size if total_count else 1,
    }


@app.get("/api/search")
async def search_database(
    table_name: str = Query(..., min_length=1),
    search_term: str = Query(..., min_length=1),
    columns: Optional[List[str]] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
) -> Dict[str, Any]:
    if _is_file_table(table_name):
        path = resolve_file_table(table_name)
        if not path:
            raise HTTPException(status_code=404, detail="File table not found")
        data, total_count = search_jsonl(path, search_term, page, page_size, columns)
        return {
            "data": data,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": (total_count + page_size - 1) // page_size if total_count else 1,
        }

    if storage._db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    col_rows = await storage._db.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table_name,
    )
    all_columns = [row[0] for row in col_rows]
    if not all_columns:
        raise HTTPException(status_code=404, detail="Table not found")

    if columns:
        search_columns = [c for c in columns if c in all_columns]
    else:
        search_columns = all_columns

    if not search_columns:
        return {"data": [], "total_count": 0, "page": page, "page_size": page_size, "total_pages": 1}

    where = " OR ".join([f'CAST("{c}" AS TEXT) ILIKE $1' for c in search_columns])
    offset = (page - 1) * page_size

    total_query = f'SELECT COUNT(*) FROM "{table_name}" WHERE {where}'
    data_query = f'SELECT * FROM "{table_name}" WHERE {where} OFFSET $2 LIMIT $3'

    total_count = await storage._db.fetch_value(total_query, f"%{search_term}%")
    rows = await storage._db.fetch(data_query, f"%{search_term}%", offset, page_size)

    data = [_format_row(dict(row)) for row in rows]

    return {
        "data": data,
        "total_count": int(total_count or 0),
        "page": page,
        "page_size": page_size,
        "total_pages": (int(total_count or 0) + page_size - 1) // page_size if total_count else 1,
    }


@app.get("/")
async def read_root():
    if os.path.isdir(static_dir):
        return FileResponse(os.path.join(static_dir, "index.html"))
    return {"message": "Dashboard UI not found"}
