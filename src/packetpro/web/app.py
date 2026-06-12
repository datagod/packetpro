"""FastAPI search UI for PacketPro."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from packetpro.config import AppConfig, load_config
from packetpro.db import get_document, init_db, search_documents
from packetpro.enhance import render_pdf_page

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or load_config()
    init_db(cfg.database)
    archive_root = cfg.archive.resolve()

    app = FastAPI(title="PacketPro", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, q: str = Query(default="")) -> HTMLResponse:
        results = search_documents(cfg.database, q) if q.strip() else []
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "query": q,
                "results": results,
                "result_count": len(results),
            },
        )

    @app.get("/api/search")
    async def api_search(q: str = Query(..., min_length=1)) -> dict:
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