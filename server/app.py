"""Demo web server: serves the built React app and its precomputed data. No database, no keys."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Module scripts (the map worker is an .mjs file) must be served as JavaScript; Linux hosts may not know these
for ext, kind in ((".mjs", "text/javascript"), (".js", "text/javascript"), (".webp", "image/webp"),
                  (".geojson", "application/geo+json")):
    mimetypes.add_type(kind, ext)

# Build folder: MSP_WEB_DIST if set, else dist/ next to this file (deploy package), else web/dist in the repo
HERE = Path(__file__).resolve().parent
DIST = Path(os.environ.get("MSP_WEB_DIST") or (HERE / "dist" if (HERE / "dist").is_dir() else HERE.parent / "web" / "dist"))

app = FastAPI(title="Ships, radar and sea state off Norway", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.middleware("http")
async def cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/data/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif not path.startswith("/api/"):
        # the app page itself is always revalidated, so a new version is picked up at once
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="assets")
app.mount("/data", StaticFiles(directory=DIST / "data", check_dir=False), name="data")


@app.get("/{path:path}")
def spa(path: str) -> FileResponse:
    """Files from the build folder; every other path gets the app (client-side routing)."""
    f = (DIST / path).resolve()
    if path and f.is_file() and DIST.resolve() in f.parents:
        return FileResponse(f)
    return FileResponse(DIST / "index.html")
