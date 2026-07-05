"""FastAPI search UI for PacketPro."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import cv2
from fastapi import Body, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from packetpro.config import (
    AppConfig,
    ConfigError,
    check_watch_folder_access,
    ensure_data_dirs,
    get_paths_settings,
    is_archive_source,
    load_config,
    resolve_allowed_document_path,
    save_ocr_engine_settings,
    save_ocr_model_settings,
    save_paths_settings,
)
from packetpro.db import (
    SEARCH_LIMIT_ALL_MAX,
    SearchQueryError,
    count_documents_with_archive,
    delete_document,
    get_document,
    init_db,
    resolve_search_limit,
    search_documents,
)
from packetpro.enhance import render_pdf_page
from packetpro.export import (
    EXPORT_SEARCH_LIMIT,
    export_filename,
    format_ai_export,
    write_export_pdf,
)
from packetpro.pipeline_control import (
    get_control_state,
    kickstart_pipeline,
    read_activity,
    set_processing_enabled,
)
from packetpro.stats import collect_stats
from packetpro.workers.watch_worker import (
    get_watch_watermark_status,
    request_watch_folder_scan,
    set_watch_watermark,
)

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


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

    def _export_documents(cfg: AppConfig, query: str):
        init_db(cfg.database)
        ensure_data_dirs(cfg)
        results = search_documents(cfg.database, query, limit=EXPORT_SEARCH_LIMIT)
        return [item.document for item in results]

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        q: str = Query(default=""),
        limit: str = Query(default="50"),
    ) -> HTMLResponse:
        settings = get_paths_settings()
        cfg = _try_load_config()
        results = []
        search_error = None
        search_limit_capped = False
        resolved_limit = resolve_search_limit(limit)
        display_limit = "all" if resolved_limit is None else str(resolved_limit)
        if cfg is not None and q.strip():
            init_db(cfg.database)
            try:
                results = search_documents(cfg.database, q, limit=resolved_limit)
                search_limit_capped = (
                    resolved_limit is None and len(results) >= SEARCH_LIMIT_ALL_MAX
                )
            except SearchQueryError as exc:
                search_error = str(exc)

        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "query": q,
                "results": results,
                "result_count": len(results),
                "search_limit": display_limit,
                "search_limit_max": SEARCH_LIMIT_ALL_MAX,
                "search_limit_capped": search_limit_capped,
                "search_error": search_error,
                "configured": settings["configured"],
                "paths": settings,
                "active_tab": "search",
            },
        )

    @app.get("/pipeline", response_class=HTMLResponse)
    async def pipeline_page(
        request: Request,
        investigate: str = Query(default=""),
    ) -> HTMLResponse:
        settings = get_paths_settings()
        return TEMPLATES.TemplateResponse(
            request,
            "pipeline.html",
            {
                "configured": settings["configured"],
                "paths": settings,
                "active_tab": "pipeline",
                "investigate": investigate == "1",
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
                "active_tab": "settings",
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
        watch_folder: str = Form(default=""),
        use_both_gpus: str = Form(default=""),
        ocr_model: str = Form(default=""),
        secondary_ocr_model: str = Form(default=""),
    ) -> RedirectResponse:
        try:
            cfg = save_paths_settings(
                data_root=data_root,
                inbox=inbox,
                transformed=transformed,
                archive=archive,
                failed=failed,
                database=database,
                watch_folder=watch_folder,
                use_both_gpus=use_both_gpus == "on",
                ocr_model=ocr_model,
                secondary_ocr_model=secondary_ocr_model,
            )
            init_db(cfg.database)
        except ConfigError as exc:
            from urllib.parse import quote

            return RedirectResponse(
                url=f"/settings?error={quote(str(exc))}",
                status_code=303,
            )
        return RedirectResponse(url="/settings?saved=1", status_code=303)

    @app.get("/api/control")
    async def api_control_get() -> dict:
        cfg = _try_load_config()
        if cfg is None:
            return {"configured": False, "processing_enabled": False}
        return {"configured": True, **get_control_state(cfg)}

    @app.post("/api/control")
    async def api_control_set(payload: dict = Body(...)) -> dict:
        cfg = runtime_config()
        if "processing_enabled" not in payload:
            raise HTTPException(status_code=400, detail="processing_enabled is required")
        state = set_processing_enabled(cfg, bool(payload["processing_enabled"]))
        return {"configured": True, **state}

    @app.post("/api/pipeline/kickstart")
    async def api_pipeline_kickstart() -> dict:
        cfg = runtime_config()
        result = kickstart_pipeline(cfg)
        return {"configured": True, **result}

    @app.get("/api/activity")
    async def api_activity(
        limit: int = Query(default=80, ge=1, le=200),
        debug: str = Query(default=""),
    ) -> dict:
        cfg = _try_load_config()
        if cfg is None:
            return {"configured": False, "entries": []}
        return {
            "configured": True,
            "entries": read_activity(cfg, limit=limit, include_debug=debug == "1"),
        }

    @app.get("/api/stats")
    async def api_stats() -> dict:
        from packetpro.stats import get_gpu_stats

        cfg = _try_load_config()
        if cfg is None:
            return {"configured": False, "gpu": get_gpu_stats()}
        return {"configured": True, **collect_stats(cfg)}

    @app.get("/api/settings/watch-folder-check")
    async def api_watch_folder_check(path: str = Query(default="")) -> dict:
        return check_watch_folder_access(path)

    @app.post("/api/watch/scan")
    async def api_watch_scan() -> dict:
        cfg = runtime_config()
        return request_watch_folder_scan(cfg)

    @app.get("/api/watch/watermark")
    async def api_watch_watermark_get() -> dict:
        cfg = runtime_config()
        if cfg.watch_folder is None:
            return {"configured": False, "message": "Watch folder is not configured"}
        return get_watch_watermark_status(cfg)

    @app.post("/api/watch/watermark")
    async def api_watch_watermark_set(payload: dict = Body(...)) -> dict:
        cfg = runtime_config()
        if cfg.watch_folder is None:
            raise HTTPException(status_code=400, detail="Watch folder is not configured")

        all_files = bool(payload.get("all_files")) or payload.get("mode") == "all"
        since = payload.get("since")
        days_back = payload.get("days_back")
        if days_back is not None:
            try:
                days_back = int(days_back)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="days_back must be an integer") from exc

        if not all_files and since is None and days_back is None:
            raise HTTPException(
                status_code=400,
                detail="Provide since, days_back, or all_files",
            )

        try:
            return set_watch_watermark(
                cfg,
                since=str(since) if since is not None else None,
                all_files=all_files,
                days_back=days_back,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/settings/ocr-backends")
    async def api_ocr_backends() -> dict:
        cfg = _try_load_config()
        if cfg is None:
            return {"configured": False}
        from packetpro.ocr import describe_ocr_backends

        return {"configured": True, **describe_ocr_backends(cfg.ocr)}

    @app.get("/api/settings/ollama-models")
    async def api_ollama_models() -> dict:
        from packetpro.ocr import list_ollama_models

        settings = get_paths_settings()
        ocr = settings.get("ocr", {})
        primary = list_ollama_models(str(ocr.get("primary_url", "http://127.0.0.1:11434")))
        secondary = list_ollama_models(str(ocr.get("secondary_url", "http://127.0.0.1:11435")))
        return {
            "configured": settings["configured"],
            "primary": primary,
            "secondary": secondary,
            "selected": {
                "primary_model": ocr.get("primary_model", ""),
                "secondary_model": ocr.get("secondary_model", ""),
            },
        }

    @app.post("/api/settings/ocr-engine")
    async def api_ocr_engine_set(payload: dict = Body(...)) -> dict:
        engine = str(payload.get("engine", "")).strip().lower()
        if not engine:
            raise HTTPException(status_code=400, detail="engine is required")

        try:
            cfg = save_ocr_engine_settings(engine=engine)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        from packetpro.ocr import describe_ocr_backends, reset_paddle_ocr

        reset_paddle_ocr()
        return {
            "ok": True,
            "engine": cfg.ocr.engine,
            "backends": describe_ocr_backends(cfg.ocr),
        }

    @app.post("/api/settings/ocr-model")
    async def api_ocr_model_set(payload: dict = Body(...)) -> dict:
        primary_model = str(payload.get("primary_model", "")).strip()
        if not primary_model:
            raise HTTPException(status_code=400, detail="primary_model is required")

        secondary_model = payload.get("secondary_model")
        try:
            cfg = save_ocr_model_settings(
                primary_model=primary_model,
                secondary_model=(
                    str(secondary_model).strip()
                    if secondary_model is not None and str(secondary_model).strip()
                    else None
                ),
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        from packetpro.ocr import describe_ocr_backends

        return {
            "ok": True,
            "primary_model": cfg.ocr.model,
            "secondary_model": cfg.ocr.secondary_model,
            "backends": describe_ocr_backends(cfg.ocr),
        }

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
                "exports": str(cfg.exports),
                "watch_folder": str(cfg.watch_folder) if cfg.watch_folder else "",
            }
        return {"settings": settings, "resolved_paths": resolved}

    @app.get("/api/export/text")
    async def api_export_text(q: str = Query(..., min_length=1)) -> dict:
        cfg = runtime_config()
        try:
            documents = _export_documents(cfg, q)
        except SearchQueryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not documents:
            raise HTTPException(status_code=404, detail="No documents matched the query")
        text = format_ai_export(q, documents)
        return {
            "query": q,
            "count": len(documents),
            "truncated": len(documents) >= EXPORT_SEARCH_LIMIT,
            "text": text,
        }

    @app.post("/api/export/pdf")
    async def api_export_pdf(payload: dict = Body(...)) -> dict:
        query = str(payload.get("query", "")).strip()
        if not query:
            raise HTTPException(status_code=400, detail="query is required")

        cfg = runtime_config()
        try:
            documents = _export_documents(cfg, query)
        except SearchQueryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not documents:
            raise HTTPException(status_code=404, detail="No documents matched the query")

        text = format_ai_export(query, documents)
        filename = export_filename(query)
        output_path = cfg.exports / filename
        write_export_pdf(output_path, text)

        return {
            "ok": True,
            "query": query,
            "count": len(documents),
            "truncated": len(documents) >= EXPORT_SEARCH_LIMIT,
            "filename": filename,
            "path": str(output_path),
            "download_url": f"/exports/{filename}",
        }

    @app.get("/exports/{filename}")
    async def download_export(filename: str) -> FileResponse:
        cfg = runtime_config()
        exports_root = cfg.exports.resolve()
        safe_name = Path(filename).name
        if safe_name != filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        file_path = (exports_root / safe_name).resolve()
        try:
            file_path.relative_to(exports_root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Invalid export path") from exc

        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="Export not found")

        return FileResponse(
            file_path,
            media_type="application/pdf",
            filename=safe_name,
        )

    @app.get("/api/search")
    async def api_search(
        q: str = Query(..., min_length=1),
        limit: str = Query(default="50"),
    ) -> dict:
        cfg = runtime_config()
        init_db(cfg.database)
        resolved_limit = resolve_search_limit(limit)
        try:
            results = search_documents(cfg.database, q, limit=resolved_limit)
        except SearchQueryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "query": q,
            "limit": limit,
            "count": len(results),
            "results": [
                {
                    "id": item.document.id,
                    "job_id": item.document.job_id,
                    "original_name": item.document.original_name,
                    "page_number": item.document.page_number,
                    "ocr_text": item.document.ocr_text,
                    "highlighted_text": item.snippet,
                    "processed_at": item.document.processed_at,
                    "image_url": f"/images/{item.document.id}",
                }
                for item in results
            ],
        }

    @app.delete("/api/documents/{doc_id}")
    async def api_delete_document(doc_id: int) -> dict:
        cfg = runtime_config()
        init_db(cfg.database)
        doc = get_document(cfg.database, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")

        archive_path = Path(doc.archive_path)
        if resolve_allowed_document_path(cfg, archive_path) is None:
            raise HTTPException(status_code=403, detail="Invalid document path")

        if not delete_document(cfg.database, doc_id):
            raise HTTPException(status_code=404, detail="Document not found")

        archive_deleted = False
        resolved_archive = archive_path.resolve()
        if (
            is_archive_source(cfg, resolved_archive)
            and count_documents_with_archive(cfg.database, doc.archive_path) == 0
            and resolved_archive.is_file()
        ):
            resolved_archive.unlink()
            archive_deleted = True

        return {
            "ok": True,
            "id": doc_id,
            "original_name": doc.original_name,
            "archive_deleted": archive_deleted,
        }

    @app.get("/images/{doc_id}")
    async def get_image(doc_id: int) -> FileResponse:
        cfg = runtime_config()
        doc = get_document(cfg.database, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")

        image_path = resolve_allowed_document_path(cfg, Path(doc.archive_path))
        if image_path is None:
            raise HTTPException(status_code=403, detail="Invalid document path")

        if not image_path.is_file():
            raise HTTPException(status_code=404, detail="Image file not found")

        if image_path.suffix.lower() == ".pdf":
            page = render_pdf_page(image_path, doc.page_number - 1, dpi=150)
            ok, encoded = cv2.imencode(".png", page)
            if not ok:
                raise HTTPException(status_code=500, detail="Failed to render PDF page")
            return Response(content=encoded.tobytes(), media_type="image/png")

        media_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        return FileResponse(
            image_path,
            media_type=media_type,
            content_disposition_type="inline",
        )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app