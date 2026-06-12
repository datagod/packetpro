"""FastAPI search UI for PacketPro."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import cv2
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from packetpro.config import (
    AppConfig,
    ConfigError,
    get_paths_settings,
    load_config,
    save_paths_settings,
)
from packetpro.db import get_document, init_db, search_documents
from packetpro.enhance import render_pdf_page

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _try_load_config() -> AppConfig | None:
    try:
        return load_config()
    except ConfigError:
        return None


def create_app(config: AppConfig | None = None) -> FastAPI:
    app = FastAPI(title="PacketPro", version="0.1.0")

    def runtime_config() -> AppConfig:
        if config is not None:
            return config
        loaded = _try_load_config()
        if loaded is None:
            raise HTTPException(
                status_code=503,
                detail="Folder locations are not configured. Visit /settings first.",
            )
        return loaded

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, q: str = Query(default="")) -> HTMLResponse:
        settings = get_paths_settings()
        cfg = _try_load_config()
        results = []
        if cfg is not None and q.strip():
            init_db(cfg.database)
            results = search_documents(cfg.database, q)

        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "query": q,
                "results": results,
                "result_count": len(results),
                "configured": settings["configured"],
                "paths": settings,
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(
        request: Request,
        saved: str = Query(default=""),
        error: str = Query(default=""),
    ) -> HTMLResponse:
        settings = get_paths_settings()
        return TEMPLATES.TemplateResponse(
            request,
            "settings.html",
            {
                "settings": settings,
                "saved": saved == "1",
                "error": error,
            },
        )

    @app.post("/settings")
    async def settings_save(
        data_root: str = Form(...),
        inbox: str = Form(...),
        transformed: str = Form(...),
        archive: str = Form(...),
        failed: str = Form(...),
        database: str = Form(...),
    ) -> RedirectResponse:
        try:
            cfg = save_paths_settings(
                data_root=data_root,
                inbox=inbox,
                transformed=transformed,
                archive=archive,
                failed=failed,
                database=database,
            )
            init_db(cfg.database)
        except ConfigError as exc:
            from urllib.parse import quote

            return RedirectResponse(
                url=f"/settings?error={quote(str(exc))}",
                status_code=303,
            )
        return RedirectResponse(url="/settings?saved=1", status_code=303)

    @app.get("/api/settings")
    async def api_settings() -> dict:
        settings = get_paths_settings()
        cfg = _try_load_config()
        resolved = None
        if cfg is not None:
            resolved = {
                "inbox": str(cfg.inbox),
                "transformed": str(cfg.transformed),
                "archive": str(cfg.archive),
                "failed": str(cfg.failed),
                "database": str(cfg.database),
            }
        return {"settings": settings, "resolved_paths": resolved}

    @app.get("/api/search")
    async def api_search(q: str = Query(..., min_length=1)) -> dict:
        cfg = runtime_config()
        init_db(cfg.database)
        results = search_documents(cfg.database, q)
        return {
            "query": q,
            "count": len(results),
            "results": [
                {
                    "id": item.document.id,
                    "job_id": item.document.job_id,
                    "original_name": item.document.original_name,
                    "page_number": item.document.page_number,
                    "snippet": item.snippet,
                    "processed_at": item.document.processed_at,
                    "image_url": f"/images/{item.document.id}",
                }
                for item in results
            ],
        }

    @app.get("/images/{doc_id}")
    async def get_image(doc_id: int) -> FileResponse:
        cfg = runtime_config()
        archive_root = cfg.archive.resolve()
        doc = get_document(cfg.database, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")

        image_path = Path(doc.archive_path).resolve()
        try:
            image_path.relative_to(archive_root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Invalid archive path") from exc

        if not image_path.is_file():
            raise HTTPException(status_code=404, detail="Image file not found")

        if image_path.suffix.lower() == ".pdf":
            page = render_pdf_page(image_path, doc.page_number - 1, dpi=150)
            ok, encoded = cv2.imencode(".png", page)
            if not ok:
                raise HTTPException(status_code=500, detail="Failed to render PDF page")
            return Response(content=encoded.tobytes(), media_type="image/png")

        media_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        return FileResponse(image_path, media_type=media_type, filename=doc.original_name)

    return app